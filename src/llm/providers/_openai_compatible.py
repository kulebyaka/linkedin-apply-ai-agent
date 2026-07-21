"""Shared OpenAI-compatible client base used by OpenAI, Grok, and DeepSeek.

All three providers expose the OpenAI Chat Completions API shape, so the
request/response handling is identical; the differences fit on this contract:

- ``base_url``: where to point the OpenAI Python SDK
- ``supports_strict_schema``: True for OpenAI/Grok (native JSON Schema mode),
  False for DeepSeek (falls back to ``json_object`` + post-hoc validation)
- ``provider_label``: log prefix so messages aren't all tagged "OpenAI"
- ``reasoning_model_prefixes``: optional list of model name prefixes that
  reject custom temperature (OpenAI reasoning models)
"""

from __future__ import annotations

import json
import logging
import time
from typing import ClassVar

from collections.abc import Callable

from ..base import (
    DEFAULT_MAX_TOKENS,
    BaseLLMClient,
    LLMTruncatedError,
    basic_validate_json_schema,
    build_retry_feedback,
)
from ..prompt_spec import PromptSpec
from ..schema_strict import make_schema_strict

logger = logging.getLogger(__name__)


class OpenAICompatibleClient(BaseLLMClient):
    """Base for clients that speak the OpenAI Chat Completions API.

    Subclasses set the class attributes below; behaviour follows automatically.
    """

    base_url: ClassVar[str | None] = None
    supports_strict_schema: ClassVar[bool] = False
    provider_label: ClassVar[str] = "OpenAI-compatible"
    reasoning_model_prefixes: ClassVar[tuple[str, ...]] = ()

    def __init__(self, api_key: str, model: str, **kwargs):
        super().__init__(api_key, model, **kwargs)
        try:
            from openai import OpenAI
        except ImportError as err:
            raise ImportError(
                "OpenAI package not installed. Install with: pip install openai"
            ) from err

        client_kwargs: dict = {"api_key": api_key}
        if self.base_url is not None:
            client_kwargs["base_url"] = self.base_url
        self.client = OpenAI(**client_kwargs)

    def _is_reasoning_model(self) -> bool:
        return any(
            self.model.startswith(prefix) for prefix in self.reasoning_model_prefixes
        )

    def _build_messages(self, spec: PromptSpec) -> list[dict]:
        messages: list[dict] = []
        if spec.system:
            messages.append({"role": "system", "content": spec.system})
        messages.append({"role": "user", "content": spec.user})
        return messages

    def _apply_cache_key(self, api_kwargs: dict, spec: PromptSpec) -> None:
        if spec.cache_key:
            api_kwargs["prompt_cache_key"] = spec.cache_key

    def _log_usage(self, response, elapsed: float, spec: PromptSpec, kind: str) -> None:
        usage = getattr(response, "usage", None)
        details = getattr(usage, "prompt_tokens_details", None) if usage else None
        cached = getattr(details, "cached_tokens", 0) if details else 0
        total = getattr(usage, "prompt_tokens", 0) if usage else 0
        logger.info(
            "[TIMING] %s %s call completed in %.2fs (cached_tokens=%d/%d key=%s)",
            self.provider_label,
            kind,
            elapsed,
            cached or 0,
            total,
            spec.cache_key or "-",
        )

    def generate(self, spec: PromptSpec, temperature: float = 0.7, **kwargs) -> str:
        try:
            api_kwargs = dict(kwargs)
            if not self._is_reasoning_model():
                api_kwargs["temperature"] = temperature
            self._apply_cache_key(api_kwargs, spec)

            api_start = time.time()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self._build_messages(spec),
                **api_kwargs,
            )
            elapsed = time.time() - api_start
            self._log_usage(response, elapsed, spec, "text")
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"{self.provider_label} generation failed: {e}")
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
        was_array_schema = bool(schema and schema.get("type") == "array")
        use_strict = schema is not None and self.supports_strict_schema

        if use_strict:
            assert schema is not None  # narrowed by use_strict
            strict_schema = make_schema_strict(schema)
            response_format: dict = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": True,
                    "schema": strict_schema,
                },
            }
            # Strict JSON schema mode on OpenAI/Grok does NOT support a custom
            # temperature — only the default (1.0).
            json_instruction = ""
        else:
            response_format = {"type": "json_object"}
            # json_object mode needs an explicit "respond with JSON" hint,
            # appended to the user message so the cacheable prefix is unaffected.
            json_instruction = "\n\nYou must respond with valid JSON only."

        # Pop once so a caller's max_tokens survives across retries (and gives a
        # base to double on truncation).
        max_tokens = kwargs.pop("max_tokens", None)
        truncation_retry_used = False
        feedback: str | None = None
        json_attempt = 0

        while True:
            user_text = spec.user + json_instruction
            if feedback:
                user_text += "\n\n" + feedback
            call_spec = PromptSpec(
                system=spec.system, user=user_text, cache_key=spec.cache_key
            )

            api_kwargs = kwargs.copy()
            if max_tokens is not None:
                api_kwargs["max_tokens"] = max_tokens
            if not use_strict and not self._is_reasoning_model():
                api_kwargs["temperature"] = temperature
            self._apply_cache_key(api_kwargs, call_spec)

            logger.info(
                "[TIMING] Starting %s JSON call (model=%s, attempt=%d, key=%s)",
                self.provider_label,
                self.model,
                json_attempt + 1,
                call_spec.cache_key or "-",
            )
            api_start = time.time()
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self._build_messages(call_spec),
                    response_format=response_format,
                    **api_kwargs,
                )
            except Exception as e:
                logger.error(f"{self.provider_label} JSON generation failed: {e}")
                raise
            elapsed = time.time() - api_start
            self._log_usage(response, elapsed, call_spec, "JSON")

            choice = response.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)

            # Truncation: identical retries can't fix it — grow the budget once,
            # then surface a typed error.
            if finish_reason == "length":
                if not truncation_retry_used:
                    truncation_retry_used = True
                    max_tokens = (max_tokens or DEFAULT_MAX_TOKENS) * 2
                    feedback = None
                    logger.warning(
                        "%s output truncated; retrying with max_tokens=%d",
                        self.provider_label,
                        max_tokens,
                    )
                    continue
                raise LLMTruncatedError(
                    f"{self.provider_label} output truncated even after doubling "
                    f"max_tokens to {max_tokens}"
                )

            content = choice.message.content
            json_attempt += 1
            try:
                result = json.loads(content)
                if was_array_schema and isinstance(result, dict) and "items" in result:
                    result = result["items"]
                if schema and not self.supports_strict_schema:
                    basic_validate_json_schema(result, schema)
                if validator is not None:
                    validator(result)
                logger.debug("Successfully generated JSON on attempt %d", json_attempt)
                return result
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    "%s JSON invalid on attempt %d: %s",
                    self.provider_label,
                    json_attempt,
                    e,
                )
                if json_attempt >= max_retries:
                    raise ValueError(
                        f"Failed to generate valid JSON after {max_retries} attempts: {e}"
                    ) from e
                feedback = build_retry_feedback(content, str(e))
