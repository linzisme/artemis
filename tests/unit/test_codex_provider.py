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

"""Public provider contract; CLI doubles test transport, never live access."""

from artemis.llm.router import ModelEndpoint, ModelFactory, ModelProvider

import asyncio
import base64
import io
import json
import os
from pathlib import Path
import sys

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from PIL import Image
from pydantic import BaseModel

from artemis.llm.codex import CodexChatModel, CodexError


@pytest.fixture
def cli(tmp_path):
    """Executable boundary double, with independently inspectable input/output."""
    executable = tmp_path / "codex-double"
    config = tmp_path / "config.json"
    receipt = tmp_path / "receipt.json"
    executable.write_text(f"""#!{sys.executable}
import json, os, pathlib, sys, time
cfg=json.loads(pathlib.Path({str(config)!r}).read_text())
if sys.argv[1:] == ["login", "status"]:
    print(cfg.get("login", "Logged in using ChatGPT"))
    sys.exit(0)
prompt=sys.stdin.read()
args=sys.argv[1:]
images=[pathlib.Path(args[i+1]) for i,v in enumerate(args) if v == "--image"]
pathlib.Path({str(receipt)!r}).write_text(json.dumps({{
    "prompt":prompt,"cwd":os.getcwd(),"pid":os.getpid(),"args":args,
    "api_key_present":"OPENAI_API_KEY" in os.environ or "CODEX_API_KEY" in os.environ,
    "images":[{{"bytes":list(p.read_bytes()),"mode":p.stat().st_mode & 511}} for p in images]
}}))
time.sleep(cfg.get("sleep",0))
if cfg.get("error"):
    print(json.dumps({{"type":"turn.failed","error":{{"message":cfg["error"]}}}}))
    sys.exit(1)
pathlib.Path(args[args.index("--output-last-message")+1]).write_text(json.dumps(cfg["response"]))
print(json.dumps({{"type":"turn.completed","usage":{{"input_tokens":23,"output_tokens":7}}}}))
""")
    executable.chmod(0o700)

    def configure(response=None, **kwargs):
        config.write_text(
            json.dumps({"response": response or {"content": "ready", "tool_calls": []}, **kwargs})
        )
        return CodexChatModel(
            model_name="test-model", executable=str(executable), timeout_seconds=2
        )

    return configure, receipt


@pytest.mark.asyncio
async def test_text_call_has_usage_no_keys_and_cleans_temporary_files(cli, monkeypatch):
    configure, receipt = cli
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-cli")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-reach-cli")
    result = await configure().ainvoke("hello")
    assert result.content == "ready"
    assert result.usage_metadata["total_tokens"] == 30
    recorded = json.loads(receipt.read_text())
    assert not recorded["api_key_present"]
    assert not Path(recorded["cwd"]).exists()
    assert "--ignore-user-config" in recorded["args"]


@pytest.mark.asyncio
async def test_real_action_declarations_and_tool_result_history(cli):
    from artemis.mcp.action_specs import operator_shell_tool

    configure, receipt = cli
    model = configure(
        {
            "content": "",
            "tool_calls": [
                {"name": "click", "arguments_json": '{"target": 2}'},
                {"name": "press_key", "arguments_json": '{"key": "BACK"}'},
            ],
        }
    )
    bound = model.bind_tools([operator_shell_tool("click"), operator_shell_tool("press_key")])
    result = await bound.ainvoke("Click item 2, then wait one second.")
    assert [c["name"] for c in result.tool_calls] == ["click", "press_key"]
    assert len({c["id"] for c in result.tool_calls}) == 2
    configure()
    await bound.ainvoke(
        [
            HumanMessage(content="task"),
            result,
            ToolMessage(content="clicked", tool_call_id=result.tool_calls[0]["id"]),
        ]
    )
    recorded = json.loads(receipt.read_text())["prompt"]
    assert "clicked" in recorded and result.tool_calls[0]["id"] in recorded


@pytest.mark.asyncio
async def test_invalid_action_arguments_rejected_before_execution(cli):
    from artemis.mcp.action_specs import operator_shell_tool

    configure, _ = cli
    model = configure(
        {"content": "", "tool_calls": [{"name": "press_key", "arguments_json": '{"key":"WRONG"}'}]}
    )
    with pytest.raises(CodexError, match="schema validation"):
        await model.bind_tools([operator_shell_tool("press_key")]).ainvoke("press_key")


