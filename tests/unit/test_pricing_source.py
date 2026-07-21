"""Tests for the dynamic model catalog (LiteLLM pricing source)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from src.llm.model_catalog import ModelCatalogEntry
from src.llm.provider import LLMProvider
from src.llm import pricing_source
from src.llm.pricing_source import (
    fetch_catalog,
    load_catalog,
    parse_litellm_json,
    read_cache,
    write_cache,
)

FIXTURE = Path(__file__).parent / "fixtures" / "litellm_model_prices.json"
NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def litellm_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parser_produces_expected_model_list(litellm_data):
    entries = parse_litellm_json(litellm_data)
    ids = {e.model for e in entries}
    # openai/anthropic/deepseek/xai chat models with a price, deduped, dated
    # snapshots and non-text modalities dropped.
    assert ids == {
        "claude-opus-4-1",
        "claude-haiku-4-5",
        "gpt-4o",
        "gpt-4o-mini",
        "grok-4",
        "grok-2-1212",
        "deepseek-chat",
    }


def test_parser_strips_provider_prefixes(litellm_data):
    entries = {e.model: e for e in parse_litellm_json(litellm_data)}
    # xai/ and deepseek/ prefixes stripped
    assert entries["grok-4"].provider == LLMProvider.GROK
    assert entries["grok-2-1212"].provider == LLMProvider.GROK
    assert entries["deepseek-chat"].provider == LLMProvider.DEEPSEEK


def test_parser_cost_math_per_1m(litellm_data):
    entries = {e.model: e for e in parse_litellm_json(litellm_data)}
    # gpt-4o: 2.5e-06 / 1e-05 per token -> 2.5 / 10.0 per 1M
    assert entries["gpt-4o"].input_cost_per_1m == 2.5
    assert entries["gpt-4o"].output_cost_per_1m == 10.0
    # deepseek-chat: 2.8e-07 / 4.2e-07 -> 0.28 / 0.42
    assert entries["deepseek-chat"].input_cost_per_1m == 0.28
    assert entries["deepseek-chat"].output_cost_per_1m == 0.42


def test_parser_capability_mapping(litellm_data):
    entries = {e.model: e for e in parse_litellm_json(litellm_data)}
    # supports_response_schema True -> supports_strict_schema True
    assert entries["gpt-4o"].supports_strict_schema is True
    assert entries["deepseek-chat"].supports_strict_schema is True
    # supports_response_schema None -> False
    assert entries["grok-4"].supports_strict_schema is False
    assert entries["grok-2-1212"].supports_strict_schema is False


def test_parser_drops_dated_snapshot_when_alias_present(litellm_data):
    entries = {e.model for e in parse_litellm_json(litellm_data)}
    # claude-opus-4-1 (alias) kept, claude-opus-4-1-20250805 (dated) dropped
    assert "claude-opus-4-1" in entries
    assert "claude-opus-4-1-20250805" not in entries


def test_parser_noise_and_provider_filters(litellm_data):
    ids = {e.model for e in parse_litellm_json(litellm_data)}
    assert "text-embedding-3-small" not in ids       # mode=embedding
    assert "gpt-4o-transcribe" not in ids             # mode=audio_transcription
    assert "gpt-4o-realtime-preview" not in ids       # mode=chat but 'realtime'
    assert "mistral-large-latest" not in ids          # excluded provider


def test_parser_dedups_prefixed_and_bare_duplicates(litellm_data):
    entries = [e for e in parse_litellm_json(litellm_data) if e.model == "deepseek-chat"]
    assert len(entries) == 1  # deepseek-chat + deepseek/deepseek-chat collapse


# ---------------------------------------------------------------------------
# fetch_catalog (httpx mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_catalog_parses_http_response(monkeypatch, litellm_data):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=litellm_data)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        kwargs.setdefault("transport", transport)
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    entries = await fetch_catalog("https://example.test/prices.json")
    assert {e.model for e in entries} >= {"gpt-4o", "claude-haiku-4-5"}


@pytest.mark.asyncio
async def test_fetch_catalog_raises_on_http_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**{**kw, "transport": transport}),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await fetch_catalog("https://example.test/prices.json")


# ---------------------------------------------------------------------------
# Cache round-trip + load_catalog fallback chain / TTL
# ---------------------------------------------------------------------------


def _sample_entries() -> list[ModelCatalogEntry]:
    return [
        ModelCatalogEntry(
            provider=LLMProvider.OPENAI,
            model="gpt-4o",
            display_name="gpt-4o",
            input_cost_per_1m=2.5,
            output_cost_per_1m=10.0,
            supports_strict_schema=True,
            supports_json_object=True,
        )
    ]


def test_cache_round_trip(tmp_path):
    path = str(tmp_path / "cache.json")
    entries = _sample_entries()
    write_cache(path, entries, now=NOW)
    cached = read_cache(path)
    assert cached is not None
    assert cached.fetched_at == NOW
    assert [e.model for e in cached.entries] == ["gpt-4o"]


def test_read_cache_missing_returns_none(tmp_path):
    assert read_cache(str(tmp_path / "nope.json")) is None


def test_read_cache_corrupt_returns_none(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{ not json", encoding="utf-8")
    assert read_cache(str(path)) is None


@pytest.mark.asyncio
async def test_load_uses_fresh_cache_without_fetching(tmp_path, monkeypatch):
    path = str(tmp_path / "cache.json")
    write_cache(path, _sample_entries(), now=NOW)

    async def boom(*a, **k):
        raise AssertionError("must not fetch when cache is fresh")

    monkeypatch.setattr(pricing_source, "fetch_catalog", boom)
    result = await load_catalog(cache_path=path, ttl_hours=24, now=NOW + timedelta(hours=1))
    assert [e.model for e in result] == ["gpt-4o"]


@pytest.mark.asyncio
async def test_load_refetches_when_cache_stale(tmp_path, monkeypatch):
    path = str(tmp_path / "cache.json")
    write_cache(path, _sample_entries(), now=NOW)

    fresh = [
        ModelCatalogEntry(
            provider=LLMProvider.ANTHROPIC,
            model="claude-opus-4-8",
            display_name="claude-opus-4-8",
            input_cost_per_1m=5.0,
            output_cost_per_1m=25.0,
            supports_strict_schema=True,
            supports_json_object=True,
        )
    ]

    async def fake_fetch(*a, **k):
        return fresh

    monkeypatch.setattr(pricing_source, "fetch_catalog", fake_fetch)
    result = await load_catalog(
        cache_path=path, ttl_hours=24, now=NOW + timedelta(hours=25)
    )
    assert [e.model for e in result] == ["claude-opus-4-8"]
    # fresh result was written back to cache
    assert [e.model for e in read_cache(path).entries] == ["claude-opus-4-8"]


@pytest.mark.asyncio
async def test_load_falls_back_to_stale_cache_on_fetch_failure(tmp_path, monkeypatch):
    path = str(tmp_path / "cache.json")
    write_cache(path, _sample_entries(), now=NOW)

    async def failing_fetch(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(pricing_source, "fetch_catalog", failing_fetch)
    result = await load_catalog(
        cache_path=path, ttl_hours=24, now=NOW + timedelta(hours=25)
    )
    assert [e.model for e in result] == ["gpt-4o"]  # stale cache


@pytest.mark.asyncio
async def test_load_falls_back_to_static_when_no_cache(tmp_path, monkeypatch):
    path = str(tmp_path / "nonexistent.json")

    async def failing_fetch(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(pricing_source, "fetch_catalog", failing_fetch)
    static = _sample_entries()
    result = await load_catalog(cache_path=path, ttl_hours=24, static=static, now=NOW)
    assert result == static
