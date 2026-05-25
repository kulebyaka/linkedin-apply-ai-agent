"""Grok (xAI) provider client — OpenAI-compatible API."""

from __future__ import annotations

from typing import ClassVar

from ._openai_compatible import OpenAICompatibleClient


class GrokClient(OpenAICompatibleClient):
    """xAI Grok client. Supports strict JSON schema (OpenAI-superset)."""

    base_url: ClassVar[str | None] = "https://api.x.ai/v1"
    supports_strict_schema: ClassVar[bool] = True
    provider_label: ClassVar[str] = "Grok"
