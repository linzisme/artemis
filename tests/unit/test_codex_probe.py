"""Probe cache claims are checked at the Codex executable boundary."""

import pytest

from scripts.probe_codex_subscription import probe_related_calls
from tests.unit import test_codex_provider

cli = test_codex_provider.cli


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cached,content,status",
    [
        ([0, 0, 0], "CACHE_PROBE_OK", "unsupported"),
        ([0, 12, 12], "CACHE_PROBE_OK", "passed"),
        ([0, 24, 24], "CACHE_PROBE_OK", "failed"),
        ([0, 12, 12], "ready", "passed"),
    ],
)
async def test_codex_cache_probe_requires_valid_usage_independently_of_answer(
    cli, cached, content, status
):
    configure, _ = cli
    model = configure({"content": content, "tool_calls": []}, cached_input_tokens=cached)
    report = await probe_related_calls(model)
    assert report["status"] == status
    assert report["passed"] is (status == "passed")
    assert report["later_cached_input_tokens"] == cached[1:]


@pytest.mark.asyncio
@pytest.mark.parametrize("sequential_failure", [False, True])
async def test_codex_probe_keeps_sequential_checks_when_parallel_is_unsupported(
    monkeypatch, tmp_path, sequential_failure
):
    import json
    import os
    import sys

    from artemis.config.settings import settings
    from scripts.probe_codex_subscription import probe

    def text(value):
        return {"content": value, "tool_calls": []}

    def tool(name, args):
        return {"content": "", "tool_calls": [{"name": name, "arguments_json": json.dumps(args)}]}

    click = tool("click", {"target": 2})
    responses = [
        text("CACHE_PROBE_OK"),
        text("CACHE_PROBE_OK"),
        text("CACHE_PROBE_OK"),
        text("ARTEMIS_CODEX_OK"),
        text("blue"),
        click,
        click,
        tool("press_key", {"key": "BACK"}),
        text("DRY_RUN_ACCEPTED"),
        {
            "result_json": json.dumps(
                {
                    "verdicts": [
                        {
                            "item_text": "blue square",
                            "kind": "assert",
                            "status": "passed",
                            "evidence": "synthetic image is blue",
                            "suggestion": "",
                        }
                    ],
                    "unmet_subgoals": [],
                }
            )
        },
        tool(
            "submit_answer",
            {
                "candidates": [{"label": "D1", "coords": [500, 500], "description": "blue square"}],
                "fallback_message": "",
            },
        ),
    ]
    if sequential_failure:
        responses[7] = text("No tool call")
    queue = tmp_path / "responses.json"
    queue.write_text(json.dumps(responses))
    executable = tmp_path / "codex"
    executable.write_text(f"""#!{sys.executable}
import json, pathlib, sys
if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT")
    sys.exit(0)
sys.stdin.read()
queue = pathlib.Path({str(queue)!r})
responses = json.loads(queue.read_text())
response = responses.pop(0)
queue.write_text(json.dumps(responses))
pathlib.Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(json.dumps(response))
print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 23, "output_tokens": 7, "cached_input_tokens": 0}}}}))
""")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("ARTEMIS_FAKE_LLM", raising=False)
    monkeypatch.setattr(settings, "TRACES_PATH", tmp_path)
    monkeypatch.setattr(settings, "DATA_ENGINE_DB_PATH", tmp_path / "probe.db")
    output = tmp_path / "probe.json"
    if sequential_failure:
        with pytest.raises(AssertionError):
            await probe("synthetic-model", output=output)
        result = json.loads(output.read_text())
        assert result["checks"]["text"]["passed"]
        assert result["checks"]["tool_calls"]["status"] == "unsupported"
        assert result["checks"]["sequential_first_tool"]["passed"]
        assert result["checks"]["sequential_second_tool"]["status"] == "failed"
        for name in ("tool_result_continuation", "checker_schema", "explorer_universal_loop"):
            assert result["checks"][name]["status"] == "unexecuted"
        assert not result["required_checks_passed"]
        assert not result["all_checks_passed"]
        return
    result = await probe("synthetic-model", output=output)
    assert json.loads(output.read_text()) == result
    assert result["checks"]["tool_calls"]["status"] == "unsupported"
    assert result["checks"]["sequential_first_tool"]["passed"]
    assert result["checks"]["sequential_second_tool"]["passed"]
    assert result["checks"]["tool_result_continuation"]["passed"]
    assert result["checks"]["explorer_universal_loop"]["passed"]
    assert result["required_checks_passed"]
    assert not result["all_checks_passed"]
    assert json.loads(queue.read_text()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["process", "timeout", "cancel"])
async def test_codex_probe_persists_failure_during_cache_measurement(
    cli, monkeypatch, tmp_path, failure
):
    import asyncio
    import json
    import os
    from pathlib import Path

    from artemis.llm.codex import CodexError
    from scripts.probe_codex_subscription import probe

    configure, receipt = cli
    model = configure(**({"error": "unsupported model"} if failure == "process" else {"sleep": 30}))
    Path(model.executable).with_name("codex").symlink_to(model.executable)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    output = tmp_path / "failed-probe.json"
    task = asyncio.create_task(probe("synthetic-model", output=output))
    if failure != "process":
        for _ in range(200):
            if receipt.exists():
                break
            await asyncio.sleep(0.01)
        assert receipt.exists()
        if failure == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else TimeoutError):
            await asyncio.wait_for(task, timeout=0.1)
    else:
        with pytest.raises(CodexError):
            await task
    result = json.loads(output.read_text())
    assert result["checks"]["related_call_cache"]["status"] == "failed"
    assert result["checks"]["text"]["status"] == "unexecuted"
    assert not result["required_checks_passed"]
    assert not result["all_checks_passed"]


def test_codex_probe_cli_prints_failure_report_and_exits_nonzero(cli, monkeypatch, tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys

    configure, _ = cli
    model = configure(error="unsupported model")
    Path(model.executable).with_name("codex").symlink_to(model.executable)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    output = tmp_path / "cli-report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/probe_codex_subscription.py",
            "--live",
            "--cache-only",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 1
    report = json.loads(output.read_text())
    assert json.loads(completed.stdout) == report
    assert report["checks"]["related_call_cache"]["status"] == "failed"
    assert not report["required_checks_passed"]
