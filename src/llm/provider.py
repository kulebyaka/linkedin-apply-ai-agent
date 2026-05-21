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

import base64
import json
import logging
import time
from abc import ABC, abstractmethod
from enum import StrEnum

logger = logging.getLogger(__name__)


class LLMProvider(StrEnum):
    """Supported LLM providers"""

    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    GROK = "grok"
    ANTHROPIC = "anthropic"


class BaseLLMClient(ABC):
    """Base class for LLM provider clients"""

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
        schema: dict | None = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs,
    ) -> dict:
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

    SUPPORTS_PDF_INPUT = True

    # Reasoning models that only support temperature=1 (default)
    _REASONING_MODELS = ("o1", "o3", "o4-mini", "gpt-5-mini")

    def __init__(self, api_key: str, model: str, **kwargs):
        super().__init__(api_key, model, **kwargs)
        try:
            from openai import OpenAI

            self.client = OpenAI(api_key=api_key)
        except ImportError as err:
            raise ImportError("OpenAI package not installed. Install with: pip install openai") from err

    def _is_reasoning_model(self) -> bool:
        return any(self.model.startswith(prefix) for prefix in self._REASONING_MODELS)

    def generate(self, prompt: str, temperature: float = 0.7, **kwargs) -> str:
        """Generate text completion using OpenAI"""
        try:
            api_kwargs = dict(kwargs)
            if not self._is_reasoning_model():
                api_kwargs["temperature"] = temperature
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                **api_kwargs,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI generation failed: {e}")
            raise

    def generate_json(
        self,
        prompt: str,
        schema: dict | None = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs,
    ) -> dict:
        """Generate structured JSON output using OpenAI with native JSON Schema support"""
        for attempt in range(max_retries):
            try:
                # Track if we wrapped an array schema
                was_array_schema = schema and schema.get("type") == "array"

                # Use strict JSON Schema mode for guaranteed valid output
                if schema:
                    # OpenAI strict mode requires additionalProperties: false for all objects
                    strict_schema = self._make_schema_strict(schema)

                    response_format = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "response",
                            "strict": True,
                            "schema": strict_schema,
                        },
                    }
                    # No need for JSON instruction in prompt with strict mode
                    user_prompt = prompt
                    # NOTE: OpenAI strict JSON schema mode does NOT support custom temperature
                    # It only allows the default temperature (1.0)
                    # See: https://platform.openai.com/docs/guides/structured-outputs
                    api_kwargs = kwargs.copy()  # Don't pass temperature with strict mode
                else:
                    # Fallback to json_object mode without schema
                    response_format = {"type": "json_object"}
                    user_prompt = f"{prompt}\n\nYou must respond with valid JSON only."
                    api_kwargs = kwargs.copy()
                    if not self._is_reasoning_model():
                        api_kwargs["temperature"] = temperature

                logger.info(
                    f"[TIMING] Starting OpenAI API call (model={self.model}, attempt={attempt + 1})"
                )
                api_start = time.time()
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": user_prompt}],
                    response_format=response_format,
                    **api_kwargs,
                )
                api_elapsed = time.time() - api_start
                logger.info(f"[TIMING] OpenAI API call completed in {api_elapsed:.2f}s")

                content = response.choices[0].message.content
                result = json.loads(content)

                # If we wrapped an array schema, unwrap the response
                if was_array_schema and isinstance(result, dict) and "items" in result:
                    result = result["items"]

                logger.debug(f"Successfully generated JSON on attempt {attempt + 1}")
                return result

            except json.JSONDecodeError as e:
                logger.warning(f"JSON parsing failed on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise ValueError(f"Failed to generate valid JSON after {max_retries} attempts") from e

            except Exception as e:
                logger.error(f"OpenAI JSON generation failed: {e}")
                raise

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

        strict_schema = self._make_schema_strict(schema) if schema else None
        text_param: dict = {}
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

    def _make_schema_strict(self, schema: dict) -> dict:
        """
        Make JSON schema compatible with OpenAI strict mode
        - Wraps top-level arrays in an object (OpenAI requires root to be object)
        - Adds additionalProperties: false to all object schemas
        - Ensures all properties are in the required array
        """
        import copy

        schema = copy.deepcopy(schema)

        # OpenAI strict mode requires root schema to be type: object
        # If schema is an array, wrap it in an object
        if schema.get("type") == "array":
            schema = {
                "type": "object",
                "properties": {"items": schema},
                "required": ["items"],
                "additionalProperties": False,
            }

        def make_strict_recursive(obj):
            if isinstance(obj, dict):
                # If this is an object type schema
                type_val = obj.get("type")
                is_object = type_val == "object" or (
                    isinstance(type_val, list) and "object" in type_val
                )

                if is_object:
                    # Add additionalProperties: false
                    if "additionalProperties" not in obj:
                        obj["additionalProperties"] = False

                    # OpenAI strict mode requires ALL properties to be in required array
                    if "properties" in obj:
                        all_props = list(obj["properties"].keys())
                        obj["required"] = all_props

                # Recursively process properties
                if "properties" in obj:
                    for prop_schema in obj["properties"].values():
                        make_strict_recursive(prop_schema)

                # Recursively process items (for arrays)
                if "items" in obj:
                    make_strict_recursive(obj["items"])

                # Recursively process nested schemas
                for key in ["anyOf", "oneOf", "allOf"]:
                    if key in obj:
                        for sub_schema in obj[key]:
                            make_strict_recursive(sub_schema)

                # Process $defs (definitions referenced by $ref)
                if "$defs" in obj:
                    for def_schema in obj["$defs"].values():
                        make_strict_recursive(def_schema)

        make_strict_recursive(schema)
        return schema

    def _validate_json_schema(self, data: dict, schema: dict):
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
            self.client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
        except ImportError as err:
            raise ImportError("OpenAI package not installed. Install with: pip install openai") from err

    def generate(self, prompt: str, temperature: float = 0.7, **kwargs) -> str:
        """Generate text completion using DeepSeek"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                **kwargs,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"DeepSeek generation failed: {e}")
            raise

    def generate_json(
        self,
        prompt: str,
        schema: dict | None = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs,
    ) -> dict:
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
                    **kwargs,
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
                    raise ValueError(f"Failed to generate valid JSON after {max_retries} attempts") from e

            except Exception as e:
                logger.error(f"DeepSeek JSON generation failed: {e}")
                raise

    def _validate_json_schema(self, data: dict, schema: dict):
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
            self.client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
        except ImportError as err:
            raise ImportError("OpenAI package not installed. Install with: pip install openai") from err

    def generate(self, prompt: str, temperature: float = 0.7, **kwargs) -> str:
        """Generate text completion using Grok"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                **kwargs,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Grok generation failed: {e}")
            raise

    def generate_json(
        self,
        prompt: str,
        schema: dict | None = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs,
    ) -> dict:
        """Generate structured JSON output using Grok with native JSON Schema support"""
        for attempt in range(max_retries):
            try:
                # Track if we wrapped an array schema
                was_array_schema = schema and schema.get("type") == "array"

                # Use strict JSON Schema mode for guaranteed valid output
                if schema:
                    # Grok strict mode requires additionalProperties: false for all objects (like OpenAI)
                    strict_schema = self._make_schema_strict(schema)

                    response_format = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "response",
                            "strict": True,
                            "schema": strict_schema,
                        },
                    }
                    # No need for JSON instruction in prompt with strict mode
                    user_prompt = prompt
                    # NOTE: Grok (like OpenAI) strict JSON schema mode does NOT support custom temperature
                    # It only allows the default temperature (1.0)
                    api_kwargs = kwargs.copy()  # Don't pass temperature with strict mode
                else:
                    # Fallback to json_object mode without schema
                    response_format = {"type": "json_object"}
                    user_prompt = f"{prompt}\n\nYou must respond with valid JSON only."
                    api_kwargs = {"temperature": temperature, **kwargs}

                logger.info(
                    f"[TIMING] Starting OpenAI API call (model={self.model}, attempt={attempt + 1})"
                )
                api_start = time.time()
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": user_prompt}],
                    response_format=response_format,
                    **api_kwargs,
                )
                api_elapsed = time.time() - api_start
                logger.info(f"[TIMING] OpenAI API call completed in {api_elapsed:.2f}s")

                content = response.choices[0].message.content
                result = json.loads(content)

                # If we wrapped an array schema, unwrap the response
                if was_array_schema and isinstance(result, dict) and "items" in result:
                    result = result["items"]

                logger.debug(f"Successfully generated JSON on attempt {attempt + 1}")
                return result

            except json.JSONDecodeError as e:
                logger.warning(f"JSON parsing failed on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise ValueError(f"Failed to generate valid JSON after {max_retries} attempts") from e

            except Exception as e:
                logger.error(f"Grok JSON generation failed: {e}")
                raise

    def _make_schema_strict(self, schema: dict) -> dict:
        """
        Make JSON schema compatible with Grok strict mode (same as OpenAI)
        - Wraps top-level arrays in an object (Grok requires root to be object)
        - Adds additionalProperties: false to all object schemas
        - Ensures all properties are in the required array
        """
        import copy

        schema = copy.deepcopy(schema)

        # Grok strict mode requires root schema to be type: object (like OpenAI)
        # If schema is an array, wrap it in an object
        if schema.get("type") == "array":
            schema = {
                "type": "object",
                "properties": {"items": schema},
                "required": ["items"],
                "additionalProperties": False,
            }

        def make_strict_recursive(obj):
            if isinstance(obj, dict):
                # If this is an object type schema
                if obj.get("type") == "object":
                    # Add additionalProperties: false
                    if "additionalProperties" not in obj:
                        obj["additionalProperties"] = False

                    # Grok strict mode requires ALL properties to be in required array (like OpenAI)
                    if "properties" in obj:
                        all_props = list(obj["properties"].keys())
                        obj["required"] = all_props

                # Recursively process properties
                if "properties" in obj:
                    for prop_schema in obj["properties"].values():
                        make_strict_recursive(prop_schema)

                # Recursively process items (for arrays)
                if "items" in obj:
                    make_strict_recursive(obj["items"])

                # Recursively process nested schemas
                for key in ["anyOf", "oneOf", "allOf"]:
                    if key in obj:
                        for sub_schema in obj[key]:
                            make_strict_recursive(sub_schema)

                # Process $defs (definitions referenced by $ref)
                if "$defs" in obj:
                    for def_schema in obj["$defs"].values():
                        make_strict_recursive(def_schema)

        make_strict_recursive(schema)
        return schema


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude provider client"""

    SUPPORTS_PDF_INPUT = True

    def __init__(self, api_key: str, model: str, **kwargs):
        super().__init__(api_key, model, **kwargs)
        try:
            from anthropic import Anthropic

            # Enable structured outputs beta header
            self.client = Anthropic(
                api_key=api_key, default_headers={"anthropic-beta": "structured-outputs-2025-11-13"}
            )
        except ImportError as err:
            raise ImportError(
                "Anthropic package not installed. Install with: pip install anthropic"
            ) from err

    def generate(self, prompt: str, temperature: float = 0.7, **kwargs) -> str:
        """Generate text completion using Anthropic Claude"""
        try:
            max_tokens = kwargs.pop("max_tokens", 4096)
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"Anthropic generation failed: {e}")
            raise

    def generate_json(
        self,
        prompt: str,
        schema: dict | None = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs,
    ) -> dict:
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
                            "json_schema": {"name": "response", "strict": True, "schema": schema},
                        },
                        **kwargs,
                    )
                else:
                    # Fallback to prompt-based JSON without schema
                    json_prompt = f"{prompt}\n\nYou must respond with valid JSON only."
                    response = self.client.messages.create(
                        model=self.model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        messages=[{"role": "user", "content": json_prompt}],
                        **kwargs,
                    )

                content = response.content[0].text
                result = json.loads(content)

                logger.debug(f"Successfully generated JSON on attempt {attempt + 1}")
                return result

            except json.JSONDecodeError as e:
                logger.warning(f"JSON parsing failed on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise ValueError(f"Failed to generate valid JSON after {max_retries} attempts") from e

            except Exception as e:
                logger.error(f"Anthropic JSON generation failed: {e}")
                raise

    def generate_json_from_pdf(
        self,
        pdf_bytes: bytes,
        prompt: str,
        schema: dict | None = None,
        *,
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> dict:
        """Extract structured JSON from a PDF via Anthropic's document block.

        Sends the PDF as a base64-encoded ``document`` content block. Supported
        on Claude Sonnet 3.5+ models.
        """
        pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")

        message_content: list[dict] = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": pdf_b64,
                },
            },
            {"type": "text", "text": prompt},
        ]

        create_kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": message_content}],
        }
        if schema is not None:
            create_kwargs["output_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": schema},
            }

        logger.info(
            f"[TIMING] Starting Anthropic PDF extraction (model={self.model})"
        )
        api_start = time.time()
        response = self.client.messages.create(**create_kwargs)
        api_elapsed = time.time() - api_start
        logger.info(
            f"[TIMING] Anthropic PDF extraction completed in {api_elapsed:.2f}s"
        )

        text_parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
        content = "".join(text_parts) if text_parts else response.content[0].text
        return json.loads(content)


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

    @classmethod
    def supports_pdf(cls, provider: LLMProvider) -> bool:
        """Return True if the provider's client supports native PDF input."""
        client_class = cls._clients.get(provider)
        return bool(client_class and client_class.SUPPORTS_PDF_INPUT)
