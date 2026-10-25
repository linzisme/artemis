"""Responses profile routing and native request contract, without network access."""

from pathlib import Path
from types import SimpleNamespace
import json
import os
import subprocess
import sys

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
import pytest

from artemis.config.llm import LLMConfig, _expand_default_into_nodes
from artemis.config.settings import settings
from artemis.llm.router import ModelFactory
from artemis.services.llm import _resolve_endpoint, get_memory_llm
from third_party.mobile_use.utils.file import load_jsonc


@pytest.fixture
def context(monkeypatch):
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ARTEMIS_FAKE_LLM", raising=False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr("unit-test-no-network"))
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "http://127.0.0.1:3425/v1")
    profile = Path(__file__).resolve().parents[2] / "config/magpie-sol.jsonc"
    with profile.open() as stream:
        config = LLMConfig.model_validate(_expand_default_into_nodes(load_jsonc(stream)))
    return SimpleNamespace(llm_config=config, data_engine=None)


@pytest.mark.parametrize("fallback", [False, True])
def test_all_roles_keep_magpie_responses_and_fast_medium(context, fallback):
    roles = [(name, False) for name in LLMConfig.model_fields if name != "utils"]
    roles += [(name, True) for name in type(context.llm_config.utils).model_fields]
    for name, is_utils in roles:
        ep = _resolve_endpoint(context, name, is_utils=is_utils, use_fallback=fallback)
        assert (ep.provider.value, ep.model_name, ep.reasoning_effort, ep.service_tier) == (
            "openai",
            "codex/gpt-6.1-sol",
            "medium",
            "priority",
        )
        assert ep.use_responses_api
        assert ep.cache_key() != ep.model_copy(update={"use_responses_api": False}).cache_key()
    model = ModelFactory.create_model(_resolve_endpoint(context, "operator", use_fallback=fallback))
    payload = model._get_request_payload([HumanMessage(content="synthetic request")])
    assert payload["model"] == "codex/gpt-6.1-sol"
    assert payload["reasoning"]["effort"] == "medium"
    assert payload["service_tier"] == "priority"
    assert payload["stream"] is True
    assert payload["store"] is False
    assert "input" in payload and "messages" not in payload
    assert "temperature" not in payload


def test_memory_and_visual_lenses_use_responses(context):
    from artemis.agents.flash.summarizer import VisualStepSummarizer
    from artemis.memory.chunking import StepCapsuleLens

    models = [
        get_memory_llm(context),
        VisualStepSummarizer(context, "codex/gpt-6.1-sol")._llm,
        StepCapsuleLens("codex/gpt-6.1-sol", ctx=context)._get_llm(),
    ]
    for model in models:
        assert isinstance(model, ChatOpenAI)
        assert model.use_responses_api
        assert str(model.openai_api_base) == "http://127.0.0.1:3425/v1"
        assert model.model_name == "codex/gpt-6.1-sol"


def test_flash_responses_factory_error_does_not_fall_back_to_google(context, monkeypatch):
    from artemis.agents.flash import runner

    def unavailable(*args, **kwargs):
        raise RuntimeError("Responses endpoint unavailable")

    monkeypatch.setattr(runner, "get_llm", unavailable)
    with pytest.raises(RuntimeError, match="Responses endpoint unavailable"):
        runner.FlashRunner._init_llm(SimpleNamespace(ctx=context))


def test_magpie_launcher_overrides_inherited_provider_before_settings_load(tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = """
import json, os, runpy, sys
entry = runpy.run_path(sys.argv[1])
entry['configure_magpie_runtime']()
from artemis.config.llm import get_default_llm_config
from artemis.config.settings import settings
cfg = get_default_llm_config().operator
print(json.dumps({
    'base_url': str(settings.OPENAI_BASE_URL),
    'key_is_loopback_placeholder': settings.OPENAI_API_KEY.get_secret_value() == 'magpie',
    'model': cfg.model,
    'responses': cfg.use_responses_api,
    'proxies_present': any(os.environ.get(k) for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')),
    'fake_llm': os.environ.get('ARTEMIS_FAKE_LLM'),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(root / "scripts/magpie_local.py")],
        cwd=tmp_path,
        env={
            **os.environ,
            "ARTEMIS_CODEX_STATE_DIR": str(tmp_path / "runtime"),
            "OPENAI_API_KEY": "must-not-be-used",
            "OPENAI_BASE_URL": "http://invalid.example/v1",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "ARTEMIS_FAKE_LLM": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "base_url": "http://127.0.0.1:3425/v1",
        "key_is_loopback_placeholder": True,
        "model": "codex/gpt-6.1-sol",
        "responses": True,
        "proxies_present": False,
        "fake_llm": None,
    }
