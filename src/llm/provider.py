"""Re-export shim for the split LLM provider package.

The implementation is split across:
- ``base.py`` — LLMProvider enum + BaseLLMClient ABC + ``provider_supports_pdf``
- ``providers/instructor_client.py`` — the single Instructor + LiteLLM client
- ``prompt_spec.py`` — the cache-aware PromptSpec

Existing imports such as ``from src.llm.provider import InstructorClient`` keep
working via the re-exports below.
"""

from .base import (
    BaseLLMClient,
    LLMProvider,
    provider_supports_pdf,
)
from .prompt_spec import PromptSpec
from .providers.instructor_client import InstructorClient, litellm_model

__all__ = [
    "BaseLLMClient",
    "InstructorClient",
    "LLMProvider",
    "PromptSpec",
    "litellm_model",
    "provider_supports_pdf",
]
