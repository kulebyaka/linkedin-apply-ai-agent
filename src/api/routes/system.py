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
    """Health check endpoint with consumer health status."""
    ctx = get_ctx(request)
    consumer_health = ctx.consumer_manager.health_check() if ctx.consumer_manager else {}
    return {
        "status": "running",
        "message": "LinkedIn Job Application Agent API",
        **consumer_health,
    }


@router.get("/api/llm/models")
async def list_llm_models(
    operation: Annotated[str | None, Query()] = None,
):
    """Return the LLM model catalog, optionally filtered by operation.

    Public endpoint (no auth) — exposes only model names, display names,
    and pricing. Never exposes API keys or user data.
    """
    from src.llm.model_catalog import (
        OPERATIONS,
        build_label,
        get_catalog_for_operation,
    )
    from src.llm.provider import LLMProvider

    settings = get_settings()

    if operation is not None and operation not in OPERATIONS:
        raise HTTPException(
            422,
            f"Invalid operation: {operation!r}. "
            f"Must be one of: {', '.join(OPERATIONS)} (or omit for full catalog)",
        )

    entries = get_catalog_for_operation(operation)  # type: ignore[arg-type]

    try:
        default_provider = LLMProvider(settings.primary_llm_provider)
    except ValueError:
        default_provider = LLMProvider.OPENAI
    provider_to_model = {
        LLMProvider.OPENAI: settings.openai_model,
        LLMProvider.DEEPSEEK: settings.deepseek_model,
        LLMProvider.GROK: settings.grok_model,
        LLMProvider.ANTHROPIC: settings.anthropic_model,
    }
    default_model = provider_to_model.get(default_provider, "")

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
