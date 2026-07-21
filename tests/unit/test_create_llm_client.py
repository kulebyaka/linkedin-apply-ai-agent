"""Tests for ``create_llm_client`` (agents/_shared): provider resolution and
LiteLLM-prefixed model strings backed by ``InstructorClient``."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# WeasyPrint loads native system libraries at import time (Pango/GLib) that are
# unavailable in the unit-test env; ``_shared`` chains into it, so stub the
# package before importing.
_wp_mock = MagicMock()
for _mod in [
    "weasyprint",
    "weasyprint.css",
    "weasyprint.html",
    "weasyprint.text",
    "weasyprint.text.fonts",
]:
    sys.modules.setdefault(_mod, _wp_mock)

from src.agents import _shared  # noqa: E402,I001
from src.llm.providers.instructor_client import InstructorClient  # noqa: E402,I001


def _patched_settings(**overrides):
    base = {
        "primary_llm_provider": "openai",
        "openai_api_key": "oai-key",
        "anthropic_api_key": "anthropic-key",
        "deepseek_api_key": "deepseek-key",
        "grok_api_key": "grok-key",
        "openai_model": "gpt-4o",
        "anthropic_model": "claude-sonnet-5",
        "deepseek_model": "deepseek-chat",
        "grok_model": "grok-4",
    }
    base.update(overrides)
    return base


class TestCreateLLMClient:
    @pytest.mark.parametrize(
        ("provider", "model", "expected_model", "expected_key"),
        [
            ("openai", "gpt-4o", "openai/gpt-4o", "oai-key"),
            ("anthropic", "claude-sonnet-5", "anthropic/claude-sonnet-5", "anthropic-key"),
            ("deepseek", "deepseek-chat", "deepseek/deepseek-chat", "deepseek-key"),
            ("grok", "grok-4", "xai/grok-4", "grok-key"),
        ],
    )
    def test_builds_instructor_client_with_prefixed_model(
        self, provider, model, expected_model, expected_key
    ):
        overrides = _patched_settings(primary_llm_provider=provider)
        with patch.multiple(_shared.settings, **overrides):
            client = _shared.create_llm_client()

        assert isinstance(client, InstructorClient)
        assert client.model == expected_model
        assert client.api_key == expected_key

    def test_grok_maps_to_xai_prefix(self):
        with patch.multiple(_shared.settings, **_patched_settings()):
            client = _shared.create_llm_client(llm_provider="grok", llm_model="grok-4")
        assert client.model == "xai/grok-4"

    def test_model_override_is_prefixed(self):
        with patch.multiple(_shared.settings, **_patched_settings()):
            client = _shared.create_llm_client(
                llm_provider="anthropic", llm_model="claude-opus-4-8"
            )
        assert client.model == "anthropic/claude-opus-4-8"

    def test_missing_api_key_raises(self):
        with patch.multiple(_shared.settings, **_patched_settings(openai_api_key=None)):
            with pytest.raises(ValueError, match="API key not configured"):
                _shared.create_llm_client(llm_provider="openai")

    def test_unsupported_provider_raises(self):
        with patch.multiple(_shared.settings, **_patched_settings()):
            with pytest.raises(ValueError):
                _shared.create_llm_client(llm_provider="not-a-provider")
