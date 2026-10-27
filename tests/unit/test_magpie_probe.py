"""A partial or uncached live probe must not report successful validation."""

import asyncio

from langchain_core.messages import AIMessage
import pytest

from scripts.probe_magpie_responses import (
    REQUIRED_CHECKS,
    all_checks_passed,
    cache_reused,
    check_result,
    required_checks_passed,
)


def test_only_a_complete_probe_can_report_all_passed():
    report = {"checks": {name: {"passed": True} for name in REQUIRED_CHECKS}}
    assert not all_checks_passed(report)
    report["completed"] = True
    assert required_checks_passed(report)
    assert not all_checks_passed(report)
    report["checks"]["tool_calls"] = {"passed": True}
    assert all_checks_passed(report)
    report["checks"].pop("cache_followup")
    assert not all_checks_passed(report)


@pytest.mark.asyncio
async def test_cancellation_records_the_incomplete_check():
    started = asyncio.Event()

    async def pending_call():
        started.set()
        await asyncio.Event().wait()

    report = {"checks": {"text": {"passed": True}}}
    task = asyncio.create_task(check_result(report, "vision", pending_call(), lambda _: True))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert report["checks"]["vision"] == {
        "passed": False,
        "status": "failed",
        "error_type": "CancelledError",
    }
    assert not all_checks_passed(report)


@pytest.mark.parametrize("cached, expected", [(0, False), (60, True), (101, False)])
def test_cache_probe_requires_valid_reported_cache_usage(cached, expected):
    response = AIMessage(
        content="CACHE_OK",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 3,
            "total_tokens": 103,
            "input_token_details": {"cache_read": cached},
        },
    )
    assert cache_reused(response) is expected


@pytest.mark.asyncio
async def test_nonfatal_capability_failure_remains_in_final_result():
    response = AIMessage(content="Only one call returned")

    async def call():
        return response

    report = {
        "completed": True,
        "checks": {name: {"passed": True} for name in REQUIRED_CHECKS},
    }
    observed = await check_result(report, "tool_calls", call(), lambda _: False, fatal=False)
    assert observed is response
    assert not all_checks_passed(report)
    assert report["checks"]["tool_calls"]["status"] == "unsupported"
    assert required_checks_passed(report)


@pytest.mark.asyncio
async def test_failed_cache_check_retains_actual_usage_and_fails_required_checks():
    response = AIMessage(
        content="CACHE_OK",
        usage_metadata={
            "input_tokens": 5000,
            "output_tokens": 3,
            "total_tokens": 5003,
            "input_token_details": {"cache_read": 0},
        },
    )

    async def call():
        return response

    report = {"completed": True, "checks": {name: {"passed": True} for name in REQUIRED_CHECKS}}
    await check_result(report, "cache_followup", call(), cache_reused, fatal=False)
    assert report["checks"]["cache_followup"]["usage"]["input_token_details"]["cache_read"] == 0
    assert report["checks"]["cache_followup"]["status"] == "failed"
    assert not required_checks_passed(report)
