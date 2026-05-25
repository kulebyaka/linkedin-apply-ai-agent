"""OpenAI provider client."""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import ClassVar

from ..schema_strict import make_schema_strict
from ._openai_compatible import OpenAICompatibleClient

logger = logging.getLogger(__name__)


class OpenAIClient(OpenAICompatibleClient):
    """OpenAI client — strict JSON schema enforcement + native PDF input."""

    SUPPORTS_PDF_INPUT = True
    base_url: ClassVar[str | None] = None
    supports_strict_schema: ClassVar[bool] = True
    provider_label: ClassVar[str] = "OpenAI"
    # Reasoning models that only support temperature=1 (default).
    reasoning_model_prefixes: ClassVar[tuple[str, ...]] = (
        "o1", "o3", "o4-mini", "gpt-5-mini",
    )

    def generate_json_from_pdf(
        self,
        pdf_bytes: bytes,
        prompt: str,
        schema: dict | None = None,
        *,
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> dict:
        """Extract structured JSON from a PDF via OpenAI's Responses API.

        Uses input_file content blocks to send the PDF natively. Requires a
        GPT-4 family model with vision/document support.
        """
        pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")

        strict_schema = make_schema_strict(schema) if schema else None
        if strict_schema is not None:
            text_param = {
                "format": {
                    "type": "json_schema",
                    "name": "response",
                    "strict": True,
                    "schema": strict_schema,
                }
            }
        else:
            text_param = {"format": {"type": "json_object"}}

        input_payload = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "filename": "resume.pdf",
                        "file_data": f"data:application/pdf;base64,{pdf_b64}",
                    },
                    {"type": "input_text", "text": prompt},
                ],
            }
        ]

        logger.info(
            f"[TIMING] Starting OpenAI PDF extraction (model={self.model})"
        )
        api_start = time.time()
        response = self.client.responses.create(
            model=self.model,
            input=input_payload,
            text=text_param,
            max_output_tokens=max_tokens,
        )
        api_elapsed = time.time() - api_start
        logger.info(
            f"[TIMING] OpenAI PDF extraction completed in {api_elapsed:.2f}s"
        )

        content = response.output_text
        result = json.loads(content)
        if (
            schema is not None
            and schema.get("type") == "array"
            and isinstance(result, dict)
            and "items" in result
        ):
            result = result["items"]
        return result
