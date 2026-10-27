"""Explicit Magpie protocol/cache probe using synthetic data and no device actions."""

import argparse
import asyncio
import base64
from functools import partial
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def text_content(message):
    content = message.content
    if isinstance(content, str):
        return content.strip()
    return "".join(b.get("text", "") for b in content if isinstance(b, dict)).strip()


REQUIRED_CHECKS = frozenset(
    {
        "text",
        "vision",
        "sequential_first_tool",
        "sequential_second_tool",
        "tool_continuation",
        "checker",
        "hopper",
        "explorer",
        "cache_first",
        "cache_followup",
    }
)


def cache_reused(message):
    usage = message.usage_metadata or {}
    cached = usage.get("input_token_details", {}).get("cache_read", 0)
    return text_content(message) == "CACHE_OK" and 0 < cached <= usage.get("input_tokens", 0)


def required_checks_passed(report):
    checks = report["checks"]
    return (
        bool(report.get("completed"))
        and REQUIRED_CHECKS.issubset(checks)
        and all(checks[name]["passed"] for name in REQUIRED_CHECKS)
    )


def all_checks_passed(report):
    return (
        required_checks_passed(report)
        and "tool_calls" in report["checks"]
        and all(row["passed"] for row in report["checks"].values())
    )


async def check_result(report, name, call, validate, *, fatal=True):
    print("START " + name, flush=True)
    started = time.monotonic()
    value = None
    try:
        value = await asyncio.wait_for(call, timeout=180)
        assert validate(value), f"{name}: unexpected result"
    except (Exception, asyncio.CancelledError) as exc:
        report["checks"][name] = {
            "passed": False,
            "status": "unsupported" if name == "tool_calls" and value is not None else "failed",
            "error_type": type(exc).__name__,
        }
        if hasattr(value, "usage_metadata"):
            report["checks"][name]["usage"] = value.usage_metadata
        if hasattr(value, "tool_calls"):
            report["checks"][name]["observed_tool_calls"] = value.tool_calls
            report["checks"][name]["observed_text"] = text_content(value)
        print("FAIL " + name + " " + type(exc).__name__, flush=True)
        if fatal or isinstance(exc, asyncio.CancelledError):
            raise
        return value
    row = {"passed": True, "status": "passed", "seconds": round(time.monotonic() - started, 2)}
    if hasattr(value, "usage_metadata"):
        row["usage"] = value.usage_metadata
        row["served_tier"] = value.response_metadata.get("service_tier")
        row["served_model"] = value.response_metadata.get("model_name")
    report["checks"][name] = row
    print("PASS " + name + " " + str(row["seconds"]) + "s", flush=True)
    return value


