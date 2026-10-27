"""Public model-factory reuse and outgoing requests with synthetic credentials."""

import importlib
import json

import httpx
from pydantic import SecretStr
import pytest

from artemis.config.settings import settings
from artemis.llm.router import ModelEndpoint, ModelFactory, ModelProvider


@pytest.fixture
def factory(monkeypatch):
    class IsolatedModelFactory(ModelFactory):
        _cache = {}

    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "ARTEMIS_FAKE_LLM",
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "OPENAI_BASE_URL",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_API_URL",
        "ANTHROPIC_BASE_URL",
        "OPEN_ROUTER_API_KEY",
        "XAI_API_KEY",
        "GOOGLE_GEMINI_BASE_URL",
        "GOOGLE_GENAI_USE_VERTEXAI",
    ):
        monkeypatch.delenv(name, raising=False)
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPEN_ROUTER_API_KEY",
        "XAI_API_KEY",
    ):
        monkeypatch.setattr(settings, name, None)
    return IsolatedModelFactory


@pytest.fixture
def requests(monkeypatch):
    recorded = []

    def send(client, request, *, http=httpx, **kwargs):
        recorded.append(request)
        if request.url.path.endswith("/responses"):
            event = {
                "type": "response.output_text.delta",
                "delta": "ok",
                "item_id": "msg_test",
                "output_index": 0,
                "content_index": 0,
                "sequence_number": 1,
            }
            return http.Response(
                200,
                request=request,
                headers={"content-type": "text/event-stream"},
                content=f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode(),
            )
        if request.url.path.endswith("/messages"):
            return http.Response(
                200,
                request=request,
                json={
                    "id": "synthetic-message",
                    "type": "message",
                    "role": "assistant",
                    "model": "test-model",
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        if "/models/" in request.url.path:
            return http.Response(
                200,
                request=request,
                json={
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": [{"text": "ok"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 1,
                        "candidatesTokenCount": 1,
                        "totalTokenCount": 2,
                    },
                },
            )
        return http.Response(
            200,
            request=request,
            json={
                "id": "synthetic-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    # OpenAI SDK releases may use httpx2 while other providers still use httpx.
    try:
        httpx2 = importlib.import_module("httpx2")
    except ImportError:
        pass
    else:
        monkeypatch.setattr(
            httpx2.Client,
            "send",
            lambda client, request, **kw: send(client, request, http=httpx2, **kw),
        )
    return recorded


def test_factory_tracks_inherited_openai_endpoint(factory, requests, monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr("synthetic-first-key"))
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://first.invalid/v1")
    endpoint = ModelEndpoint(provider=ModelProvider.OPENAI, model_name="test-model")
    first = factory.get_model(endpoint)
    first.invoke("hello")

    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://second.invalid/v1")
    second = factory.get_model(endpoint)
    second.invoke("hello")

    assert [str(request.url) for request in requests] == [
        "https://first.invalid/v1/chat/completions",
        "https://second.invalid/v1/chat/completions",
    ]
    assert second is not first
    assert factory.get_model(endpoint) is second
    assert endpoint.api_base is None


@pytest.mark.parametrize("source", ["settings", "environment"])
@pytest.mark.parametrize(
    "provider,key_name,header,prefix",
    [
        (ModelProvider.OPENAI, "OPENAI_API_KEY", "authorization", "Bearer "),
        (ModelProvider.OPENROUTER, "OPEN_ROUTER_API_KEY", "authorization", "Bearer "),
        (ModelProvider.XAI, "XAI_API_KEY", "authorization", "Bearer "),
        (ModelProvider.ANTHROPIC, "ANTHROPIC_API_KEY", "x-api-key", ""),
        (ModelProvider.GOOGLE, "GOOGLE_API_KEY", "x-goog-api-key", ""),
    ],
)
def test_factory_tracks_inherited_credentials(
    factory, requests, monkeypatch, source, provider, key_name, header, prefix
):
    def set_key(value):
        if source == "settings":
            monkeypatch.setattr(settings, key_name, SecretStr(value))
        else:
            monkeypatch.setenv(key_name, value)

    set_key("synthetic-first-key")
    endpoint = ModelEndpoint(provider=provider, model_name="gemini-2.5-flash")
    first = factory.get_model(endpoint)
    first.invoke("hello")
    set_key("synthetic-second-key")
    second = factory.get_model(endpoint)
    second.invoke("hello")

    assert [request.headers[header] for request in requests] == [
        prefix + "synthetic-first-key",
        prefix + "synthetic-second-key",
    ]
    assert second is not first
    assert factory.get_model(endpoint) is second
    explicit = endpoint.model_copy(update={"api_key": "synthetic-second-key"})
    assert factory.get_model(explicit) is second
    assert endpoint.api_key is None


@pytest.mark.parametrize(
    "provider,base_name,path",
    [
        (ModelProvider.OPENAI, "OPENAI_API_BASE", "/chat/completions"),
        (ModelProvider.OPENAI, "OPENAI_BASE_URL", "/chat/completions"),
        (ModelProvider.ANTHROPIC, "ANTHROPIC_API_URL", "/v1/messages"),
        (ModelProvider.ANTHROPIC, "ANTHROPIC_BASE_URL", "/v1/messages"),
        (ModelProvider.OLLAMA, "OPENAI_BASE_URL", "/chat/completions"),
        (ModelProvider.VLLM, "OPENAI_BASE_URL", "/chat/completions"),
        (ModelProvider.CUSTOM, "OPENAI_BASE_URL", "/chat/completions"),
    ],
)
def test_factory_tracks_environment_endpoint(
    factory, requests, monkeypatch, provider, base_name, path
):
    endpoint = ModelEndpoint(provider=provider, model_name="test-model", api_key="synthetic-key")
    monkeypatch.setenv(base_name, "https://first.invalid/v1")
    first = factory.get_model(endpoint)
    first.invoke("hello")
    monkeypatch.setenv(base_name, "https://second.invalid/v1")
    second = factory.get_model(endpoint)
    second.invoke("hello")
    assert [str(request.url) for request in requests] == [
        "https://first.invalid/v1" + path,
        "https://second.invalid/v1" + path,
    ]
    assert second is not first
    explicit = endpoint.model_copy(update={"api_base": "https://second.invalid/v1"})
    assert factory.get_model(explicit) is second


@pytest.mark.parametrize(
    "provider,base_url",
    [
        (ModelProvider.OPENROUTER, "https://openrouter.ai/api/v1"),
        (ModelProvider.XAI, "https://api.x.ai/v1"),
        (ModelProvider.ANTHROPIC, "https://api.anthropic.com"),
        (ModelProvider.OLLAMA, "http://localhost:8000/v1"),
        (ModelProvider.VLLM, "http://localhost:8000/v1"),
        (ModelProvider.CUSTOM, "http://localhost:8000/v1"),
    ],
)
def test_factory_reuses_explicit_and_default_endpoint(factory, provider, base_url):
    endpoint = ModelEndpoint(provider=provider, model_name="test-model", api_key="synthetic-key")
    assert factory.get_model(endpoint) is factory.get_model(
        endpoint.model_copy(update={"api_base": base_url})
    )


def test_endpoint_diagnostics_do_not_expose_credentials(factory, requests, caplog):
    endpoint = ModelEndpoint(
        provider=ModelProvider.OPENAI,
        model_name="test-model",
        api_key="synthetic-private-key",
    )
    factory.get_model(endpoint).invoke("hello")
    for diagnostic in (str(endpoint.cache_key()), repr(endpoint), str(endpoint), caplog.text):
        assert "synthetic-private-key" not in diagnostic
    assert (
        endpoint.cache_key() != endpoint.model_copy(update={"api_key": "another-key"}).cache_key()
    )


@pytest.mark.parametrize(
    "provider,key_name,header,prefix",
    [
        (ModelProvider.OLLAMA, "OPENAI_API_KEY", "authorization", "Bearer "),
        (ModelProvider.VLLM, "OPENAI_API_KEY", "authorization", "Bearer "),
        (ModelProvider.CUSTOM, "OPENAI_API_KEY", "authorization", "Bearer "),
        (ModelProvider.OPENROUTER, "OPENAI_API_KEY", "authorization", "Bearer "),
        (ModelProvider.XAI, "OPENAI_API_KEY", "authorization", "Bearer "),
        (ModelProvider.GOOGLE, "GEMINI_API_KEY", "x-goog-api-key", ""),
    ],
)
def test_factory_tracks_sdk_credential_defaults(
    factory, requests, monkeypatch, provider, key_name, header, prefix
):
    monkeypatch.setenv(key_name, "synthetic-first-key")
    endpoint = ModelEndpoint(provider=provider, model_name="gemini-2.5-flash")
    first = factory.get_model(endpoint)
    first.invoke("hello")
    monkeypatch.setenv(key_name, "synthetic-second-key")
    second = factory.get_model(endpoint)
    second.invoke("hello")
    assert [request.headers[header] for request in requests] == [
        prefix + "synthetic-first-key",
        prefix + "synthetic-second-key",
    ]
    assert second is not first


def test_factory_tracks_google_endpoint(factory, requests, monkeypatch):
    endpoint = ModelEndpoint(
        provider=ModelProvider.GOOGLE, model_name="gemini-2.5-flash", api_key="synthetic-key"
    )
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "https://first.invalid")
    first = factory.get_model(endpoint)
    first.invoke("hello")
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "https://second.invalid")
    second = factory.get_model(endpoint)
    second.invoke("hello")
    assert [request.url.host for request in requests] == ["first.invalid", "second.invalid"]
    assert second is not first
    assert (
        factory.get_model(endpoint.model_copy(update={"api_base": "https://second.invalid"}))
        is second
    )


@pytest.mark.parametrize(
    "change,payload_key,expected",
    [
        ({"model_name": "other-model"}, "model", "other-model"),
        ({"temperature": 0.7}, "temperature", 0.7),
        ({"max_tokens": 123}, "max_completion_tokens", 123),
        ({"reasoning_effort": "medium"}, "reasoning_effort", "medium"),
        ({"service_tier": "priority"}, "service_tier", "priority"),
        ({"timeout_seconds": 180}, None, 180),
        ({"provider": ModelProvider.CUSTOM}, "model", "test-model"),
    ],
)
def test_factory_separates_request_parameters(factory, requests, change, payload_key, expected):
    endpoint = ModelEndpoint(
        provider=ModelProvider.OPENAI,
        model_name="test-model",
        api_key="synthetic-key",
        api_base="https://gateway.invalid/v1",
    )
    first = factory.get_model(endpoint)
    changed = endpoint.model_copy(update=change)
    second = factory.get_model(changed)
    assert second is not first
    assert factory.get_model(changed) is second
    second.invoke("hello")
    if payload_key is None:
        assert requests[-1].extensions["timeout"]["read"] == expected
    else:
        assert json.loads(requests[-1].content)[payload_key] == expected


def test_factory_switches_responses_mode_and_preserves_native_history(factory, requests):
    from langchain_core.messages import HumanMessage

    endpoint = ModelEndpoint(
        provider=ModelProvider.OPENAI,
        model_name="test-model",
        api_key="synthetic-key",
        api_base="https://gateway.invalid/v1",
        timeout_seconds=180,
        reasoning_effort="medium",
        service_tier="priority",
    )
    chat = factory.get_model(endpoint)
    chat.invoke("hello")
    responses_endpoint = endpoint.model_copy(update={"use_responses_api": True})
    responses = factory.get_model(responses_endpoint)
    history = [HumanMessage(content="hello")]
    history.append(responses.invoke(history))
    history.append(HumanMessage(content="continue"))
    responses.invoke(history)

    assert responses is not chat
    assert factory.get_model(responses_endpoint) is responses
    assert [request.url.path for request in requests] == [
        "/v1/chat/completions",
        "/v1/responses",
        "/v1/responses",
    ]
    for request in requests[1:]:
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["reasoning"] == {"effort": "medium"}
        assert body["service_tier"] == "priority"
        assert body["stream"] is True
        assert body["store"] is False
        assert body["include"] == ["reasoning.encrypted_content"]
        assert "previous_response_id" not in body
        assert "temperature" not in body
        assert request.extensions["timeout"]["read"] == 180
    assert [item["role"] for item in json.loads(requests[-1].content)["input"]] == [
        "user",
        "assistant",
        "user",
    ]


def test_explicit_connection_overrides_global_changes(factory, requests, monkeypatch):
    endpoint = ModelEndpoint(
        provider=ModelProvider.OPENAI,
        model_name="test-model",
        api_key="synthetic-explicit-key",
        api_base="https://explicit.invalid/v1",
    )
    first = factory.get_model(endpoint)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr("synthetic-ignored-key"))
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://ignored.invalid/v1")
    assert factory.get_model(endpoint) is first
    first.invoke("hello")
    assert requests[-1].url.host == "explicit.invalid"
    assert requests[-1].headers["authorization"] == "Bearer synthetic-explicit-key"


