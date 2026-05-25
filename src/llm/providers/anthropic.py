"""Anthropic Claude provider client."""

from __future__ import annotations

import base64
import json
import logging
import time

from ..base import BaseLLMClient

logger = logging.getLogger(__name__)


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude client — native structured outputs (beta) + PDF input."""

    SUPPORTS_PDF_INPUT = True

    def __init__(self, api_key: str, model: str, **kwargs):
        super().__init__(api_key, model, **kwargs)
        try:
            from anthropic import Anthropic
        except ImportError as err:
            raise ImportError(
                "Anthropic package not installed. Install with: pip install anthropic"
            ) from err

        # Beta header is required for structured-outputs JSON schema mode.
        self.client = Anthropic(
            api_key=api_key,
            default_headers={"anthropic-beta": "structured-outputs-2025-11-13"},
        )

    def generate(self, prompt: str, temperature: float = 0.7, **kwargs) -> str:
        try:
            max_tokens = kwargs.pop("max_tokens", 4096)
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"Anthropic generation failed: {e}")
            raise

    def generate_json(
        self,
        prompt: str,
        schema: dict | None = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs,
    ) -> dict:
        for attempt in range(max_retries):
            try:
                max_tokens = kwargs.pop("max_tokens", 4096)

                if schema:
                    response = self.client.messages.create(
                        model=self.model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        messages=[{"role": "user", "content": prompt}],
                        output_format={
                            "type": "json_schema",
                            "json_schema": {
                                "name": "response",
                                "strict": True,
                                "schema": schema,
                            },
                        },
                        **kwargs,
                    )
                else:
                    json_prompt = f"{prompt}\n\nYou must respond with valid JSON only."
                    response = self.client.messages.create(
                        model=self.model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        messages=[{"role": "user", "content": json_prompt}],
                        **kwargs,
                    )

                content = response.content[0].text
                result = json.loads(content)

                logger.debug(f"Successfully generated JSON on attempt {attempt + 1}")
                return result

            except json.JSONDecodeError as e:
                logger.warning(f"JSON parsing failed on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise ValueError(
                        f"Failed to generate valid JSON after {max_retries} attempts"
                    ) from e

            except Exception as e:
                logger.error(f"Anthropic JSON generation failed: {e}")
                raise

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
            "temperature": temperature,
            "messages": [{"role": "user", "content": message_content}],
        }
        if schema is not None:
            create_kwargs["output_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": schema},
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
