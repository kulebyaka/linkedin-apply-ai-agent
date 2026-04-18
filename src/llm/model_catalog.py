"""Static catalog of LLM models with pricing and capability metadata.

Pricing reflects public per-million-token rates as of the snapshot date below.
Update manually when provider pricing changes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .provider import LLMProvider

PRICING_SNAPSHOT_DATE = "2026-04-18"

Operation = Literal["cv_generation", "job_filtering", "filter_prompt_generation"]

OPERATIONS: tuple[Operation, ...] = (
    "cv_generation",
    "job_filtering",
    "filter_prompt_generation",
)


class ModelCatalogEntry(BaseModel):
    """A single LLM model entry with pricing and capability metadata."""

    provider: LLMProvider
    model: str
    display_name: str
    input_cost_per_1m: float
    output_cost_per_1m: float
    supports_strict_schema: bool
    supports_json_object: bool
    supports_plain_text: bool = True


_PROVIDER_DISPLAY = {
    LLMProvider.OPENAI: "OpenAI",
    LLMProvider.ANTHROPIC: "Anthropic",
    LLMProvider.DEEPSEEK: "DeepSeek",
    LLMProvider.GROK: "Grok",
}


MODEL_CATALOG: list[ModelCatalogEntry] = [
    # OpenAI
    ModelCatalogEntry(
        provider=LLMProvider.OPENAI,
        model="gpt-5.2",
        display_name="GPT-5.2",
        input_cost_per_1m=1.75,
        output_cost_per_1m=14.00,
        supports_strict_schema=True,
        supports_json_object=True,
    ),
    ModelCatalogEntry(
        provider=LLMProvider.OPENAI,
        model="gpt-5-mini",
        display_name="GPT-5 mini",
        input_cost_per_1m=0.25,
        output_cost_per_1m=2.00,
        supports_strict_schema=True,
        supports_json_object=True,
    ),
    ModelCatalogEntry(
        provider=LLMProvider.OPENAI,
        model="gpt-5-nano",
        display_name="GPT-5 nano",
        input_cost_per_1m=0.05,
        output_cost_per_1m=0.40,
        supports_strict_schema=True,
        supports_json_object=True,
    ),
    ModelCatalogEntry(
        provider=LLMProvider.OPENAI,
        model="gpt-4o",
        display_name="GPT-4o",
        input_cost_per_1m=2.50,
        output_cost_per_1m=10.00,
        supports_strict_schema=True,
        supports_json_object=True,
    ),
    ModelCatalogEntry(
        provider=LLMProvider.OPENAI,
        model="gpt-4o-mini",
        display_name="GPT-4o mini",
        input_cost_per_1m=0.15,
        output_cost_per_1m=0.60,
        supports_strict_schema=True,
        supports_json_object=True,
    ),
    # Anthropic
    ModelCatalogEntry(
        provider=LLMProvider.ANTHROPIC,
        model="claude-opus-4.6",
        display_name="Claude Opus 4.6",
        input_cost_per_1m=5.00,
        output_cost_per_1m=25.00,
        supports_strict_schema=True,
        supports_json_object=True,
    ),
    ModelCatalogEntry(
        provider=LLMProvider.ANTHROPIC,
        model="claude-sonnet-4.6",
        display_name="Claude Sonnet 4.6",
        input_cost_per_1m=3.00,
        output_cost_per_1m=15.00,
        supports_strict_schema=True,
        supports_json_object=True,
    ),
    ModelCatalogEntry(
        provider=LLMProvider.ANTHROPIC,
        model="claude-haiku-4.5",
        display_name="Claude Haiku 4.5",
        input_cost_per_1m=1.00,
        output_cost_per_1m=5.00,
        supports_strict_schema=True,
        supports_json_object=True,
    ),
    # DeepSeek
    ModelCatalogEntry(
        provider=LLMProvider.DEEPSEEK,
        model="deepseek-chat",
        display_name="DeepSeek Chat",
        input_cost_per_1m=0.28,
        output_cost_per_1m=0.42,
        supports_strict_schema=False,
        supports_json_object=True,
    ),
    ModelCatalogEntry(
        provider=LLMProvider.DEEPSEEK,
        model="deepseek-reasoner",
        display_name="DeepSeek Reasoner",
        input_cost_per_1m=0.28,
        output_cost_per_1m=0.42,
        supports_strict_schema=False,
        supports_json_object=True,
    ),
    # xAI Grok
    ModelCatalogEntry(
        provider=LLMProvider.GROK,
        model="grok-4",
        display_name="Grok 4",
        input_cost_per_1m=3.00,
        output_cost_per_1m=15.00,
        supports_strict_schema=True,
        supports_json_object=True,
    ),
    ModelCatalogEntry(
        provider=LLMProvider.GROK,
        model="grok-4-fast",
        display_name="Grok 4.1 Fast",
        input_cost_per_1m=0.20,
        output_cost_per_1m=0.50,
        supports_strict_schema=True,
        supports_json_object=True,
    ),
    ModelCatalogEntry(
        provider=LLMProvider.GROK,
        model="grok-3",
        display_name="Grok 3",
        input_cost_per_1m=3.00,
        output_cost_per_1m=15.00,
        supports_strict_schema=True,
        supports_json_object=True,
    ),
]


def build_label(entry: ModelCatalogEntry) -> str:
    """Render a human-readable dropdown label:
    'Provider DisplayName ($input / $output per 1M)'.
    """
    provider_name = _PROVIDER_DISPLAY.get(entry.provider, str(entry.provider))
    return (
        f"{provider_name} {entry.display_name} "
        f"(${entry.input_cost_per_1m:.2f} / ${entry.output_cost_per_1m:.2f} per 1M)"
    )


def get_catalog_for_operation(operation: Operation | None = None) -> list[ModelCatalogEntry]:
    """Return models suitable for the given operation, sorted by output
    cost descending (expensive → cheap).

    - cv_generation, job_filtering: require strict schema OR json_object support.
    - filter_prompt_generation: any model with plain-text support.
    - None: returns the full catalog.
    """
    if operation is None:
        entries = list(MODEL_CATALOG)
    elif operation in ("cv_generation", "job_filtering"):
        entries = [
            e for e in MODEL_CATALOG
            if e.supports_strict_schema or e.supports_json_object
        ]
    elif operation == "filter_prompt_generation":
        entries = [e for e in MODEL_CATALOG if e.supports_plain_text]
    else:
        raise ValueError(f"Unknown operation: {operation}")

    return sorted(entries, key=lambda e: e.output_cost_per_1m, reverse=True)


def get_default_choice(provider: LLMProvider, model: str) -> dict:
    """Build a ModelChoice-like dict for a (provider, model) pair.

    Does not validate against the catalog — used to surface the global
    .env default even if that model isn't listed.
    """
    return {"provider": provider.value, "model": model}