@pytest.mark.parametrize(
    "change",
    [
        {"thinking_budget": 2048},
        {"thinking_level": "high"},
        {"include_thoughts": True},
        {"enable_grounding": True},
    ],
)
def test_factory_separates_google_thinking_and_grounding(factory, change):
    endpoint = ModelEndpoint(
        provider=ModelProvider.GOOGLE, model_name="gemini-3-flash-preview", api_key="synthetic-key"
    )
    first = factory.get_model(endpoint)
    changed = endpoint.model_copy(update=change)
    second = factory.get_model(changed)
    assert second is not first
    assert factory.get_model(changed) is second


def test_codex_factory_reuses_profile_parameters_without_api_credentials(factory, monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr("synthetic-unrelated-key"))
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://unrelated.invalid/v1")
    endpoint = ModelEndpoint(
        provider=ModelProvider.CODEX,
        model_name="gpt-6.1-sol",
        timeout_seconds=180,
        reasoning_effort="medium",
        service_tier="fast",
    )
    model = factory.get_model(endpoint)
    assert factory.get_model(endpoint.model_copy()) is model
    assert (
        model.model_name,
        model.timeout_seconds,
        model.reasoning_effort,
        model.service_tier,
    ) == (
        "gpt-6.1-sol",
        180,
        "medium",
        "fast",
    )
    assert factory.get_model(endpoint.model_copy(update={"service_tier": None})) is not model
