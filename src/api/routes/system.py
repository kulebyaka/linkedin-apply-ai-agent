"""System endpoints: health check and LLM model catalog."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from src.api.deps import get_ctx
from src.config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/health")
async def health(request: Request):
    """Health check endpoint with consumer health status + in-flight counts."""
    ctx = get_ctx(request)
    consumer_health = ctx.consumer_manager.health_check() if ctx.consumer_manager else {}

    queued_count = 0
    processing_count = 0
    try:
        counts = await ctx.repository.count_by_status_global()
        queued_count = counts.get("queued", 0)
        processing_count = counts.get("processing", 0)
    except Exception:
        logger.debug("Failed to read status counts for /api/health", exc_info=True)

    return {
        "status": "running",
        "message": "LinkedIn Job Application Agent API",
        "queued_count": queued_count,
        "processing_count": processing_count,
        **consumer_health,
    }


def _configured_providers(settings) -> set:
    """Return the set of providers that have a non-empty API key configured.

    Drives the provider filtering on ``/api/llm/models`` so the UI only ever
    offers providers the server can actually call.
    """
    from src.llm.provider import LLMProvider

    keys = {
        LLMProvider.OPENAI: settings.openai_api_key,
        LLMProvider.DEEPSEEK: settings.deepseek_api_key,
        LLMProvider.GROK: settings.grok_api_key,
        LLMProvider.ANTHROPIC: settings.anthropic_api_key,
    }
    return {provider for provider, key in keys.items() if key}


@router.get("/api/llm/models")
async def list_llm_models(
    request: Request,
    operation: Annotated[str | None, Query()] = None,
):
    """Return the LLM model catalog, optionally filtered by operation.

    Public endpoint (no auth) — exposes only model names, display names,
    and pricing. Never exposes API keys or user data. Reads the live,
    context-held catalog (dynamically loaded from LiteLLM; static fallback).

    The provider list is filtered to those with an API key configured on the
    server, so the UI never offers a provider that would fail at call time.
    """
    from src.llm.model_catalog import (
        OPERATIONS,
        build_label,
        get_catalog_for_operation,
    )
    from src.llm.provider import LLMProvider

    settings = get_settings()
    ctx = get_ctx(request)

    if operation is not None and operation not in OPERATIONS:
        raise HTTPException(
            422,
            f"Invalid operation: {operation!r}. "
            f"Must be one of: {', '.join(OPERATIONS)} (or omit for full catalog)",
        )

    entries = get_catalog_for_operation(
        operation,  # type: ignore[arg-type]
        catalog=ctx.model_catalog,
    )

    # Restrict to providers with an API key configured. If none are configured
    # (e.g. a bare dev environment) fall back to the full list so the UI is
    # never empty.
    configured = _configured_providers(settings)
    if configured:
        entries = [e for e in entries if e.provider in configured]
    else:
        logger.warning(
            "No LLM API keys configured — returning the full model catalog "
            "unfiltered. Set OPENAI_API_KEY / ANTHROPIC_API_KEY / etc. to "
            "restrict the provider list."
        )

    provider_to_model = {
        LLMProvider.OPENAI: settings.openai_model,
        LLMProvider.DEEPSEEK: settings.deepseek_model,
        LLMProvider.GROK: settings.grok_model,
        LLMProvider.ANTHROPIC: settings.anthropic_model,
    }

    # Default provider/model: prefer the configured primary, but fall back to
    # the first available entry so we never default to a filtered-out provider.
    try:
        default_provider = LLMProvider(settings.primary_llm_provider)
    except ValueError:
        default_provider = LLMProvider.OPENAI
    available_providers = {e.provider for e in entries}
    if default_provider not in available_providers and entries:
        default_provider = entries[0].provider

    default_model = provider_to_model.get(default_provider, "")
    provider_models = {e.model for e in entries if e.provider == default_provider}
    if default_model not in provider_models:
        # The env default model isn't in the (filtered) catalog for this
        # provider — fall back to the first listed model for it.
        default_model = next(
            (e.model for e in entries if e.provider == default_provider), ""
        )

    return {
        "models": [
            {
                "provider": e.provider.value,
                "model": e.model,
                "display_name": e.display_name,
                "label": build_label(e),
                "input_cost_per_1m": e.input_cost_per_1m,
                "output_cost_per_1m": e.output_cost_per_1m,
                "supports_strict_schema": e.supports_strict_schema,
                "supports_json_object": e.supports_json_object,
            }
            for e in entries
        ],
        "default": {
            "provider": default_provider.value,
            "model": default_model,
        },
    }
