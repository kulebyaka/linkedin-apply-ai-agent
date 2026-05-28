"""LLM provider enum and abstract base client."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from .prompt_spec import PromptSpec


class LLMProvider(StrEnum):
    """Supported LLM providers."""

    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    GROK = "grok"
    ANTHROPIC = "anthropic"


class BaseLLMClient(ABC):
    """Base class for LLM provider clients."""

    # Override to True on subclasses that support native PDF document input.
    SUPPORTS_PDF_INPUT: bool = False

    def __init__(self, api_key: str, model: str, **kwargs):
        self.api_key = api_key
        self.model = model
        self.config = kwargs

    def generate_json_from_pdf(
        self,
        pdf_bytes: bytes,
        prompt: str,
        schema: dict | None = None,
        *,
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> dict:
        """Generate structured JSON from a native PDF document input.

        Override in providers whose APIs accept PDF document content blocks.
        Default raises so callers can react with a clear UX error.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support native PDF input"
        )

    @abstractmethod
    def generate(self, spec: PromptSpec, temperature: float = 0.7, **kwargs) -> str:
        """Generate text completion from a cache-aware prompt spec."""
        pass

    @abstractmethod
    def generate_json(
        self,
        spec: PromptSpec,
        schema: dict | None = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs,
    ) -> dict:
        """Generate structured JSON output from a cache-aware prompt spec."""
        pass


def basic_validate_json_schema(data, schema: dict) -> None:
    """Simplified schema validation used as a fallback when the provider
    cannot enforce schemas natively (DeepSeek).

    Production-quality validation would use the ``jsonschema`` library.
    """
    schema_type = schema.get("type")

    if schema_type == "object":
        if not isinstance(data, dict):
            raise ValueError(f"Expected object, got {type(data)}")
        for field in schema.get("required", []):
            if field not in data:
                raise ValueError(f"Missing required field: {field}")
    elif schema_type == "array":
        if not isinstance(data, list):
            raise ValueError(f"Expected array, got {type(data)}")
