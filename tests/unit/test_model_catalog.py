"""Tests for the static LLM model catalog.

Guards the correctness invariants the catalog must hold as the offline
fallback: real (dashed) Anthropic model IDs, and correct operation
filtering/sorting.
"""

import re

from src.llm.model_catalog import (
    MODEL_CATALOG,
    ModelCatalogEntry,
    build_label,
    get_catalog_for_operation,
)
from src.llm.provider import LLMProvider

# A dotted version segment like "-4.6" / "4.5" — the real Anthropic API uses
# dashes ("claude-sonnet-4-6"), and dotted IDs 404. This regex catches any
# regression that reintroduces a dotted model ID.
_DOTTED_VERSION = re.compile(r"\d+\.\d+")


def test_no_anthropic_entry_has_dotted_version() -> None:
    """Anthropic model IDs must be dashed (claude-sonnet-4-6), never dotted."""
    anthropic = [e for e in MODEL_CATALOG if e.provider == LLMProvider.ANTHROPIC]
    assert anthropic, "expected at least one Anthropic catalog entry"
    for entry in anthropic:
        assert not _DOTTED_VERSION.search(entry.model), (
            f"Anthropic model id {entry.model!r} contains a dotted version "
            "segment; the real API uses dashes and 404s on dotted ids"
        )


def test_anthropic_lineup_is_current() -> None:
    """The refreshed Anthropic lineup must be present with correct pricing."""
    by_model = {
        e.model: e for e in MODEL_CATALOG if e.provider == LLMProvider.ANTHROPIC
    }
    assert set(by_model) == {
        "claude-opus-4-8",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    }
    assert (by_model["claude-opus-4-8"].input_cost_per_1m,
            by_model["claude-opus-4-8"].output_cost_per_1m) == (5.00, 25.00)
    assert (by_model["claude-sonnet-5"].input_cost_per_1m,
            by_model["claude-sonnet-5"].output_cost_per_1m) == (3.00, 15.00)
    assert (by_model["claude-haiku-4-5"].input_cost_per_1m,
            by_model["claude-haiku-4-5"].output_cost_per_1m) == (1.00, 5.00)


def test_get_catalog_none_returns_full_catalog_sorted() -> None:
    entries = get_catalog_for_operation(None)
    assert len(entries) == len(MODEL_CATALOG)
    outputs = [e.output_cost_per_1m for e in entries]
    assert outputs == sorted(outputs, reverse=True), "must be sorted expensive→cheap"


def test_get_catalog_cv_generation_requires_schema_or_json() -> None:
    entries = get_catalog_for_operation("cv_generation")
    assert entries, "expected some models for cv_generation"
    for e in entries:
        assert e.supports_strict_schema or e.supports_json_object
    outputs = [e.output_cost_per_1m for e in entries]
    assert outputs == sorted(outputs, reverse=True)


def test_get_catalog_job_filtering_matches_cv_generation_filter() -> None:
    assert (
        get_catalog_for_operation("job_filtering")
        == get_catalog_for_operation("cv_generation")
    )


def test_get_catalog_filter_prompt_generation_requires_plain_text() -> None:
    entries = get_catalog_for_operation("filter_prompt_generation")
    assert entries
    for e in entries:
        assert e.supports_plain_text


def test_build_label_renders_provider_and_pricing() -> None:
    entry = ModelCatalogEntry(
        provider=LLMProvider.ANTHROPIC,
        model="claude-opus-4-8",
        display_name="Claude Opus 4.8",
        input_cost_per_1m=5.00,
        output_cost_per_1m=25.00,
        supports_strict_schema=True,
        supports_json_object=True,
    )
    assert build_label(entry) == "Anthropic Claude Opus 4.8 ($5.00 / $25.00 per 1M)"
