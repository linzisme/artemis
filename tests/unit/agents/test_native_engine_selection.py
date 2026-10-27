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

"""Provider-facing native engine eligibility for Explorer and Video Analyzer."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from pydantic import SecretStr

from artemis.agents.explorer.explorer import Explorer
from artemis.agents.video_analyzer.video_analyzer import VideoAnalyzer
from artemis.config import LLMWithFallback, settings


def engine_context(provider="openrouter", model="google/gemini-3.8-flash", client=None):
    endpoint = LLMWithFallback(
        provider=provider, model=model, fallback={"provider": provider, "model": model}
    )
    return SimpleNamespace(
        llm_config=SimpleNamespace(
            explorer=endpoint,
            utils=SimpleNamespace(video_analyzer=endpoint),
        ),
        agent_config=None,
        data_engine=None,
        _genai_client=client,
    )


def test_explorer_openrouter_gemini_remains_universal_with_google_credentials(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("synthetic-google-key"))

    explorer = Explorer(engine_context())

    assert explorer.use_native_gemini is False


def test_video_analyzer_openrouter_ignores_shared_google_client(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("synthetic-google-key"))
    shared_client = object()
    ctx = engine_context(client=shared_client)

    analyzer = VideoAnalyzer(ctx)

    assert analyzer.use_native_gemini is False
    assert analyzer.client is None
    assert ctx._genai_client is shared_client


@pytest.mark.parametrize("agent_type", [Explorer, VideoAnalyzer])
@pytest.mark.parametrize(
    "provider",
    ["openrouter", "openai", "anthropic", "codex", "vertexai", "xai", "ollama", "vllm", "custom"],
)
@pytest.mark.parametrize("model", ["google/gemini-3.8-flash", "other-model"])
@pytest.mark.parametrize("key", [None, "", "synthetic-google-key"])
@pytest.mark.parametrize("shared_client", [False, True])
def test_non_google_engines_remain_universal(
    monkeypatch, agent_type, provider, model, key, shared_client
):
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr(key) if key is not None else None)
    client = object() if shared_client else None
    ctx = engine_context(provider, model, client)
    with patch("google.genai.Client", side_effect=AssertionError("Unexpected Google client")):
        agent = agent_type(ctx)

    assert agent.use_native_gemini is False
    assert agent.client is None
    assert ctx._genai_client is client


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_type", [Explorer, VideoAnalyzer])
@pytest.mark.parametrize(
    "model,key,shared_client,native",
    [
        ("gemini-3.8-flash", None, False, False),
        ("gemini-3.8-flash", "", False, False),
        ("gemini-3.8-flash", "synthetic-google-key", False, True),
        ("google/gemini-3.8-flash", "synthetic-google-key", False, True),
        ("gemini-3.8-flash", None, True, True),
        ("gemini-3.8-flash", "synthetic-google-key", True, True),
        ("other-model", None, True, False),
        ("other-model", "synthetic-google-key", True, False),
        ("other-model", "synthetic-google-key", False, False),
    ],
)
async def test_google_engines_require_compatible_model_and_credentials(
    monkeypatch, agent_type, model, key, shared_client, native
):
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr(key) if key is not None else None)

    async def empty_files():
        if False:
            yield

    client = SimpleNamespace(
        aio=SimpleNamespace(files=SimpleNamespace(list=AsyncMock(return_value=empty_files())))
    )
    ctx = engine_context("google", model, client if shared_client else None)
    with patch("google.genai.Client", return_value=client):
        agent = agent_type(ctx)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert agent.use_native_gemini is native
    if agent_type is VideoAnalyzer:
        assert agent.client is (client if native else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("shared_client", [False, True])
async def test_explorer_openrouter_tool_results_stay_on_openrouter(
    monkeypatch, tmp_path, shared_client
):
    """Drive the public loop through real routing and an HTTP transport double."""
    import json

    import importlib

    from openai import DefaultAsyncHttpxClient
    from PIL import Image

    from artemis.config import LLMConfig
    from artemis.context import ArtemisContext
    from artemis.graph.state import State
    from third_party.mobile_use.context import DeviceContext

    for name in (
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("synthetic-google-key"))
    monkeypatch.setattr(settings, "OPEN_ROUTER_API_KEY", SecretStr("synthetic-openrouter-key"))
    monkeypatch.setattr(settings, "DATA_ENGINE_DB_PATH", str(tmp_path / "engine.db"))
    monkeypatch.setattr(settings, "TRACES_PATH", tmp_path)
    endpoint = engine_context().llm_config.explorer
    config = LLMConfig(
        **{
            name: endpoint
            for name in (
                "planner",
                "summarizer",
                "operator",
                "operator_summarizer",
                "log_reader_sub_agent",
                "log_analyzer",
                "diagnoser",
                "checker",
                "planner_avatar",
                "history_analyzer_expert",
                "diagnoser_expert",
                "explorer",
            )
        },
        utils={"outputter": endpoint, "hopper": endpoint, "video_analyzer": endpoint},
    )
    ctx = ArtemisContext(
        device=DeviceContext(device_width=100, device_height=100), llm_config=config
    )
    ctx._genai_client = object() if shared_client else None
    screenshot = tmp_path / "screen.jpg"
    Image.new("RGB", (100, 100), "white").save(screenshot)
    state = State(
        initial_goal="Find target", latest_screenshot=str(screenshot), latest_ui_hierarchy=[]
    )
    requests = []
    replies = iter(
        [
            ("inspect_region", {"x_min": 0, "y_min": 0, "x_max": 500, "y_max": 500}),
            (
                "submit_answer",
                {
                    "candidates": [{"label": "D1", "coords": [250, 250], "description": "target"}],
                    "fallback_message": "",
                },
            ),
        ]
    )

    async def send(client, request, **kwargs):
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-openrouter-key"
        body = json.loads(request.content)
        requests.append(body)
        name, args = next(replies)
        response = {
            "id": f"reply-{len(requests)}",
            "object": "chat.completion",
            "created": 0,
            "model": "google/gemini-3.8-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"call-{len(requests)}",
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        # The SDK may use httpx or its httpx2 fork; use its request's transport.
        transport = importlib.import_module(type(request).__module__.split(".")[0])
        return transport.Response(200, request=request, json=response)

    monkeypatch.setattr(DefaultAsyncHttpxClient, "send", send)
    with patch("google.genai.Client", side_effect=AssertionError("Unexpected native Google call")):
        result = json.loads(
            await Explorer(ctx).run(
                query="Find target",
                context_feedback="",
                screenshot_path=str(screenshot),
                state=state,
                version="ultra",
            )
        )

    assert result["candidates"][0]["coords"] == [250, 250]
    assert len(requests) == 2
    for request in requests:
        assert request["model"] == "google/gemini-3.8-flash"
        assert all(tool["type"] == "function" for tool in request["tools"])
        assert {tool["function"]["name"] for tool in request["tools"]} <= {
            "inspect_region",
            "submit_answer",
            "get_ocr_list",
            "ask_perception_tool",
            "detect_objects",
            "ask_image_processor",
        }
    messages = requests[1]["messages"]
    tool_result = next(message for message in messages if message["role"] == "tool")
    assert tool_result["tool_call_id"] == "call-1"
    assert "Inspected region coordinates: [0, 0] to [500, 500]" in tool_result["content"]
    assert any(
        message["role"] == "user"
        and isinstance(message["content"], list)
        and any(block.get("type") == "image_url" for block in message["content"])
        and any("inspect_region" in block.get("text", "") for block in message["content"])
        for message in messages
    )


@pytest.mark.parametrize("agent_type", [Explorer, VideoAnalyzer])
@pytest.mark.parametrize("missing_config", [True, False])
@pytest.mark.parametrize("shared_client,native", [(False, False), (True, True)])
def test_omitted_configuration_retains_google_defaults(
    monkeypatch, agent_type, missing_config, shared_client, native
):
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", None)
    ctx = engine_context(client=object() if shared_client else None)
    ctx.llm_config = (
        None
        if missing_config
        else SimpleNamespace(explorer=None, utils=SimpleNamespace(video_analyzer=None))
    )

    agent = agent_type(ctx)

    assert agent.use_native_gemini is native
    assert agent.model_name == "gemini-3.8-flash"
