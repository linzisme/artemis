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
import tempfile
import time
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from PIL import Image, ImageDraw

from artemis.agents.checker.checker import CheckReport
from artemis.agents.explorer.explorer import Explorer
from artemis.config.llm import LLMConfig, _expand_default_into_nodes
from artemis.llm.codex import CodexChatModel
from artemis.llm.router import ModelEndpoint, ModelFactory, ModelProvider
from artemis.mcp.action_specs import operator_shell_tool
from artemis.services.llm import RobustChatModelWrapper


async def probe_related_calls(model: CodexChatModel):
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
    calls = []
    for turn in range(3):
        start = time.monotonic()
        response = await model.ainvoke(messages)
        calls.append(
            {"seconds": round(time.monotonic() - start, 2), "usage": response.usage_metadata}
        )
        messages.extend(
            [response, HumanMessage(content=f"Acknowledge record {turn + 1:04d} as well.")]
        )
    later_cached = [
        (call["usage"] or {}).get("input_token_details", {}).get("cache_read", 0)
        for call in calls[1:]
    ]
    hit = any(count > 0 for count in later_cached)
    return {
        "status": "passed" if hit else "unsupported",
        "passed": hit,
        "reason": (
            "Cached input reported on appended-history calls."
            if hit
            else "Cache reuse was not observed on later calls; caching is unsupported for this "
            "probe run. This does not establish that the provider never caches."
        ),
        "later_cached_input_tokens": later_cached,
        "calls": calls,
    }


async def probe(model_name, reasoning_effort="medium", service_tier="fast", *, cache_only=False):
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
    result = {"related_call_cache": await probe_related_calls(model)}
    probe_report = {
        "model": model_name,
        "reasoning_effort": reasoning_effort,
        "requested_service_tier": service_tier,
        "auth": "chatgpt",
        "device_actions_executed": False,
        "checks": result,
    }
    if cache_only:
        return probe_report

    async def measure(name, call):
        start = time.monotonic()
        value = await call
        result[name] = {"passed": True, "seconds": round(time.monotonic() - start, 2)}
        return value

    text = await measure("text", model.ainvoke("Reply exactly ARTEMIS_CODEX_OK."))
    assert text.content.strip() == "ARTEMIS_CODEX_OK"
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
    vision = await measure(
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
    )
    assert vision.content.strip().lower() == "blue", vision.content

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
    action = await measure("operator_multi_action_schema", bound.ainvoke(messages))
    assert [c["name"] for c in action.tool_calls] == ["click", "press_key"]
    assert action.tool_calls[0]["args"]["target"] == 2
    assert action.tool_calls[1]["args"]["key"] == "BACK"
    # A local protocol fixture is explicitly labelled, not a claim of device execution.
    messages += [
        action,
        *[
            ToolMessage(
                content="Protocol fixture receipt: DRY_RUN_ACCEPTED; no device action was performed.",
                tool_call_id=call["id"],
            )
            for call in action.tool_calls
        ],
        HumanMessage(
            content="What receipt did both tools return? Reply only DRY_RUN_ACCEPTED with no more tools."
        ),
    ]
    resumed = await measure("tool_result_continuation", bound.ainvoke(messages))
    assert not resumed.tool_calls and "DRY_RUN_ACCEPTED" in resumed.content

    report = await measure(
        "checker_schema",
        RobustChatModelWrapper(model)
        .with_structured_output(CheckReport)
        .ainvoke(
            "Return one assert verdict: item_text 'blue square', status passed, evidence 'synthetic image is blue', suggestion empty. No unmet subgoals."
        ),
    )
    assert report.verdicts[0].status == "passed"

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
    explorer = Explorer(context)
    explorer.denylisted_tools = {
        "ask_perception_tool",
        "detect_objects",
        "get_ocr_list",
        "ask_image_processor",
        "inspect_region",
    }
    with tempfile.TemporaryDirectory(prefix="artemis-probe-") as directory:
        screenshot = Path(directory) / "synthetic.png"
        image.save(screenshot)
        answer = await measure(
            "explorer_universal_loop",
            explorer._run_universal(
                query="Find the centre of the blue square.",
                context_feedback="",
                screenshot_path=str(screenshot),
                state={},
                minimal_list="",
                prompt_template="Locate the requested square. Submit one candidate with normalized 0-1000 centre coordinates.",
                max_turns=1,
            ),
        )
    candidates = json.loads(answer)["candidates"]
    assert candidates and all(abs(v - 500) <= 50 for v in candidates[0]["coords"])
    result["explorer_universal_loop"]["coords"] = candidates[0]["coords"]
    return probe_report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow real subscription calls")
    parser.add_argument(
        "--cache-only", action="store_true", help="Only probe related-call cache usage"
    )
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--service-tier", choices=("fast", "priority"), default="fast")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly allow subscription calls")
    print(
        json.dumps(
            asyncio.run(
                probe(
                    args.model, args.reasoning_effort, args.service_tier, cache_only=args.cache_only
                )
            ),
            indent=2,
        )
    )