@pytest.mark.asyncio
async def test_images_preserve_order_and_cleanup(cli):
    configure, receipt = cli
    blocks = []
    for colour in ("red", "blue"):
        buffer = io.BytesIO()
        Image.new("RGB", (16, 16), colour).save(buffer, format="PNG")
        blocks.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
                },
            }
        )
    await configure().ainvoke([HumanMessage(content=blocks)])
    recorded = json.loads(receipt.read_text())
    colours = [
        Image.open(io.BytesIO(bytes(v["bytes"]))).getpixel((0, 0)) for v in recorded["images"]
    ]
    assert colours == [(255, 0, 0), (0, 0, 255)]
    assert all(v["mode"] == 0o600 for v in recorded["images"])
    assert not Path(recorded["cwd"]).exists()


@pytest.mark.asyncio
async def test_structured_output_validates_domain_schema(cli):
    class Verdict(BaseModel):
        passed: bool

    configure, _ = cli
    result = (
        await configure({"result_json": '{"passed":true}'})
        .with_structured_output(Verdict)
        .ainvoke("check")
    )
    assert result.passed is True
    with pytest.raises(CodexError, match="schema validation"):
        await (
            configure({"result_json": '{"passed":"wrong"}'})
            .with_structured_output(Verdict)
            .ainvoke("check")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", ["quota exceeded SECRET", "authentication failed SECRET", "unsupported model SECRET"]
)
async def test_terminal_errors_never_leak_or_fallback(cli, error):
    configure, _ = cli
    with pytest.raises(CodexError) as caught:
        await configure(error=error).ainvoke("test")
    assert "SECRET" not in str(caught.value)
    assert not caught.value.failure.should_fallback
    assert not caught.value.failure.retryable


@pytest.mark.asyncio
async def test_api_login_is_rejected_without_generation(cli):
    configure, receipt = cli
    with pytest.raises(CodexError, match="existing ChatGPT login"):
        await configure(login="Logged in using an API key: SECRET").ainvoke("test")
    assert not receipt.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancel_kill_process_and_remove_files(cli, cancel):
    configure, receipt = cli
    model = configure(sleep=30)
    model.timeout_seconds = 0.3
    task = asyncio.create_task(model.ainvoke("test"))
    for _ in range(100):
        if receipt.exists():
            break
        await asyncio.sleep(0.01)
    assert receipt.exists()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else CodexError):
        await task
    recorded = json.loads(receipt.read_text())
    assert not Path(recorded["cwd"]).exists()
    with pytest.raises(ProcessLookupError):
        os.kill(recorded["pid"], 0)


def test_remote_schema_is_rejected(cli):
    configure, _ = cli
    with pytest.raises(CodexError, match="Remote schema"):
        configure().with_structured_output({"$ref": "https://example.invalid/schema"})


def test_subscription_config_routes_all_roles_without_api_fallback():
    from artemis.config.llm import LLMConfig, _expand_default_into_nodes

    config = LLMConfig.model_validate(
        _expand_default_into_nodes(
            {
                "default": {
                    "provider": "codex",
                    "model": "gpt-6.1-sol",
                    "fallback": {"provider": "codex", "model": "gpt-6.1-sol"},
                }
            }
        )
    )
    for role in ("operator", "summarizer", "planner_validation", "validator_pixel_safety_net"):
        assert config.get_agent(role).provider == "codex"
    with pytest.raises(ValueError, match="API fallback"):
        from third_party.mobile_use.config.llm import LLMWithFallback

        LLMWithFallback(
            provider="codex", model="test", fallback={"provider": "openai", "model": "test"}
        )


def test_codex_factory_needs_no_api_key(monkeypatch):
    monkeypatch.delenv("ARTEMIS_FAKE_LLM", raising=False)
    model = ModelFactory.create_model(
        ModelEndpoint(provider=ModelProvider.from_string("codex"), model_name="gpt-6.1-sol")
    )
    assert model._llm_type == "codex-chatgpt"
    assert model.model_name == "gpt-6.1-sol"


