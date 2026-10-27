# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ChatGPT subscription adapter using the official Codex CLI, never auth files.

Calls carry their full history. ARTEMIS executes all declared tools; Codex emits
validated JSON envelopes. A session's empty execution directory stays stable while
per-call schemas, images and results are ephemeral, including concurrent calls.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
from contextlib import contextmanager
from contextvars import ContextVar
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
from typing import Any, Literal
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError as SchemaValidationError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool
from PIL import Image
from pydantic import BaseModel, Field, PrivateAttr, ValidationError

from artemis.llm.reliability import Failure, FailureCategory, LLMPermanentError


class CodexError(LLMPermanentError):
    """Terminal: surface login, quota and protocol problems without API fallback."""

    def __init__(self, message: str, category=FailureCategory.BAD_REQUEST):
        super().__init__(message, failure=Failure(category, False, False))


def _environment() -> dict[str, str]:
    # Preserve CLI-managed auth location and network transport, not provider keys
    # or inherited Codex session/daemon settings. Never open/copy auth.json.
    allowed = {
        "HOME",
        "PATH",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "CODEX_HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "CODEX_CA_CERTIFICATE",
        "SYSTEMROOT",
        "WINDIR",
    }
    return {**{k: v for k, v in os.environ.items() if k in allowed}, "RUST_LOG": "error"}


