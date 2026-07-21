"""LLM provider enum and abstract base client."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum

from .prompt_spec import PromptSpec

#: Fallback output-token budget used when a caller passes no ``max_tokens`` and
#: a truncation retry needs a value to double.
DEFAULT_MAX_TOKENS = 4096


class LLMTruncatedError(Exception):
    """Raised when model output was cut off by the token limit.

    ``generate_json`` retries a truncated response exactly once with
    ``max_tokens`` doubled; if it truncates again this is raised rather than
    blind-retrying the identical request (which can never succeed).
    """


def build_retry_feedback(previous_output: str, error: str, *, limit: int = 1000) -> str:
    """Build the feedback block appended to a retry's user message.

    Includes the previous (invalid) output truncated to ``limit`` chars plus
    the specific error, so the retry is corrective rather than a blind repeat.
    """
    prev = (previous_output or "")[:limit]
    return (
        "Your previous response was invalid.\n"
        f"Previous response (truncated):\n{prev}\n"
        f"Error: {error}\n"
        "Return corrected JSON matching the schema."
    )


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
        validator: Callable[[dict], None] | None = None,
        **kwargs,
    ) -> dict:
        """Generate structured JSON output from a cache-aware prompt spec.

        ``validator`` is an optional callable that receives the parsed dict
        and raises ``ValueError`` on a semantic problem the JSON schema can't
        express; the raised message feeds the retry-with-feedback loop.
        """
        pass


def basic_validate_json_schema(data, schema: dict) -> None:
    """Validate ``data`` against ``schema`` for providers without native
    schema enforcement (DeepSeek).

    Full JSON Schema validation via the ``jsonschema`` library, including
    nested objects/arrays and constraints. ``jsonschema.ValidationError`` is
    wrapped as ``ValueError`` so the ``generate_json`` retry-with-feedback loop
    picks up the message.
    """
    import jsonschema

    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as e:
        # e.message is the concise reason; the full path helps the model fix it.
        location = "/".join(str(p) for p in e.absolute_path)
        detail = f"{e.message} (at '{location}')" if location else e.message
        raise ValueError(f"Schema validation failed: {detail}") from e
