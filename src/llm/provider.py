"""
LLM provider abstraction for multi-model support

This module implements native structured output support for multiple LLM providers,
eliminating reliance on prompt-based JSON generation for improved reliability.

Structured Output Capabilities by Provider:
-------------------------------------------

1. OpenAI (GPT-4, GPT-3.5-turbo, etc.)
   - Supports: Strict JSON Schema enforcement via response_format
   - Method: {"type": "json_schema", "json_schema": {...}} with "strict": True
   - Reliability: 100% schema adherence (per OpenAI benchmarks)
   - Models: GPT-4o and newer models

2. Anthropic (Claude Sonnet 4.5, Opus 4.1, Haiku 4.5)
   - Supports: Strict JSON Schema enforcement via output_format
   - Method: output_format with "strict": True, requires beta header
   - Beta Header: "anthropic-beta: structured-outputs-2025-11-13"
   - Reliability: Guaranteed schema compliance using constrained decoding
   - Released: December 4, 2025 (public beta)

3. Grok (xAI - grok-2-1212 and newer)
   - Supports: Strict JSON Schema enforcement via response_format
   - Method: {"type": "json_schema", "json_schema": {...}} (OpenAI-compatible)
   - Reliability: Schema-guaranteed outputs
   - Models: All models after grok-2-1212

4. DeepSeek (OpenAI-compatible API)
   - Supports: json_object mode only (NOT strict JSON Schema)
   - Method: {"type": "json_object"} with JSON keyword in prompt
   - Reliability: Improved JSON generation but requires manual validation
   - Limitation: Cannot enforce strict schema compliance natively

All providers use native structured outputs when a schema is provided to reduce
JSON parsing errors and improve reliability.
"""

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
        """Generate structured JSON output using OpenAI with native JSON Schema support"""
        for attempt in range(max_retries):
            try:
                # Use strict JSON Schema mode for guaranteed valid output
                if schema:
                    response_format = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "response",
                            "strict": True,
                            "schema": schema
                        }
                    }
                    # No need for JSON instruction in prompt with strict mode
                    user_prompt = prompt
                else:
                    # Fallback to json_object mode without schema
                    response_format = {"type": "json_object"}
                    user_prompt = f"{prompt}\n\nYou must respond with valid JSON only."

                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": user_prompt}],
                    temperature=temperature,
                    response_format=response_format,
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
        """
        Generate structured JSON output using DeepSeek

        Note: DeepSeek supports json_object mode but not strict JSON Schema enforcement.
        Schema validation happens after generation if provided.
        """
        # DeepSeek requires "json" keyword in prompt when using json_object mode
        json_prompt = f"{prompt}\n\nYou must respond with valid JSON only. Do not include any text before or after the JSON."

        for attempt in range(max_retries):
            try:
                # Use json_object mode for more reliable JSON generation
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": json_prompt}],
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    **kwargs
                )

                content = response.choices[0].message.content
                result = json.loads(content)

                # Manual schema validation if provided (DeepSeek doesn't support strict schemas)
                if schema:
                    self._validate_json_schema(result, schema)

                logger.debug(f"Successfully generated JSON on attempt {attempt + 1}")
                return result

            except json.JSONDecodeError as e:
                logger.warning(f"JSON parsing failed on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise ValueError(f"Failed to generate valid JSON after {max_retries} attempts")

            except Exception as e:
                logger.error(f"DeepSeek JSON generation failed: {e}")
                raise

    def _validate_json_schema(self, data: Dict, schema: Dict):
        """Basic JSON schema validation (same as OpenAI implementation)"""
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
        """Generate structured JSON output using Grok with native JSON Schema support"""
        for attempt in range(max_retries):
            try:
                # Use strict JSON Schema mode for guaranteed valid output
                if schema:
                    response_format = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "response",
                            "strict": True,
                            "schema": schema
                        }
                    }
                    # No need for JSON instruction in prompt with strict mode
                    user_prompt = prompt
                else:
                    # Fallback to json_object mode without schema
                    response_format = {"type": "json_object"}
                    user_prompt = f"{prompt}\n\nYou must respond with valid JSON only."

                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": user_prompt}],
                    temperature=temperature,
                    response_format=response_format,
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
                logger.error(f"Grok JSON generation failed: {e}")
                raise


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude provider client"""

    def __init__(self, api_key: str, model: str, **kwargs):
        super().__init__(api_key, model, **kwargs)
        try:
            from anthropic import Anthropic
            # Enable structured outputs beta header
            self.client = Anthropic(
                api_key=api_key,
                default_headers={"anthropic-beta": "structured-outputs-2025-11-13"}
            )
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
        """Generate structured JSON output using Anthropic Claude with native structured outputs"""
        for attempt in range(max_retries):
            try:
                max_tokens = kwargs.pop("max_tokens", 4096)

                # Use structured outputs with schema if provided
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
                                "schema": schema
                            }
                        },
                        **kwargs
                    )
                else:
                    # Fallback to prompt-based JSON without schema
                    json_prompt = f"{prompt}\n\nYou must respond with valid JSON only."
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
