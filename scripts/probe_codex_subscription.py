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


async def probe(model_name):
    model = ModelFactory.create_model(
        ModelEndpoint(
            provider=ModelProvider.CODEX,
            model_name=model_name,
            timeout_seconds=180,
            reasoning_effort="low",
        )
    )
    assert isinstance(model, CodexChatModel), "Fake models must not be used in the live probe"
    result = {}

    async def measure(name, call):
        start = time.monotonic()
        value = await call
        result[name] = {"passed": True, "seconds": round(time.monotonic() - start, 2)}
        return value

    text = await measure("text", model.ainvoke("Reply exactly ARTEMIS_CODEX_OK."))
    assert text.content.strip() == "ARTEMIS_CODEX_OK"
    result["text"]["usage"] = text.usage_metadata

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
                    "reasoning_effort": "low",
                    "fallback": {"provider": "codex", "model": model_name},
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
    return {
        "model": model_name,
        "auth": "chatgpt",
        "device_actions_executed": False,
        "checks": result,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow real subscription calls")
    parser.add_argument("--model", default="gpt-6.1-sol")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly allow subscription calls")
    print(json.dumps(asyncio.run(probe(args.model)), indent=2))
