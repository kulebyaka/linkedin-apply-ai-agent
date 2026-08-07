"""LLM provider enum and abstract base client."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum
from typing import Any, TypeVar, overload

from pydantic import BaseModel

from .prompt_spec import PromptSpec

_ResponseModelT = TypeVar("_ResponseModelT", bound=BaseModel)


class LLMProvider(StrEnum):
    """Supported LLM providers."""

    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    GROK = "grok"
    ANTHROPIC = "anthropic"


#: Providers whose models can accept native PDF document input. LiteLLM 1.93.0
#: has no ``supports_pdf_input`` lookup, so capability is tracked here (the
#: OpenAI GPT-4 family and Anthropic Claude accept PDFs; Grok/DeepSeek do not).
_PDF_CAPABLE_PROVIDERS: frozenset[LLMProvider] = frozenset(
    {LLMProvider.OPENAI, LLMProvider.ANTHROPIC}
)


def provider_supports_pdf(provider: LLMProvider) -> bool:
    """Return True if ``provider`` can accept native PDF document input."""
    return provider in _PDF_CAPABLE_PROVIDERS


class BaseLLMClient(ABC):
    """Base class for LLM provider clients."""

    # Override to True on subclasses that support native PDF document input.
    SUPPORTS_PDF_INPUT: bool = False

    def __init__(self, api_key: str, model: str, **kwargs):
        self.api_key = api_key
        self.model = model
        self.config = kwargs

    def reasoning_kwargs(self, effort: str = "low", *, structured: bool) -> dict[str, Any]:
        """Completion kwargs that request ``effort``-level reasoning, or ``{}``.

        Call sites that want reasoning ask the client for the kwargs rather than
        setting ``reasoning_effort`` themselves: whether a model accepts the
        param — and on which code path — is provider- and model-specific.
        ``structured=True`` means the call goes through ``generate_json``,
        whose forced tool call more models reject than the plain-text path.

        The returned dict may also override ``temperature``, because some
        providers accept reasoning only at a fixed sampling temperature. Merge
        it over the call's own kwargs (last wins) rather than passing
        ``temperature`` separately, or Python raises on the duplicate keyword.

        The base implementation returns ``{}`` — no reasoning — so provider
        clients opt in.
        """
        return {}

    def generate_json_from_pdf(
        self,
        pdf_bytes: bytes,
        prompt: str,
        response_model: type[BaseModel] | None = None,
        schema: dict | None = None,
        *,
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> dict | BaseModel:
        """Generate structured JSON from a native PDF document input.

        Override in providers whose APIs accept PDF document content blocks.
        Default raises so callers can react with a clear UX error.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support native PDF input")

    @abstractmethod
    def generate(self, spec: PromptSpec, temperature: float = 0.7, **kwargs) -> str:
        """Generate text completion from a cache-aware prompt spec."""
        pass

    @overload
    def generate_json(
        self,
        spec: PromptSpec,
        response_model: type[_ResponseModelT],
        schema: dict | None = ...,
        temperature: float = ...,
        max_retries: int = ...,
        validator: Callable[[dict], None] | None = ...,
        **kwargs: Any,
    ) -> _ResponseModelT: ...

    @overload
    def generate_json(
        self,
        spec: PromptSpec,
        response_model: None = ...,
        schema: dict | None = ...,
        temperature: float = ...,
        max_retries: int = ...,
        validator: Callable[[dict], None] | None = ...,
        **kwargs: Any,
    ) -> dict: ...

    @abstractmethod
    def generate_json(
        self,
        spec: PromptSpec,
        response_model: type[BaseModel] | None = None,
        schema: dict | None = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        validator: Callable[[dict], None] | None = None,
        **kwargs: Any,
    ) -> dict | BaseModel:
        """Generate structured JSON output from a cache-aware prompt spec.

        ``response_model`` is the preferred way to request structured output:
        a Pydantic model class the result is validated against and returned as.
        ``schema`` (a raw JSON-schema dict) is retained for ad-hoc call sites.

        ``validator`` is an optional callable that receives the parsed dict
        and raises ``ValueError`` on a semantic problem the schema can't
        express.
        """
        pass
