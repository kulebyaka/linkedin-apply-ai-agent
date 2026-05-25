"""Re-export shim for the split LLM provider package.

The implementation has been split across:
- ``base.py`` — LLMProvider enum + BaseLLMClient ABC
- ``schema_strict.py`` — make_schema_strict for OpenAI/Grok strict JSON
- ``providers/openai.py`` / ``grok.py`` / ``deepseek.py`` / ``anthropic.py``
- ``factory.py`` — LLMClientFactory

Existing imports such as ``from src.llm.provider import OpenAIClient`` keep
working via the re-exports below.
"""

from .base import BaseLLMClient, LLMProvider, basic_validate_json_schema
from .factory import LLMClientFactory
from .providers import AnthropicClient, DeepSeekClient, GrokClient, OpenAIClient
from .schema_strict import make_schema_strict

__all__ = [
    "AnthropicClient",
    "BaseLLMClient",
    "DeepSeekClient",
    "GrokClient",
    "LLMClientFactory",
    "LLMProvider",
    "OpenAIClient",
    "basic_validate_json_schema",
    "make_schema_strict",
]
