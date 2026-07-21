"""Anthropic Claude provider client."""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from ..base import (
    DEFAULT_MAX_TOKENS,
    BaseLLMClient,
    LLMTruncatedError,
    build_retry_feedback,
)
from ..prompt_spec import PromptSpec
from ..schema_strict import make_schema_anthropic_safe

logger = logging.getLogger(__name__)


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude client — GA structured outputs + native PDF input."""

    SUPPORTS_PDF_INPUT = True

    # Models that reject sampling params (temperature/top_p/top_k) with a 400.
    # Mirrors the reasoning_model_prefixes gating used for OpenAI models.
    SAMPLING_UNSUPPORTED_PREFIXES: tuple[str, ...] = (
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-sonnet-5",
        "claude-fable",
    )

    def __init__(self, api_key: str, model: str, **kwargs):
        super().__init__(api_key, model, **kwargs)
        try:
            from anthropic import Anthropic
        except ImportError as err:
            raise ImportError(
                "Anthropic package not installed. Install with: pip install anthropic"
            ) from err

        # Structured outputs are GA — no beta header required.
        self.client = Anthropic(api_key=api_key)

    def _supports_temperature(self) -> bool:
        """False for current-gen models that 400 on sampling parameters."""
        return not any(
            self.model.startswith(prefix)
            for prefix in self.SAMPLING_UNSUPPORTED_PREFIXES
        )

    def _log_usage(self, response: Any, elapsed: float, kind: str) -> None:
        """Log input/cached token counts (parity with the OpenAI base)."""
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
        cached = getattr(usage, "cache_read_input_tokens", 0) if usage else 0
        logger.info(
            "[TIMING] Anthropic %s call completed in %.2fs "
            "(input_tokens=%d cached_tokens=%d)",
            kind,
            elapsed,
            input_tokens or 0,
            cached or 0,
        )

    def generate(self, spec: PromptSpec, temperature: float = 0.7, **kwargs) -> str:
        try:
            max_tokens = kwargs.pop("max_tokens", DEFAULT_MAX_TOKENS)
            create_kwargs: dict = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": spec.user}],
            }
            if self._supports_temperature():
                create_kwargs["temperature"] = temperature
            if spec.system:
                create_kwargs["system"] = spec.system
            response = self.client.messages.create(**create_kwargs, **kwargs)
            return response.content[0].text
        except Exception as e:
            logger.error(f"Anthropic generation failed: {e}")
            raise

    def generate_json(
        self,
        spec: PromptSpec,
        schema: dict | None = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        validator: Callable[[dict], None] | None = None,
        **kwargs,
    ) -> dict:
        # Hoisted out of the retry loop so a caller's max_tokens survives across
        # attempts (and gives a base to double on truncation).
        max_tokens = kwargs.pop("max_tokens", DEFAULT_MAX_TOKENS)
        json_instruction = "" if schema else "\n\nYou must respond with valid JSON only."
        # Strip constraints Anthropic structured outputs reject once, up front
        # (they remain enforced client-side via the ``validator`` callback).
        safe_schema = make_schema_anthropic_safe(schema) if schema else None
        truncation_retry_used = False
        feedback: str | None = None
        json_attempt = 0

        while True:
            user_text = spec.user + json_instruction
            if feedback:
                user_text += "\n\n" + feedback

            create_kwargs: dict = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": user_text}],
            }
            if self._supports_temperature():
                create_kwargs["temperature"] = temperature
            if spec.system:
                create_kwargs["system"] = spec.system
            if safe_schema is not None:
                create_kwargs["output_config"] = {
                    "format": {"type": "json_schema", "schema": safe_schema}
                }

            api_start = time.time()
            try:
                response = self.client.messages.create(**create_kwargs, **kwargs)
            except Exception as e:
                logger.error(f"Anthropic JSON generation failed: {e}")
                raise
            self._log_usage(response, time.time() - api_start, "JSON")

            # Truncation: grow the budget once, then surface a typed error
            # rather than blind-retrying the identical request.
            if getattr(response, "stop_reason", None) == "max_tokens":
                if not truncation_retry_used:
                    truncation_retry_used = True
                    max_tokens *= 2
                    feedback = None
                    logger.warning(
                        "Anthropic output truncated; retrying with max_tokens=%d",
                        max_tokens,
                    )
                    continue
                raise LLMTruncatedError(
                    "Anthropic output truncated even after doubling max_tokens "
                    f"to {max_tokens}"
                )

            content = response.content[0].text
            json_attempt += 1
            try:
                result = json.loads(content)
                if validator is not None:
                    validator(result)
                logger.debug("Successfully generated JSON on attempt %d", json_attempt)
                return result
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    "Anthropic JSON invalid on attempt %d: %s", json_attempt, e
                )
                if json_attempt >= max_retries:
                    raise ValueError(
                        f"Failed to generate valid JSON after {max_retries} attempts: {e}"
                    ) from e
                feedback = build_retry_feedback(content, str(e))

    def generate_json_from_pdf(
        self,
        pdf_bytes: bytes,
        prompt: str,
        schema: dict | None = None,
        *,
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> dict:
        """Extract structured JSON from a PDF via Anthropic's document block.

        Supported on Claude Sonnet 3.5+ models.
        """
        pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")

        message_content: list[dict] = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": pdf_b64,
                },
            },
            {"type": "text", "text": prompt},
        ]

        create_kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": message_content}],
        }
        if self._supports_temperature():
            create_kwargs["temperature"] = temperature
        if schema is not None:
            create_kwargs["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": make_schema_anthropic_safe(schema),
                }
            }

        logger.info(
            f"[TIMING] Starting Anthropic PDF extraction (model={self.model})"
        )
        api_start = time.time()
        response = self.client.messages.create(**create_kwargs)
        api_elapsed = time.time() - api_start
        logger.info(
            f"[TIMING] Anthropic PDF extraction completed in {api_elapsed:.2f}s"
        )

        text_parts = [
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        ]
        content = "".join(text_parts) if text_parts else response.content[0].text
        return json.loads(content)
