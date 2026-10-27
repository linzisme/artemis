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

"""Explicit real subscription probe against synthetic images; no device access.

Run from checkout: PYTHONPATH=. python scripts/probe_codex_subscription.py --live
Requires existing CLI ChatGPT login. Makes several real model calls.
Use --cache-only to report the related-call cache check independently of optional
parallel-tool support. Zero observed hits are reported explicitly as unsupported.
"""

import argparse
import asyncio
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage
from PIL import Image, ImageDraw

from artemis.agents.checker.checker import CheckReport
from artemis.agents.explorer.explorer import Explorer
from artemis.config.llm import LLMConfig, _expand_default_into_nodes
from artemis.llm.codex import CodexChatModel
from artemis.llm.router import ModelEndpoint, ModelFactory, ModelProvider
from artemis.mcp.action_specs import operator_shell_tool
from artemis.services.llm import RobustChatModelWrapper
from scripts.probe_magpie_responses import check_result
from scripts.probe_tools import probe_sequential_tools


async def probe_related_calls(model: CodexChatModel, *, report=None):
    """Measure reported cache usage on appended history; never infer hits from reuse."""
    prefix = (
        "Synthetic cache fixture. Do not execute tools or actions. "
        "For each request reply only CACHE_PROBE_OK. Reference records follow:\n"
        + "\n".join(
            f"Record {index:04d}: synthetic blue square; centre 500,500; "
            "dry-run receipt accepted; no device action performed."
            for index in range(256)
        )
    )
    messages = [SystemMessage(content=prefix), HumanMessage(content="Acknowledge record 0000.")]
    report = report if report is not None else {}
    calls = report.setdefault("calls", [])
    for turn in range(3):
        start = time.monotonic()
        response = await model.ainvoke(messages)
        usage = response.usage_metadata or {}
        cached = usage.get("input_token_details", {}).get("cache_read", 0)
        calls.append(
            {
                "seconds": round(time.monotonic() - start, 2),
                "usage": response.usage_metadata,
                "valid": 0 <= cached <= usage.get("input_tokens", 0),
            }
        )
        messages.extend(
            [response, HumanMessage(content=f"Acknowledge record {turn + 1:04d} as well.")]
        )
    later_cached = [
        (call["usage"] or {}).get("input_token_details", {}).get("cache_read", 0)
        for call in calls[1:]
    ]
    valid = all(call["valid"] for call in calls)
    hit = valid and any(count > 0 for count in later_cached)
    report.update(
        {
            "status": "failed" if not valid else "passed" if hit else "unsupported",
            "passed": hit,
            "reason": (
                "Invalid cache usage; required probe behavior failed."
                if not valid
                else "Cached input reported on appended-history calls."
                if hit
                else "Cache reuse was not observed on later calls; caching is unsupported for this "
                "probe run. This does not establish that the provider never caches."
            ),
            "later_cached_input_tokens": later_cached,
            "calls": calls,
        }
    )
    return report


CODEX_REQUIRED_CHECKS = frozenset(
    {
        "related_call_cache",
        "text",
        "vision",
        "sequential_first_tool",
        "sequential_second_tool",
        "tool_result_continuation",
        "checker_schema",
        "explorer_universal_loop",
    }
)


