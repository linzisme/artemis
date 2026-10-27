"""Opt-in isolation for agent fixtures that construct Google clients."""

import socket

import pytest
from pydantic import SecretStr

from artemis.config.settings import settings
from artemis.config.llm import LLMConfig, _expand_default_into_nodes


@pytest.fixture
def google_test_runtime(monkeypatch):
    """Permit client construction with an inert key; forbid actual network calls."""
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("fixture-google-key"))
    monkeypatch.setenv("GOOGLE_API_KEY", "fixture-google-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-google-key")

    def reject_network(*args, **kwargs):
        raise AssertionError("Agent unit fixtures must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network)
    return LLMConfig.model_validate(
        _expand_default_into_nodes(
            {
                "default": {
                    "provider": "google",
                    "model": "gemini-2.5-flash",
                    "temperature": 0.1,
                    "fallback": {"provider": "google", "model": "gemini-2.5-flash"},
                }
            }
        )
    )