@pytest.mark.asyncio
@pytest.mark.parametrize("use_fallback", [False, True])
async def test_subscription_profile_preserves_fast_medium_through_cli(cli, use_fallback):
    from types import SimpleNamespace

    from artemis.config.llm import LLMConfig, _expand_default_into_nodes
    from artemis.services.llm import _resolve_endpoint
    from third_party.mobile_use.utils.file import load_jsonc

    profile = Path(__file__).resolve().parents[2] / "config" / "codex-chatgpt.jsonc"
    with profile.open() as stream:
        config = LLMConfig.model_validate(_expand_default_into_nodes(load_jsonc(stream)))
    endpoint = _resolve_endpoint(
        SimpleNamespace(llm_config=config), "planner", use_fallback=use_fallback
    )
    assert endpoint.cache_key() != endpoint.model_copy(update={"service_tier": None}).cache_key()
    model = ModelFactory.create_model(endpoint)
    configure, receipt = cli
    model.executable = configure().executable
    result = await model.ainvoke("hello")
    args = json.loads(receipt.read_text())["args"]
    assert args[args.index("--model") + 1] == "gpt-6.1-sol"
    assert 'model_reasoning_effort="medium"' in args
    assert 'service_tier="fast"' in args
    assert 'forced_login_method="chatgpt"' in args
    assert result.response_metadata["requested_service_tier"] == "fast"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        [{"type": "image_url", "image_url": {"url": "https://example.invalid/private.png"}}],
        [{"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}}],
        [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}],
    ],
)
async def test_unsupported_media_fails_before_generation(cli, content):
    configure, receipt = cli
    with pytest.raises(CodexError):
        await configure().ainvoke([HumanMessage(content=content)])
    assert not receipt.exists()


@pytest.mark.asyncio
async def test_unknown_tool_and_required_choice_fail_closed(cli):
    from artemis.mcp.action_specs import operator_shell_tool

    configure, _ = cli
    with pytest.raises(CodexError):
        await (
            configure({"content": "", "tool_calls": [{"name": "unbound", "arguments_json": "{}"}]})
            .bind_tools([operator_shell_tool("press_key")])
            .ainvoke("test")
        )
    with pytest.raises(CodexError, match="required"):
        await (
            configure()
            .bind_tools([operator_shell_tool("press_key")], tool_choice="required")
            .ainvoke("test")
        )


def test_example_profile_memory_stays_on_subscription(monkeypatch):
    from types import SimpleNamespace
    from artemis.config.llm import get_default_llm_config
    from artemis.services.llm import get_memory_llm
    from artemis.agents.flash.summarizer import VisualStepSummarizer
    from artemis.memory.chunking import StepCapsuleLens

    config_path = Path(__file__).resolve().parents[2] / "config" / "codex-chatgpt.jsonc"
    monkeypatch.setenv("ARTEMIS_ARTEMIS_JSONC", str(config_path))
    ctx = SimpleNamespace(llm_config=get_default_llm_config(), data_engine=None)
    assert get_memory_llm(ctx, "gpt-6.1-sol")._llm_type == "codex-chatgpt"
    assert VisualStepSummarizer(ctx, "gpt-6.1-sol")._llm._llm_type == "codex-chatgpt"
    assert StepCapsuleLens("gpt-6.1-sol", ctx=ctx)._get_llm()._llm_type == "codex-chatgpt"
    lazy_ctx = SimpleNamespace(llm_config=None, data_engine=None)
    assert VisualStepSummarizer(lazy_ctx)._llm._llm_type == "codex-chatgpt"
    assert (
        VisualStepSummarizer(SimpleNamespace(llm_config=None), "gpt-6.1-sol")._llm._llm_type
        == "codex-chatgpt"
    )


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
def test_api_profiles_keep_legacy_google_memory(provider, monkeypatch):
    from types import SimpleNamespace
    from pydantic import SecretStr
    from artemis.config.settings import settings
    from artemis.services.llm import get_memory_llm
    from langchain_google_genai import ChatGoogleGenerativeAI

    # Constructor only: this inert key is never sent to any service.
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("unit-test-no-network"))
    ctx = SimpleNamespace(llm_config=SimpleNamespace(summarizer=SimpleNamespace(provider=provider)))
    model = get_memory_llm(ctx, "gemini-3.8-flash")
    assert isinstance(model, ChatGoogleGenerativeAI)