async def probe(output: Path, *, profile_name: str = "magpie-sol.jsonc"):
    from scripts.magpie_local import configure_magpie_runtime
    from scripts.probe_tools import probe_sequential_tools

    os.environ["ARTEMIS_CODEX_STATE_DIR"] = str(output / "runtime")
    configure_magpie_runtime(profile_name=profile_name)
    import httpx2
    from langchain_core.messages import HumanMessage, SystemMessage
    from PIL import Image, ImageDraw

    from artemis.agents.checker.checker import CheckReport
    from artemis.agents.explorer.explorer import Explorer
    from artemis.config.llm import get_default_llm_config
    from artemis.llm.router import ModelFactory
    from artemis.mcp.action_specs import operator_shell_tool
    from artemis.services.llm import RobustChatModelWrapper, _resolve_endpoint
    from third_party.mobile_use.agents.hopper.hopper import HopperOutput

    config = get_default_llm_config()
    ctx = SimpleNamespace(llm_config=config, data_engine=None)
    endpoint = _resolve_endpoint(ctx, "operator")
    assert endpoint.use_responses_api, "This probe requires a Responses profile"
    model = ModelFactory.create_model(endpoint)
    report = {
        "profile": profile_name,
        "model": endpoint.model_name,
        "provider": endpoint.provider.value,
        "protocol": "responses",
        "base_url": endpoint.api_base or str(model.openai_api_base),
        "reasoning_effort": endpoint.reasoning_effort,
        "requested_service_tier": endpoint.service_tier,
        "device_actions_executed": False,
        "completed": False,
        "all_checks_passed": False,
        "checks": {},
        "requests": [],
    }
    original_send = httpx2.AsyncClient.send

    async def audited_send(client, request, *args, **kwargs):
        if request.method == "POST":
            body = json.loads(request.content)
            assert str(request.url) == report["base_url"].rstrip("/") + "/responses"
            assert "previous_response_id" not in body
            assert body.get("model") == report["model"]
            assert body.get("reasoning", {}).get("effort") == report["reasoning_effort"]
            assert body.get("service_tier") == report["requested_service_tier"]
            assert body.get("store") is False
            report["requests"].append(
                {
                    "path": request.url.path,
                    "model": body["model"],
                    "reasoning": body["reasoning"],
                    "service_tier": body.get("service_tier"),
                    "stream": body.get("stream"),
                    "input_items": len(body.get("input", [])),
                }
            )
        return await original_send(client, request, *args, **kwargs)

    check = partial(check_result, report)

    httpx2.AsyncClient.send = audited_send
    try:
        await check(
            "text",
            model.ainvoke("Return only MAGPIE_OK with no punctuation"),
            lambda r: text_content(r) == "MAGPIE_OK",
        )
        image = Image.new("RGB", (400, 400), "white")
        ImageDraw.Draw(image).rectangle((100, 100, 300, 300), fill="blue")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        block = {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
            },
        }
        await check(
            "vision",
            model.ainvoke(
                [
                    HumanMessage(
                        content=[
                            {
                                "type": "text",
                                "text": "What color is the square? Reply one lowercase word.",
                            },
                            block,
                        ]
                    )
                ]
            ),
            lambda r: text_content(r) == "blue",
        )
        tools = [operator_shell_tool("click"), operator_shell_tool("press_key")]
        bound = RobustChatModelWrapper(model).bind_tools(tools, parallel_tool_calls=True)
        messages = [
            SystemMessage(
                content=(
                    "This is a tool-call serialization test. Return both requested calls in one "
                    "response. They are protocol fixtures, not dependent live actions; no tool "
                    "execution or intermediate observation is required."
                )
            ),
            HumanMessage(
                content="Return exactly two tool calls in order: click target index 2; press_key BACK."
            ),
        ]
        await check(
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
            bound, check, continuation_name="tool_continuation", text_content=text_content
        )
        await check(
            "checker",
            RobustChatModelWrapper(model)
            .with_structured_output(CheckReport)
            .ainvoke(
                "Return one assert verdict: item_text 'blue square', status passed, evidence 'synthetic image is blue', suggestion empty. No unmet subgoals."
            ),
            lambda r: (
                len(r.verdicts) == 1
                and r.verdicts[0].kind == "assert"
                and r.verdicts[0].item_text == "blue square"
                and r.verdicts[0].status == "passed"
                and not r.unmet_subgoals
            ),
        )
        await check(
            "hopper",
            RobustChatModelWrapper(model)
            .with_structured_output(HopperOutput)
            .ainvoke(
                "Find the Settings package in this list: com.android.settings, com.android.calculator2. Return found=true and output=com.android.settings."
            ),
            lambda r: r.found and r.output == "com.android.settings",
        )
        ctx.agent_config = SimpleNamespace(
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
        explorer = Explorer(ctx)
        with tempfile.TemporaryDirectory(prefix="magpie-probe-") as temp:
            path = Path(temp) / "synthetic.png"
            image.save(path)
            await check(
                "explorer",
                explorer.run(
                    query="Find the centre of the blue square.",
                    context_feedback="",
                    screenshot_path=str(path),
                    state={},
                    minimal_list="",
                    version="pro",
                ),
                lambda r: (
                    bool(json.loads(r)["candidates"])
                    and all(abs(v - 500) <= 50 for v in json.loads(r)["candidates"][0]["coords"])
                ),
            )
        corpus = "\n".join(
            f"Synthetic reference row {i:03d}: the assigned color is blue and the value is {i}."
            for i in range(180)
        )
        conversation = [
            SystemMessage(content="Keep this reference for later turns:\n" + corpus),
            HumanMessage(content="Reply only CACHE_OK with no punctuation."),
        ]
        first = await check(
            "cache_first", model.ainvoke(conversation), lambda r: text_content(r) == "CACHE_OK"
        )
        conversation += [
            first,
            HumanMessage(content="Again reply only CACHE_OK with no punctuation."),
        ]
        await check("cache_followup", model.ainvoke(conversation), cache_reused)
        report["completed"] = True
    finally:
        httpx2.AsyncClient.send = original_send
        report["all_checks_passed"] = all_checks_passed(report)
        report["required_checks_passed"] = required_checks_passed(report)
        for name in (REQUIRED_CHECKS | {"tool_calls"}) - report["checks"].keys():
            report["checks"][name] = {"passed": False, "status": "unexecuted"}
        (output / "probe.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--profile",
        choices=("magpie-sol.jsonc", "magpie-terra.jsonc"),
        default="magpie-sol.jsonc",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to allow real Magpie model calls")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(asyncio.wait_for(probe(output, profile_name=args.profile), timeout=1200))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["required_checks_passed"] else 1)
