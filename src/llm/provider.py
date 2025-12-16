"""LLM provider abstraction for multi-model support"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from enum import Enum
import json
import logging

logger = logging.getLogger(__name__)


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
    def generate(self, prompt: str, temperature: float = 0.7, **kwargs) -> str:
        """
        Generate text completion

        Args:
            prompt: Input prompt
            temperature: Sampling temperature (0.0-2.0)
            **kwargs: Additional provider-specific parameters

        Returns:
            Generated text
        """
        pass

    @abstractmethod
    def generate_json(
        self,
        prompt: str,
        schema: Optional[Dict] = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs
    ) -> Dict:
        """
        Generate structured JSON output with schema validation

        Args:
            prompt: Input prompt
            schema: JSON schema for validation
            temperature: Sampling temperature (lower is more deterministic)
            max_retries: Number of retries for malformed JSON
            **kwargs: Additional provider-specific parameters

        Returns:
            Parsed JSON dictionary

        Raises:
            ValueError: If JSON parsing fails after retries
        """
        pass


class OpenAIClient(BaseLLMClient):
    """OpenAI provider client"""

    def __init__(self, api_key: str, model: str, **kwargs):
        super().__init__(api_key, model, **kwargs)
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key)
        except ImportError:
            raise ImportError(
                "OpenAI package not installed. Install with: pip install openai"
            )

    def generate(self, prompt: str, temperature: float = 0.7, **kwargs) -> str:
        """Generate text completion using OpenAI"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                **kwargs
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI generation failed: {e}")
            raise

    def generate_json(
        self,
        prompt: str,
        schema: Optional[Dict] = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs
    ) -> Dict:
        """Generate structured JSON output using OpenAI"""
        # Add JSON instruction to prompt
        json_prompt = f"{prompt}\n\nYou must respond with valid JSON only. Do not include any text before or after the JSON."

        for attempt in range(max_retries):
            try:
                # Use JSON mode if available (GPT-4 and newer models)
                response_format = {"type": "json_object"} if "gpt-4" in self.model or "gpt-3.5" in self.model else None

                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": json_prompt}],
                    temperature=temperature,
                    response_format=response_format,
                    **kwargs
                )

                content = response.choices[0].message.content

                # Parse JSON
                result = json.loads(content)

                # Basic schema validation if provided
                if schema:
                    self._validate_json_schema(result, schema)

                logger.debug(f"Successfully generated JSON on attempt {attempt + 1}")
                return result

            except json.JSONDecodeError as e:
                logger.warning(f"JSON parsing failed on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise ValueError(f"Failed to generate valid JSON after {max_retries} attempts")

            except Exception as e:
                logger.error(f"OpenAI JSON generation failed: {e}")
                raise

    def _validate_json_schema(self, data: Dict, schema: Dict):
        """Basic JSON schema validation"""
        # This is a simplified validation
        # For production, consider using jsonschema library
        schema_type = schema.get("type")

        if schema_type == "object":
            if not isinstance(data, dict):
                raise ValueError(f"Expected object, got {type(data)}")

            required = schema.get("required", [])
            for field in required:
                if field not in data:
                    raise ValueError(f"Missing required field: {field}")

        elif schema_type == "array":
            if not isinstance(data, list):
                raise ValueError(f"Expected array, got {type(data)}")


class DeepSeekClient(BaseLLMClient):
    """DeepSeek provider client (OpenAI-compatible API)"""

    def __init__(self, api_key: str, model: str, **kwargs):
        super().__init__(api_key, model, **kwargs)
        try:
            from openai import OpenAI
            # DeepSeek uses OpenAI-compatible API
            self.client = OpenAI(
                api_key=api_key,
                base_url="https://api.deepseek.com/v1"
            )
        except ImportError:
            raise ImportError(
                "OpenAI package not installed. Install with: pip install openai"
            )

    def generate(self, prompt: str, temperature: float = 0.7, **kwargs) -> str:
        """Generate text completion using DeepSeek"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                **kwargs
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"DeepSeek generation failed: {e}")
            raise

    def generate_json(
        self,
        prompt: str,
        schema: Optional[Dict] = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs
    ) -> Dict:
        """Generate structured JSON output using DeepSeek"""
        json_prompt = f"{prompt}\n\nYou must respond with valid JSON only. Do not include any text before or after the JSON."

        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": json_prompt}],
                    temperature=temperature,
                    **kwargs
                )

                content = response.choices[0].message.content
                result = json.loads(content)

                logger.debug(f"Successfully generated JSON on attempt {attempt + 1}")
                return result

            except json.JSONDecodeError as e:
                logger.warning(f"JSON parsing failed on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise ValueError(f"Failed to generate valid JSON after {max_retries} attempts")

            except Exception as e:
                logger.error(f"DeepSeek JSON generation failed: {e}")
                raise


class GrokClient(BaseLLMClient):
    """Grok (xAI) provider client (OpenAI-compatible API)"""

    def __init__(self, api_key: str, model: str, **kwargs):
        super().__init__(api_key, model, **kwargs)
        try:
            from openai import OpenAI
            # Grok uses OpenAI-compatible API
            self.client = OpenAI(
                api_key=api_key,
                base_url="https://api.x.ai/v1"
            )
        except ImportError:
            raise ImportError(
                "OpenAI package not installed. Install with: pip install openai"
            )

    def generate(self, prompt: str, temperature: float = 0.7, **kwargs) -> str:
        """Generate text completion using Grok"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                **kwargs
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Grok generation failed: {e}")
            raise

    def generate_json(
        self,
        prompt: str,
        schema: Optional[Dict] = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs
    ) -> Dict:
        """Generate structured JSON output using Grok"""
        json_prompt = f"{prompt}\n\nYou must respond with valid JSON only. Do not include any text before or after the JSON."

        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": json_prompt}],
                    temperature=temperature,
                    **kwargs
                )

                content = response.choices[0].message.content

                # Parse JSON
                result = json.loads(content)

                # Basic schema validation if provided
                if schema:
                    OpenAIClient._validate_json_schema(None, result, schema)

                logger.debug(f"Successfully generated JSON on attempt {attempt + 1}")
                return result

            except json.JSONDecodeError as e:
                logger.warning(f"JSON parsing failed on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise ValueError(f"Failed to generate valid JSON after {max_retries} attempts")

            except Exception as e:
                logger.error(f"Grok JSON generation failed: {e}")
                raise


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude provider client"""

    def __init__(self, api_key: str, model: str, **kwargs):
        super().__init__(api_key, model, **kwargs)
        try:
            from anthropic import Anthropic
            self.client = Anthropic(api_key=api_key)
        except ImportError:
            raise ImportError(
                "Anthropic package not installed. Install with: pip install anthropic"
            )

    def generate(self, prompt: str, temperature: float = 0.7, **kwargs) -> str:
        """Generate text completion using Anthropic Claude"""
        try:
            max_tokens = kwargs.pop("max_tokens", 4096)
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
                **kwargs
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"Anthropic generation failed: {e}")
            raise

    def generate_json(
        self,
        prompt: str,
        schema: Optional[Dict] = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs
    ) -> Dict:
        """Generate structured JSON output using Anthropic Claude"""
        json_prompt = f"{prompt}\n\nYou must respond with valid JSON only. Do not include any text before or after the JSON."

        for attempt in range(max_retries):
            try:
                max_tokens = kwargs.pop("max_tokens", 4096)
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=[{"role": "user", "content": json_prompt}],
                    **kwargs
                )

                content = response.content[0].text
                result = json.loads(content)

                logger.debug(f"Successfully generated JSON on attempt {attempt + 1}")
                return result

            except json.JSONDecodeError as e:
                logger.warning(f"JSON parsing failed on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise ValueError(f"Failed to generate valid JSON after {max_retries} attempts")

            except Exception as e:
                logger.error(f"Anthropic JSON generation failed: {e}")
                raise


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
