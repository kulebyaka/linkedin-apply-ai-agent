"""LLM provider abstraction for multi-model support"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from enum import Enum


class LLMProvider(str, Enum):
    """Supported LLM providers"""
    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    GROK = "grok"
    ANTHROPIC = "anthropic"


class BaseLLMClient(ABC):
    """Base class for LLM provider clients"""

    def __init__(self, api_key: str, model: str, **kwargs):
        self.api_key = api_key
        self.model = model
        self.config = kwargs

    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate text completion"""
        pass

    @abstractmethod
    def generate_json(self, prompt: str, schema: Optional[Dict] = None, **kwargs) -> Dict:
        """Generate structured JSON output"""
        pass


class OpenAIClient(BaseLLMClient):
    """OpenAI provider client"""

    def generate(self, prompt: str, **kwargs) -> str:
        # TODO: Implement OpenAI API call
        raise NotImplementedError

    def generate_json(self, prompt: str, schema: Optional[Dict] = None, **kwargs) -> Dict:
        # TODO: Implement OpenAI JSON generation
        raise NotImplementedError


class DeepSeekClient(BaseLLMClient):
    """DeepSeek provider client"""

    def generate(self, prompt: str, **kwargs) -> str:
        # TODO: Implement DeepSeek API call
        raise NotImplementedError

    def generate_json(self, prompt: str, schema: Optional[Dict] = None, **kwargs) -> Dict:
        # TODO: Implement DeepSeek JSON generation
        raise NotImplementedError


class GrokClient(BaseLLMClient):
    """Grok provider client"""

    def generate(self, prompt: str, **kwargs) -> str:
        # TODO: Implement Grok API call
        raise NotImplementedError

    def generate_json(self, prompt: str, schema: Optional[Dict] = None, **kwargs) -> Dict:
        # TODO: Implement Grok JSON generation
        raise NotImplementedError


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude provider client"""

    def generate(self, prompt: str, **kwargs) -> str:
        # TODO: Implement Anthropic API call
        raise NotImplementedError

    def generate_json(self, prompt: str, schema: Optional[Dict] = None, **kwargs) -> Dict:
        # TODO: Implement Anthropic JSON generation
        raise NotImplementedError


class LLMClientFactory:
    """Factory for creating LLM clients"""

    _clients = {
        LLMProvider.OPENAI: OpenAIClient,
        LLMProvider.DEEPSEEK: DeepSeekClient,
        LLMProvider.GROK: GrokClient,
        LLMProvider.ANTHROPIC: AnthropicClient,
    }

    @classmethod
    def create(cls, provider: LLMProvider, api_key: str, model: str, **kwargs) -> BaseLLMClient:
        """Create an LLM client for the specified provider"""
        client_class = cls._clients.get(provider)
        if not client_class:
            raise ValueError(f"Unsupported LLM provider: {provider}")
        return client_class(api_key, model, **kwargs)
