"""Provider routing exercised through public services and SDK transports."""

import json
import socket
from types import SimpleNamespace

import httpx

try:
    import httpx2 as openai_httpx
except ImportError:
    openai_httpx = httpx
from langchain_core.messages import HumanMessage
from pydantic import SecretStr
import pytest

from artemis.config.llm import LLMConfig, _expand_default_into_nodes
from artemis.config.settings import settings
from artemis.services.llm import get_memory_llm


@pytest.fixture
def transport(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Live network is forbidden in routing tests")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.delenv("ARTEMIS_FAKE_LLM", raising=False)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    for key in ("GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPEN_ROUTER_API_KEY"):
        monkeypatch.setattr(settings, key, SecretStr("synthetic-routing-test"))
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://openai.example/v1")
    requests = []

    def send(client, request, **kwargs):
        payload = json.loads(request.content)
        requests.append((request.url, payload))
        if request.url.host == "api.anthropic.com":
            body = {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": payload["model"],
                "content": [{"type": "text", "text": "Synthetic screen remained unchanged."}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        else:
            body = {
                "id": "chat_test",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Synthetic screen remained unchanged.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        response_module = openai_httpx if isinstance(request, openai_httpx.Request) else httpx
        return response_module.Response(200, json=body, request=request)

    async def async_send(client, request, **kwargs):
        return send(client, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr(openai_httpx.Client, "send", send)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_send)
    monkeypatch.setattr(openai_httpx.AsyncClient, "send", async_send)
    return requests


def context_for(provider, model="configured-model", fallback_provider=None):
    config = LLMConfig.model_validate(
        _expand_default_into_nodes(
            {
                "default": {
                    "provider": provider,
                    "model": model,
                    "timeout": 37,
                    "fallback": {
                        "provider": fallback_provider or provider,
                        "model": "configured-fallback",
                    },
                }
            }
        )
    )
    return SimpleNamespace(llm_config=config, data_engine=None)


@pytest.mark.parametrize(
    "provider,host",
    [
        ("openai", "openai.example"),
        ("anthropic", "api.anthropic.com"),
        ("openrouter", "openrouter.ai"),
    ],
)
@pytest.mark.parametrize(
    "fallback,expected", [(False, "configured-model"), (True, "configured-fallback")]
)
def test_memory_uses_configured_endpoint_despite_legacy_model_override(
    transport, provider, host, fallback, expected
):
    model = get_memory_llm(context_for(provider), "gemini-legacy-memory", use_fallback=fallback)
    assert (getattr(model, "model_name", None) or getattr(model, "model", None)) == expected
    model.invoke([HumanMessage(content="synthetic memory")])
    url, payload = transport[-1]
    assert (url.host, payload["model"]) == (host, expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,host",
    [
        ("openai", "openai.example"),
        ("anthropic", "api.anthropic.com"),
        ("openrouter", "openrouter.ai"),
    ],
)
async def test_visual_summary_dispatch_uses_summarizer_endpoint(transport, provider, host):
    from artemis.agents.flash.summarizer import VisualStepSummarizer

    summarizer = VisualStepSummarizer(context_for(provider), "gemini-legacy-visual", retry_limit=0)
    summarizer.dispatch(
        step_number=1,
        action_name="observe",
        action_args={},
        pre_img_bytes=b"synthetic-image",
        post_img_bytes=None,
        exec_outcome="Synthetic screen remained unchanged.",
    )
    await summarizer.flush()
    assert summarizer.get_summary(1) == "Synthetic screen remained unchanged."
    url, payload = transport[-1]
    assert (url.host, payload["model"]) == (host, "configured-model")


@pytest.mark.parametrize(
    "provider,host",
    [
        ("openai", "openai.example"),
        ("anthropic", "api.anthropic.com"),
        ("openrouter", "openrouter.ai"),
    ],
)
@pytest.mark.parametrize(
    "fallback,expected", [(False, "configured-model"), (True, "configured-fallback")]
)
def test_public_roles_and_judges_send_to_configured_provider(
    transport, provider, host, fallback, expected
):
    from artemis.services.llm import get_llm

    ctx = context_for(provider)
    roles = [(name, False) for name in LLMConfig.model_fields if name != "utils"]
    roles += [(name, True) for name in type(ctx.llm_config.utils).model_fields]
    for name, is_utils in roles:
        model = get_llm(ctx, name, is_utils=is_utils, use_fallback=fallback)
        # Guard the default-judge regression before attempting its provider call.
        assert model.endpoint.provider.value == provider, name
        model.invoke([HumanMessage(content="synthetic role request")])
        url, payload = transport[-1]
        assert (url.host, payload["model"]) == (host, expected), name


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic", "openrouter"])
async def test_flash_initialization_failure_is_visible(
    transport, monkeypatch, mock_driver, provider
):
    from langchain_anthropic import ChatAnthropic
    import openai
    from artemis.agents.flash.runner import FlashRunner

    ctx = context_for(provider)
    ctx._active_driver = mock_driver
    ctx.device = SimpleNamespace(device_id="mock", device_width=1080, device_height=2400)
    runner = FlashRunner(ctx, "synthetic initialization", max_turns=1)
    ctx.llm_config.operator.model = f"unavailable-{provider}"

    def unavailable(*args, **kwargs):
        raise RuntimeError("Synthetic provider initialization failure")

    monkeypatch.setattr(openai.OpenAI, "__init__", unavailable)
    monkeypatch.setattr(ChatAnthropic, "__init__", unavailable)
    with pytest.raises(RuntimeError, match="Synthetic provider initialization failure"):
        await runner.run(SimpleNamespace())


@pytest.mark.parametrize(
    "provider,fallback_provider,fallback_model",
    [
        ("openai", "openai", "configured-fallback"),
        ("openai", "anthropic", "configured-fallback"),
        ("openai", "google", "gemini-explicit-fallback"),
        ("openai", "openrouter", "configured-model"),
        ("google", "openai", "configured-fallback"),
    ],
)
def test_capsule_service_retains_explicit_fallback(
    transport, provider, fallback_provider, fallback_model
):
    from artemis.memory.chunking import HistoryChunkManager

    ctx = context_for(provider, fallback_provider=fallback_provider)
    ctx.llm_config.summarizer.fallback.model = fallback_model
    manager = HistoryChunkManager(
        ctx=ctx, chunking_config=SimpleNamespace(model="configured-model")
    )
    assert manager.capsule_service.lens.has_fallback


def test_memory_honors_explicit_google_fallback_model(transport):
    from langchain_google_genai import ChatGoogleGenerativeAI

    ctx = context_for("openai", fallback_provider="google")
    ctx.llm_config.summarizer.fallback.model = "gemini-2.5-flash"
    ctx.llm_config.summarizer.fallback.thinking_budget = 1024
    model = get_memory_llm(ctx, "gemini-legacy-override", use_fallback=True)
    assert isinstance(model, ChatGoogleGenerativeAI)
    assert model.model == "gemini-2.5-flash"
    assert model.thinking_budget == 1024


def test_codex_rejects_cross_provider_fallback():
    with pytest.raises(ValueError, match="Codex subscription nodes cannot use an API fallback"):
        context_for("codex", fallback_provider="google")


@pytest.mark.parametrize("provider", ["openai", "anthropic", "openrouter"])
def test_non_google_binding_only_sends_declared_tools(transport, provider):
    from langchain_core.tools import tool
    from artemis.services.llm import get_llm

    @tool
    def read_synthetic_note() -> str:
        """Read a synthetic note using ARTEMIS."""
        return "synthetic note"

    model = get_llm(context_for(provider), "operator").bind_tools(
        [
            read_synthetic_note,
            {"google_search": {}},
            {"google_search_retrieval": {}},
            {"google_maps": {}},
            {"code_execution": {}},
        ],
        tool_config={"include_server_side_tool_invocations": True},
    )
    model.invoke([HumanMessage(content="synthetic tool request")])
    _, payload = transport[-1]
    assert len(payload["tools"]) == 1
    tool_payload = payload["tools"][0]
    assert tool_payload.get("function", tool_payload)["name"] == "read_synthetic_note"
    assert "tool_config" not in payload


def test_google_memory_defaults_and_model_override_are_preserved(transport):
    from langchain_google_genai import ChatGoogleGenerativeAI

    ctx = SimpleNamespace(llm_config=LLMConfig.model_validate(_expand_default_into_nodes({})))
    default = get_memory_llm(ctx)
    override = get_memory_llm(ctx, "gemini-2.5-flash-lite")
    assert isinstance(default, ChatGoogleGenerativeAI)
    assert default.model == "gemini-3.8-flash"
    assert override.model == "gemini-2.5-flash-lite"
    assert ctx.llm_config.get_agent("planner_validation").model == "gemini-3.5-flash-lite"


def test_capsule_skips_identical_provider_and_model_fallback(transport):
    from artemis.memory.chunking import HistoryChunkManager

    ctx = context_for("openai")
    ctx.llm_config.summarizer.fallback.model = "configured-model"
    manager = HistoryChunkManager(ctx=ctx)
    assert not manager.capsule_service.lens.has_fallback


@pytest.mark.parametrize(
    "profile,provider,model_name,reasoning,tier,responses",
    [
        ("codex-chatgpt", "codex", "gpt-6.1-sol", "medium", "fast", False),
        ("magpie-sol", "openai", "codex/gpt-6.1-sol", "medium", "priority", True),
        ("magpie-terra", "openai", "codex/gpt-6.1-terra", "medium", None, True),
        ("magpie-ca", "openai", "ca/7670:deepseek-v4.1-flash", None, None, False),
        ("magpie-workbuddy", "openai", "workbuddy/deepseek-v4.1-flash", "low", None, False),
    ],
)
@pytest.mark.parametrize("fallback", [False, True])
def test_public_profile_roles_preserve_inference_settings(
    transport, profile, provider, model_name, reasoning, tier, responses, fallback
):
    from pathlib import Path
    from third_party.mobile_use.utils.file import load_jsonc
    from artemis.services.llm import get_llm

    with (Path(__file__).resolve().parents[2] / "config" / f"{profile}.jsonc").open() as stream:
        config = LLMConfig.model_validate(_expand_default_into_nodes(load_jsonc(stream)))
    ctx = SimpleNamespace(llm_config=config, data_engine=None)
    roles = [(name, False) for name in LLMConfig.model_fields if name != "utils"]
    roles += [(name, True) for name in type(config.utils).model_fields]
    for name, is_utils in roles:
        endpoint = get_llm(ctx, name, is_utils=is_utils, use_fallback=fallback).endpoint
        assert (
            endpoint.provider.value,
            endpoint.model_name,
            endpoint.reasoning_effort,
            endpoint.service_tier,
            endpoint.use_responses_api,
            endpoint.timeout_seconds,
        ) == (
            provider,
            model_name,
            reasoning,
            tier,
            responses,
            60 if fallback else 180,
        )
    memory = get_memory_llm(ctx, "gemini-legacy-memory", use_fallback=fallback)
    assert memory.model_name == model_name
    assert memory.reasoning_effort == reasoning
    assert memory.service_tier == tier


@pytest.mark.asyncio
async def test_capsule_outage_uses_configured_cross_provider_fallback(transport, monkeypatch):
    from artemis.memory.chunking import HistoryChunkManager

    ctx = context_for("openai", fallback_provider="anthropic")
    capsule = {
        "doing": "Inspecting the synthetic screen",
        "did": "Read the synthetic note",
        "effect": "Note recorded",
        "verified_facts": ["synthetic fact"],
        "unresolved": [],
        "failed_paths": [],
        "important_entities": [],
        "intervals": [{"start_step": 1, "end_step": 1, "text": "Read the note"}],
    }
    observed = []

    async def send(client, request, **kwargs):
        payload = json.loads(request.content)
        observed.append((request.url.host, payload["model"]))
        response_module = openai_httpx if isinstance(request, openai_httpx.Request) else httpx
        if request.url.host == "openai.example":
            return response_module.Response(
                503, json={"error": {"message": "Synthetic outage"}}, request=request
            )
        assert request.url.host == "api.anthropic.com"
        return response_module.Response(
            200,
            json={
                "id": "msg_capsule",
                "type": "message",
                "role": "assistant",
                "model": payload["model"],
                "content": [{"type": "text", "text": json.dumps(capsule)}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    monkeypatch.setattr(openai_httpx.AsyncClient, "send", send)
    manager = HistoryChunkManager(ctx=ctx)
    service = manager.capsule_service
    service.submit("chunk:1-1", {"start_step": 1, "end_step": 1, "steps": []})
    await service.flush()
    assert json.loads(service.get_summary("chunk:1-1"))["did"] == "Read the synthetic note"
    assert observed[0] == ("openai.example", "configured-model")
    assert observed[-1] == ("api.anthropic.com", "configured-fallback")


@pytest.mark.parametrize("mode", ["load", "merge"])
@pytest.mark.parametrize(
    "role,is_utils",
    [
        ("operator", False),
        ("summarizer", False),
        ("planner_validation", False),
        ("video_analyzer", True),
    ],
)
@pytest.mark.parametrize("explicit_google", [False, True])
def test_provider_override_does_not_inherit_google_fallback(
    transport, monkeypatch, tmp_path, mode, role, is_utils, explicit_google
):
    from artemis.config.llm import deep_merge_llm_config, parse_llm_config
    from artemis.services.llm import get_llm

    override = {
        "provider": "openai",
        "model": "changed-role",
        "reasoning_effort": "low",
        "service_tier": "priority",
    }
    if explicit_google:
        override["fallback"] = {"provider": "google", "model": "gemini-deliberate"}
    if mode == "load":
        path = tmp_path / "roles.jsonc"
        path.write_text(json.dumps({"nodes": {role: override}}))
        monkeypatch.setenv("ARTEMIS_ARTEMIS_JSONC", str(path))
        config = parse_llm_config()
    else:
        changes = {"utils": {role: override}} if is_utils else {role: override}
        config = deep_merge_llm_config(context_for("google").llm_config, changes)
    ctx = SimpleNamespace(llm_config=config, data_engine=None)
    primary = get_llm(ctx, role, is_utils=is_utils).endpoint
    fallback = get_llm(ctx, role, is_utils=is_utils, use_fallback=True).endpoint
    assert primary.provider.value == "openai"
    assert (fallback.provider.value, fallback.model_name) == (
        ("google", "gemini-deliberate") if explicit_google else ("openai", "changed-role")
    )
    if not explicit_google:
        assert (fallback.reasoning_effort, fallback.service_tier) == ("low", "priority")


@pytest.mark.parametrize("explicit_api", [False, True])
@pytest.mark.parametrize("mode", ["load", "merge"])
def test_codex_provider_override_never_inherits_api_fallback(
    transport, monkeypatch, tmp_path, explicit_api, mode
):
    from artemis.config.llm import deep_merge_llm_config, parse_llm_config
    from artemis.services.llm import get_llm

    override = {"provider": "codex", "model": "subscription-model", "reasoning_effort": "low"}
    if explicit_api:
        override["fallback"] = {"provider": "google", "model": "gemini-2.5-flash"}

    def load():
        if mode == "merge":
            return deep_merge_llm_config(context_for("google").llm_config, {"operator": override})
        path = tmp_path / "codex-role.jsonc"
        path.write_text(json.dumps({"nodes": {"operator": override}}))
        monkeypatch.setenv("ARTEMIS_ARTEMIS_JSONC", str(path))
        return parse_llm_config()

    if explicit_api:
        with pytest.raises(ValueError, match="Codex subscription nodes cannot use an API fallback"):
            load()
    else:
        ctx = SimpleNamespace(llm_config=load(), data_engine=None)
        endpoint = get_llm(ctx, "operator", use_fallback=True).endpoint
        assert (endpoint.provider.value, endpoint.model_name, endpoint.reasoning_effort) == (
            "codex",
            "subscription-model",
            "low",
        )