async def probe(
    model_name,
    reasoning_effort="medium",
    service_tier="fast",
    *,
    cache_only=False,
    output: Path | None = None,
):
    result = {}
    probe_report = {
        "provider": "codex",
        "protocol": "codex exec JSON envelope",
        "endpoint": "CLI-managed ChatGPT subscription",
        "model": model_name,
        "reasoning_effort": reasoning_effort,
        "requested_service_tier": service_tier,
        "auth": "chatgpt",
        "device_actions_executed": False,
        "checks": result,
        "completed": False,
    }
    expected = {"related_call_cache"} if cache_only else CODEX_REQUIRED_CHECKS | {"tool_calls"}
    try:
        model = ModelFactory.create_model(
            ModelEndpoint(
                provider=ModelProvider.CODEX,
                model_name=model_name,
                timeout_seconds=180,
                reasoning_effort=reasoning_effort,
                service_tier=service_tier,
            )
        )
        assert isinstance(model, CodexChatModel), "Fake models must not be used in the live probe"
        cache_report = result["related_call_cache"] = {}
        try:
            await probe_related_calls(model, report=cache_report)
        except (Exception, asyncio.CancelledError) as exc:
            cache_report.update(passed=False, status="failed", error_type=type(exc).__name__)
            raise
        if cache_only:
            probe_report["mode"] = "cache-only"
            probe_report["completed"] = True
            return probe_report

        async def measure(name, call, validate, *, fatal=True):
            return await check_result(probe_report, name, call, validate, fatal=fatal)

        text = await measure(
            "text",
            model.ainvoke("Reply exactly ARTEMIS_CODEX_OK."),
            lambda r: r.content.strip() == "ARTEMIS_CODEX_OK",
        )
        result["text"]["usage"] = text.usage_metadata
        result["text"]["requested_parameters"] = text.response_metadata

        image = Image.new("RGB", (400, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 100, 300, 300), fill="blue")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image_block = {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
            },
        }
        await measure(
            "vision",
            model.ainvoke(
                [
                    HumanMessage(
                        content=[
                            {
                                "type": "text",
                                "text": "What colour is the filled square? Answer one lowercase word.",
                            },
                            image_block,
                        ]
                    )
                ]
            ),
            lambda r: r.content.strip().lower() == "blue",
        )

        tools = [operator_shell_tool("click"), operator_shell_tool("press_key")]
        bound = RobustChatModelWrapper(model).bind_tools(tools)
        messages = [
            SystemMessage(
                content="You are testing action serialization. Return requested tools; do not execute them."
            ),
            HumanMessage(
                content="Return exactly two tool calls in this order: click target index 2; press_key BACK."
            ),
        ]
        await measure(
            "tool_calls",
            bound.ainvoke(messages),
            lambda r: (
                [c["name"] for c in r.tool_calls] == ["click", "press_key"]
                and r.tool_calls[0]["args"]["target"] == 2
                and r.tool_calls[1]["args"]["key"] == "BACK"
            ),
            fatal=False,
        )
        await probe_sequential_tools(
            bound,
            measure,
            continuation_name="tool_result_continuation",
            text_content=lambda response: response.content.strip(),
        )
        await measure(
            "checker_schema",
            RobustChatModelWrapper(model)
            .with_structured_output(CheckReport)
            .ainvoke(
                "Return one assert verdict: item_text 'blue square', status passed, evidence 'synthetic image is blue', suggestion empty. No unmet subgoals."
            ),
            lambda r: (
                len(r.verdicts) == 1 and r.verdicts[0].status == "passed" and not r.unmet_subgoals
            ),
        )

        config = LLMConfig.model_validate(
            _expand_default_into_nodes(
                {
                    "default": {
                        "provider": "codex",
                        "model": model_name,
                        "timeout": 180,
                        "reasoning_effort": reasoning_effort,
                        "service_tier": service_tier,
                        "fallback": {
                            "provider": "codex",
                            "model": model_name,
                            "reasoning_effort": reasoning_effort,
                            "service_tier": service_tier,
                        },
                    }
                }
            )
        )
        context = SimpleNamespace(llm_config=config, data_engine=None)
        context.agent_config = SimpleNamespace(
            denylisted_tools={
                "explorer": [
                    "ask_perception_tool",
                    "detect_objects",
                    "get_ocr_list",
                    "ask_image_processor",
                    "inspect_region",
                ]
            }
        )
        explorer = Explorer(context)
        with tempfile.TemporaryDirectory(prefix="artemis-probe-") as directory:
            screenshot = Path(directory) / "synthetic.png"
            image.save(screenshot)
            answer = await measure(
                "explorer_universal_loop",
                explorer.run(
                    query="Find the centre of the blue square.",
                    context_feedback="",
                    screenshot_path=str(screenshot),
                    state={},
                    minimal_list="",
                    version="pro",
                ),
                lambda r: (
                    bool(json.loads(r)["candidates"])
                    and all(abs(v - 500) <= 50 for v in json.loads(r)["candidates"][0]["coords"])
                ),
            )
        candidates = json.loads(answer)["candidates"]
        result["explorer_universal_loop"]["coords"] = candidates[0]["coords"]
        probe_report["completed"] = True
        return probe_report
    finally:
        for name in sorted(expected - result.keys()):
            result[name] = {"passed": False, "status": "unexecuted"}
        probe_report["required_checks_passed"] = probe_report["completed"] and all(
            row["passed"] or (name == "related_call_cache" and row["status"] == "unsupported")
            for name, row in result.items()
            if name != "tool_calls"
        )
        probe_report["all_checks_passed"] = probe_report["completed"] and all(
            row["passed"] for row in result.values()
        )
        if output is not None:
            output.write_text(json.dumps(probe_report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow real subscription calls")
    parser.add_argument(
        "--cache-only", action="store_true", help="Only probe related-call cache usage"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("codex-probe.json"),
        help="Persist observations even on failure or timeout",
    )
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--service-tier", choices=("fast", "priority"), default="fast")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly allow subscription calls")
    try:
        result = asyncio.run(
            asyncio.wait_for(
                probe(
                    args.model,
                    args.reasoning_effort,
                    args.service_tier,
                    cache_only=args.cache_only,
                    output=args.output,
                ),
                timeout=1200,
            )
        )
    except (Exception, KeyboardInterrupt) as exc:
        if args.output.exists():
            print(args.output.read_text(), end="")
        print(f"Probe failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["required_checks_passed"] else 1)
