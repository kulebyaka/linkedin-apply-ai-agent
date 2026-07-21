"""Unit tests for GET /api/llm/models provider filtering.

The endpoint must only surface providers that have an API key configured on
the server, and must pick a sane default among those providers. Exercised by
calling the route coroutine directly with a fake request + patched settings
(no full app boot needed — the route is public and reads only settings + ctx).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from src.api.routes.system import list_llm_models
from src.llm.model_catalog import MODEL_CATALOG
from src.llm.provider import LLMProvider


def _fake_request(catalog=None):
    ctx = SimpleNamespace(model_catalog=list(MODEL_CATALOG if catalog is None else catalog))
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ctx=ctx)))


def _settings(**overrides):
    base = dict(
        openai_api_key=None,
        anthropic_api_key=None,
        deepseek_api_key=None,
        grok_api_key=None,
        openai_model="gpt-4o",
        anthropic_model="claude-sonnet-4-5",
        deepseek_model="deepseek-chat",
        grok_model="grok-2-1212",
        primary_llm_provider="openai",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _call(settings, operation=None):
    with patch("src.api.routes.system.get_settings", return_value=settings):
        return asyncio.run(list_llm_models(_fake_request(), operation=operation))


def test_only_configured_providers_are_returned():
    result = _call(_settings(openai_api_key="k", anthropic_api_key="k"))
    providers = {m["provider"] for m in result["models"]}
    assert providers == {"openai", "anthropic"}


def test_single_configured_provider():
    result = _call(_settings(anthropic_api_key="k"))
    providers = {m["provider"] for m in result["models"]}
    assert providers == {"anthropic"}


def test_default_falls_back_to_configured_provider():
    # Primary is openai, but only anthropic has a key → default must be anthropic.
    result = _call(_settings(anthropic_api_key="k", primary_llm_provider="openai"))
    assert result["default"]["provider"] == "anthropic"
    # And the default model must be one that's actually listed for that provider.
    anthropic_models = {m["model"] for m in result["models"] if m["provider"] == "anthropic"}
    assert result["default"]["model"] in anthropic_models


def test_default_uses_primary_when_configured():
    result = _call(_settings(openai_api_key="k", anthropic_api_key="k"))
    assert result["default"]["provider"] == "openai"


def test_no_keys_returns_full_catalog_unfiltered():
    # Bare dev environment: nothing configured → fall back to full catalog so
    # the UI is never empty.
    result = _call(_settings())
    providers = {m["provider"] for m in result["models"]}
    assert providers == {p.value for p in LLMProvider}
