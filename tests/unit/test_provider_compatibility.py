"""Compatibility through public Explorer.run and real provider adapters; no live IO."""

import asyncio
import base64
import io
import json
import socket
from types import SimpleNamespace

import httpx
import httpx2
from PIL import Image
from pydantic import SecretStr
import pytest

from artemis.agents.explorer.explorer import Explorer
from artemis.config.llm import LLMConfig, _expand_default_into_nodes
from artemis.config.settings import settings


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openrouter"])
async def test_explorer_dispatches_sequential_tools_and_returns_text_and_images(
    monkeypatch, tmp_path, provider
):
    """The provider sees ARTEMIS crop receipts/images before submitting its answer."""
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "ARTEMIS_FAKE_LLM",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "TRACES_PATH", tmp_path)
    monkeypatch.setattr(settings, "DATA_ENGINE_DB_PATH", tmp_path / "probe.db")
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("synthetic-google-must-not-be-used"))

    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr("synthetic-key"))
    monkeypatch.setattr(settings, "OPEN_ROUTER_API_KEY", SecretStr("synthetic-key"))
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://compatibility.invalid/v1")
    expected_url = (
        "https://compatibility.invalid/v1/chat/completions"
        if provider == "openai"
        else "https://openrouter.ai/api/v1/chat/completions"
    )

    def no_network(*args, **kwargs):
        raise AssertionError("No live network is allowed")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    requests = []
    calls = [
        ("inspect_region", {"x_min": 250, "y_min": 250, "x_max": 750, "y_max": 750}),
        ("inspect_region", {"x_min": 0, "y_min": 0, "x_max": 500, "y_max": 500}),
        (
            "submit_answer",
            {
                "candidates": [{"label": "D1", "coords": [500, 500], "description": "blue square"}],
                "fallback_message": "",
            },
        ),
    ]

    async def send(client, request, **kwargs):
        assert str(request.url) == expected_url
        body = json.loads(request.content)
        assert body["model"] == "gemini-shaped-model"
        assert {t["function"]["name"] for t in body["tools"]} == {"inspect_region", "submit_answer"}
        requests.append(body)
        name, arguments = calls[len(requests) - 1]
        response_type = httpx2 if isinstance(request, httpx2.Request) else httpx
        event = {
            "id": f"response-{len(requests)}",
            "object": "chat.completion",
            "created": 0,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"call-{len(requests)}",
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments)},
                            }
                        ],
                    },
                }
            ],
        }
        return response_type.Response(
            200,
            request=request,
            json=event,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    monkeypatch.setattr(httpx2.AsyncClient, "send", send)
    config = LLMConfig.model_validate(
        _expand_default_into_nodes(
            {
                "default": {
                    "provider": provider,
                    "model": "gemini-shaped-model",
                    "fallback": {"provider": provider, "model": "gemini-shaped-model"},
                }
            }
        )
    )
    context = SimpleNamespace(
        llm_config=config,
        data_engine=None,
        agent_config=SimpleNamespace(
            denylisted_tools={
                "explorer": [
                    "ask_perception_tool",
                    "detect_objects",
                    "get_ocr_list",
                    "ask_image_processor",
                ]
            }
        ),
    )
    screenshot = tmp_path / "synthetic.png"
    Image.new("RGB", (400, 400), "blue").save(screenshot)
    result = await asyncio.wait_for(
        Explorer(context).run(
            "Find the blue square",
            "",
            str(screenshot),
            SimpleNamespace(operator_raw_data={"width": 400, "height": 400}),
            version="ultra",
        ),
        timeout=10,
    )
    assert json.loads(result)["candidates"][0]["coords"] == [500, 500]
    assert len(requests) == 3
    history = requests[-1]["messages"]
    receipts = [m for m in history if m["role"] == "tool"]
    assert [(m["tool_call_id"], m["content"]) for m in receipts] == [
        ("call-1", "Inspected region coordinates: [250, 250] to [750, 750]"),
        ("call-2", "Inspected region coordinates: [0, 0] to [500, 500]"),
    ]
    images = [
        block["image_url"]["url"]
        for m in history
        if isinstance(m["content"], list)
        for block in m["content"]
        if block.get("type") == "image_url"
    ]
    assert len(images) == 3
    crop = Image.open(io.BytesIO(base64.b64decode(images[-1].split(",", 1)[1])))
    assert crop.size == (400, 400)
    assert crop.getpixel((200, 200))[2] > 250