def check_codex_login(executable: str = "codex") -> None:
    """Read status via CLI; do not expose its raw output (API status can contain a key)."""
    try:
        result = subprocess.run(
            [executable, "login", "status"],
            capture_output=True,
            timeout=15,
            env=_environment(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CodexError(
            "Codex CLI unavailable; install the official CLI and run codex login."
        ) from None
    if result.returncode or b"Logged in using ChatGPT" not in result.stdout + result.stderr:
        raise CodexError(
            "Codex requires an existing ChatGPT login. Run codex login yourself; API-key login is refused.",
            FailureCategory.AUTHENTICATION,
        )


def _validator(schema: dict) -> Draft202012Validator:
    def check_refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef"} and not str(child).startswith("#"):
                    raise CodexError("Remote schema references are not supported.")
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)

    check_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise CodexError("Invalid JSON Schema.") from None
    return Draft202012Validator(schema)


def _object_schema(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


class _CodexExecutionContext:
    """Allocate an empty directory only when this session actually uses Codex."""

    def __init__(self):
        self._directory: tempfile.TemporaryDirectory | None = None

    @property
    def directory(self) -> Path:
        if self._directory is None:
            self._directory = tempfile.TemporaryDirectory(prefix="artemis-codex-context-")
        return Path(self._directory.name)

    def close(self) -> None:
        if self._directory is not None:
            self._directory.cleanup()


_CODEX_EXECUTION_CONTEXT: ContextVar[_CodexExecutionContext | None] = ContextVar(
    "codex_execution_context", default=None
)


@contextmanager
def codex_session_context():
    """Scope CLI identity to a task run, including inherited background tasks."""
    context = _CodexExecutionContext()
    token = _CODEX_EXECUTION_CONTEXT.set(context)
    try:
        yield
    finally:
        _CODEX_EXECUTION_CONTEXT.reset(token)
        context.close()


class CodexChatModel(BaseChatModel):
    """Non-streaming CLI model with asynchronous cancellation and tool validation.

    Requires the flags shipped in Codex CLI 0.161.0. Unsupported flags fail closed.
    Model availability belongs to the user's subscription, not an API catalog.
    """

    model_name: str
    timeout_seconds: float = Field(default=180, gt=0)
    reasoning_effort: str | None = None
    service_tier: Literal["fast", "priority"] | None = None
    executable: str = "codex"
    disable_streaming: bool | Literal["tool_calling"] = True
    # Standalone adapter calls have no ARTEMIS session; retain their model-local
    # context. Session invocations always use the task-owned context instead.
    _execution_directory: tempfile.TemporaryDirectory = PrivateAttr(
        default_factory=lambda: tempfile.TemporaryDirectory(prefix="artemis-codex-context-")
    )

    @property
    def _llm_type(self) -> str:
        return "codex-chatgpt"

    @property
    def _identifying_params(self) -> dict:
        return {
            "model_name": self.model_name,
            "provider": "codex",
            "reasoning_effort": self.reasoning_effort,
            "service_tier": self.service_tier,
        }

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        if kwargs:
            raise CodexError("Unsupported Codex tool binding options.")
        declarations = [copy.deepcopy(convert_to_openai_tool(t)["function"]) for t in tools]
        names = [t["name"] for t in declarations]
        if len(names) != len(set(names)):
            raise CodexError("Duplicate tool names.")
        for tool in declarations:
            schema = tool.setdefault("parameters", _object_schema({}))
            schema.setdefault("additionalProperties", False)
            _validator(schema)
        if isinstance(tool_choice, dict):
            tool_choice = tool_choice.get("function", {}).get("name")
        if tool_choice is True:
            tool_choice = "required"
        if tool_choice not in (None, "auto", "none", "required", "any", *names):
            raise CodexError("Unsupported tool_choice.")
        if tool_choice in ("required", "any") and not names:
            raise CodexError("Required tool choice needs at least one tool.")
        return self.bind(codex_tools=declarations, codex_tool_choice=tool_choice)

    def with_structured_output(self, schema=None, *, include_raw=False, **kwargs):
        method = kwargs.pop("method", "json_schema")
        if kwargs or method not in {"json_schema", "function_calling"} or schema is None:
            raise CodexError("Codex structured output requires a schema; json_mode is unsupported.")
        pydantic_schema = (
            schema if isinstance(schema, type) and issubclass(schema, BaseModel) else None
        )
        json_schema = pydantic_schema.model_json_schema() if pydantic_schema else schema
        if not isinstance(json_schema, dict):
            raise CodexError("Structured output requires a Pydantic model or JSON Schema object.")
        if isinstance(json_schema, dict) and "function" in json_schema:
            json_schema = json_schema["function"]["parameters"]
        validator = _validator(json_schema)

        def parse(message):
            try:
                value = json.loads(message.content)
                validator.validate(value)
                parsed = pydantic_schema.model_validate(value) if pydantic_schema else value
            except (ValueError, TypeError, SchemaValidationError, ValidationError):
                error = CodexError("Codex structured response failed schema validation.")
                if include_raw:
                    return {"raw": message, "parsed": None, "parsing_error": error}
                raise error from None
            return (
                {"raw": message, "parsed": parsed, "parsing_error": None} if include_raw else parsed
            )

        return self.bind(codex_output_schema=json_schema) | RunnableLambda(parse)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._agenerate(messages, stop=stop, **kwargs))
        raise CodexError("Use ainvoke inside an event loop.")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if stop:
            raise CodexError("Codex CLI does not support stop sequences.")
        tools = kwargs.pop("codex_tools", [])
        choice = kwargs.pop("codex_tool_choice", None)
        output_schema = kwargs.pop("codex_output_schema", None)
        if kwargs:
            raise CodexError("Unsupported Codex generation options.")
        await asyncio.to_thread(check_codex_login, self.executable)
        with tempfile.TemporaryDirectory(prefix="artemis-codex-") as directory:
            root = Path(directory)
            transcript, images = self._transcript(messages, root)
            if output_schema is not None:
                envelope = _object_schema({"result_json": {"type": "string"}})
            else:
                call_schema = _object_schema(
                    {
                        "name": {
                            "type": "string",
                            **({"enum": [t["name"] for t in tools]} if tools else {}),
                        },
                        "arguments_json": {"type": "string"},
                    }
                )
                envelope = _object_schema(
                    {
                        "content": {"type": "string"},
                        "tool_calls": {"type": "array", "items": call_schema},
                    }
                )
            schema_path = root / "response.schema.json"
            schema_path.write_text(json.dumps(envelope), encoding="utf-8")
            result_path = root / "response.json"
            prompt = (
                "You are the inference backend for ARTEMIS mobile testing. Continue the supplied "
                "conversation as its assistant. Respect the supplied system instructions. "
                "Do not use Codex tools, inspect files, browse, or execute any actions. "
                "ARTEMIS executes declared tools after your response. Never invent tool results. "
                "Images are attached in the order of [image N] markers in the transcript. "
                "Output the required JSON envelope. For tool calls, arguments_json is a JSON object "
                "encoded as a string and must satisfy the tool's parameters schema. "
                "Use an empty tool_calls array for a final answer. "
                "For structured output, result_json is the JSON value encoded as a string, "
                "conforming exactly to output_schema.\n"
                + json.dumps(
                    {
                        "messages": transcript,
                        "tools": tools,
                        "tool_choice": choice or "auto",
                        "output_schema": output_schema,
                    },
                    ensure_ascii=False,
                )
            )
            session_context = _CODEX_EXECUTION_CONTEXT.get()
            execution_root = (
                session_context.directory
                if session_context is not None
                else Path(self._execution_directory.name)
            )
            args = self._command(execution_root, schema_path, result_path, images)
            events = await self._execute(args, prompt, execution_root)
            try:
                value = json.loads(result_path.read_text(encoding="utf-8"))
                _validator(envelope).validate(value)
            except (ValueError, OSError, SchemaValidationError):
                raise CodexError("Codex did not return a valid final response envelope.") from None
            usage = events.get("usage", {})
            input_tokens, output_tokens = (
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
            )
            calls = []
            if output_schema is not None:
                content = value["result_json"]
            else:
                content = value["content"]
                by_name = {t["name"]: t for t in tools}
                for call in value["tool_calls"]:
                    name = call["name"]
                    if name not in by_name or choice == "none":
                        raise CodexError("Codex returned an unbound or forbidden tool.")
                    if choice in by_name and name != choice:
                        raise CodexError("Codex violated the requested tool choice.")
                    try:
                        arguments = json.loads(call["arguments_json"])
                        _validator(by_name[name]["parameters"]).validate(arguments)
                        if not isinstance(arguments, dict):
                            raise ValueError()
                    except (ValueError, TypeError, SchemaValidationError):
                        raise CodexError("Codex tool arguments failed schema validation.") from None
                    calls.append({"name": name, "args": arguments, "id": f"codex_{uuid4().hex}"})
                if (choice in ("required", "any") or choice in by_name) and not calls:
                    raise CodexError("Codex omitted the required tool call.")
            message = AIMessage(
                content=content,
                tool_calls=calls,
                response_metadata={
                    "model_name": self.model_name,
                    "provider": "codex",
                    "auth": "chatgpt",
                    "reasoning_effort": self.reasoning_effort,
                    "requested_service_tier": self.service_tier,
                },
                usage_metadata={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "input_token_details": {"cache_read": usage.get("cached_input_tokens", 0)},
                },
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

    def _transcript(self, messages: list[BaseMessage], root: Path):
        transcript, images = [], []
        total_bytes = 0
        for message in messages:
            blocks = message.content if isinstance(message.content, list) else [message.content]
            content = []
            for block in blocks:
                if isinstance(block, str):
                    content.append(block)
                elif block.get("type") == "text":
                    content.append(block["text"])
                elif block.get("type") == "image_url":
                    url = block["image_url"]
                    url = url if isinstance(url, str) else url.get("url", "")
                    match = re.fullmatch(
                        r"data:image/(png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=\s]+)", url
                    )
                    if not match or len(url) > 28_000_000:
                        raise CodexError("Codex accepts only bounded inline PNG/JPEG/WebP images.")
                    try:
                        data = base64.b64decode(re.sub(r"\s", "", match[2]), validate=True)
                        total_bytes += len(data)
                        if len(images) >= 32 or total_bytes > 40_000_000:
                            raise ValueError()
                        with Image.open(io.BytesIO(data)) as image:
                            if (
                                image.format not in {"PNG", "JPEG", "WEBP"}
                                or image.width * image.height > 25_000_000
                            ):
                                raise ValueError()
                            image.verify()
                    except (ValueError, OSError, binascii.Error, Image.DecompressionBombError):
                        raise CodexError("Invalid image or image budget exceeded.") from None
                    path = root / f"image-{len(images) + 1}.{match[1]}"
                    path.write_bytes(data)
                    path.chmod(0o600)
                    images.append(path)
                    content.append(f"[image {len(images)}]")
                else:
                    raise CodexError(
                        "Unsupported content block; native video/audio/files are not supported."
                    )
            entry = {"role": message.type, "content": content}
            if isinstance(message, AIMessage) and message.tool_calls:
                entry["tool_calls"] = message.tool_calls
            if isinstance(message, ToolMessage):
                entry["tool_call_id"] = message.tool_call_id
                entry["status"] = message.status
            if message.name:
                entry["name"] = message.name
            transcript.append(entry)
        return transcript, images

    def _command(self, root, schema, result, images):
        args = [
            self.executable,
            "--no-daemon",
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--cd",
            str(root),
            "--model",
            self.model_name,
            "--json",
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(result),
        ]
        config = {
            "forced_login_method": "chatgpt",
            "model_provider": "openai",
            "approval_policy": "never",
            "web_search": "disabled",
            "project_doc_max_bytes": 0,
            "history.persistence": "none",
            "suppress_unstable_features_warning": True,
        }
        # User config is not loaded, so user MCP servers and hooks are not imported.
        # Read-only alone does NOT disable tools; explicitly turn capabilities off.
        for feature in (
            "shell_tool",
            "unified_exec",
            "shell_snapshot",
            "plugins",
            "apps",
            "hooks",
            "multi_agent",
            "multi_agent_v2",
            "browser_use",
            "browser_use_external",
            "computer_use",
            "image_generation",
            "memories",
            "skill_search",
            "view_image",
            "code_mode",
            "sleep_tool",
            "tool_suggest",
        ):
            config[f"features.{feature}"] = False
        config["features.skip_host_skill_discovery"] = True
        if self.reasoning_effort:
            config["model_reasoning_effort"] = self.reasoning_effort
        if self.service_tier:
            config["service_tier"] = self.service_tier
        for key, value in config.items():
            args.extend(["-c", f"{key}={json.dumps(value)}"])
        for path in images:
            args.extend(["--image", str(path)])
        return [*args, "-"]

    async def _execute(self, args, prompt, root):
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=root,
                env=_environment(),
                start_new_session=os.name == "posix",
            )
        except OSError:
            raise CodexError("Cannot start Codex CLI.") from None
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")), timeout=self.timeout_seconds
            )
        except (asyncio.CancelledError, TimeoutError) as error:
            if process.returncode is None:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
            await process.communicate()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise CodexError(
                "Codex invocation timed out; process stopped.", FailureCategory.TIMEOUT
            ) from None
        completed = None
        failed = process.returncode != 0
        try:
            for line in stdout.splitlines():
                event = json.loads(line)
                if event.get("type") in {"turn.failed", "error"}:
                    failed = True
                if event.get("type") == "turn.completed":
                    completed = event
                item = event.get("item", {})
                if item and item.get("type") not in {"agent_message", "reasoning", "error"}:
                    raise CodexError(
                        "Codex attempted an unexpected built-in tool; result rejected."
                    )
        except (ValueError, TypeError):
            raise CodexError("Invalid Codex JSONL protocol.") from None
        if failed or completed is None:
            # Never include raw CLI stderr/events: they may contain request data.
            error_text = (stdout + stderr).decode("utf-8", errors="replace").lower()
            if any(s in error_text for s in ("usage limit", "quota", "rate limit", "429")):
                raise CodexError(
                    "Codex subscription limit reached; no API fallback.", FailureCategory.RATE_LIMIT
                )
            if any(s in error_text for s in ("unauthorized", "401", "login", "authentication")):
                raise CodexError(
                    "Codex ChatGPT authentication failed; log in manually.",
                    FailureCategory.AUTHENTICATION,
                )
            raise CodexError(
                "Codex invocation failed; check CLI version, model availability and connectivity. No API fallback."
            )
        return completed
