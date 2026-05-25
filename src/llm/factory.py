"""Factory for instantiating provider-specific LLM clients."""

from __future__ import annotations

from .base import BaseLLMClient, LLMProvider
from .providers import AnthropicClient, DeepSeekClient, GrokClient, OpenAIClient


class LLMClientFactory:
    """Factory for creating LLM clients."""

    _clients: dict[LLMProvider, type[BaseLLMClient]] = {
        LLMProvider.OPENAI: OpenAIClient,
        LLMProvider.DEEPSEEK: DeepSeekClient,
        LLMProvider.GROK: GrokClient,
        LLMProvider.ANTHROPIC: AnthropicClient,
    }

    @classmethod
    def create(
        cls,
        provider: LLMProvider,
        api_key: str,
        model: str,
        **kwargs,
    ) -> BaseLLMClient:
        client_class = cls._clients.get(provider)
        if not client_class:
            raise ValueError(f"Unsupported LLM provider: {provider}")
        return client_class(api_key, model, **kwargs)

    @classmethod
    def supports_pdf(cls, provider: LLMProvider) -> bool:
        client_class = cls._clients.get(provider)
        return bool(client_class and client_class.SUPPORTS_PDF_INPUT)
