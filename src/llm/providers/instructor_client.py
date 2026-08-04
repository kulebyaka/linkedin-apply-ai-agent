"""Single LLM provider client backed by Instructor + LiteLLM.

Replaces the four hand-rolled provider adapters. Structured output is coerced
by Instructor, with the mode chosen per provider family:

- **OpenAI-compatible** (OpenAI, DeepSeek, xAI): ``Mode.JSON`` — native
  ``response_format={"type": "json_object"}``, no function tools. This sidesteps
  the gpt-5.4+ restriction that *function tools + reasoning* aren't supported on
  ``/v1/chat/completions`` (so reasoning models keep reasoning enabled).
- **Anthropic** (not OpenAI-compatible): ``Mode.TOOLS`` — ``tool_use``, the most
  reliable structured path for Anthropic via LiteLLM.

Provider routing is delegated to LiteLLM through prefixed model strings
(``anthropic/claude-...``, ``openai/gpt-4o``, ``xai/grok-4``,
``deepseek/deepseek-chat``).

Prompt caching is preserved for both provider families:
- **Anthropic**: ``PromptSpec.system`` is emitted as a content-block list with
  ``cache_control: {"type": "ephemeral"}``; LiteLLM maps this onto Anthropic's
  top-level ``system`` array carrying the cache breakpoint.
- **OpenAI-compatible**: ``PromptSpec.cache_key`` is passed via
  ``extra_body={"prompt_cache_key": ...}`` (a bare kwarg is silently dropped by
  LiteLLM). OpenAI auto-caches on the stable prefix regardless; this is only the
  routing hint.

Unsupported sampling params (e.g. temperature on Opus 4.x / Sonnet 5) are
dropped automatically by ``litellm.drop_params = True`` rather than gated
per-model.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any, TypeVar, overload

import instructor
import litellm
from pydantic import BaseModel, create_model

from ..base import BaseLLMClient, LLMProvider
from ..prompt_spec import PromptSpec

_ResponseModelT = TypeVar("_ResponseModelT", bound=BaseModel)

# Keep provider metadata out of LiteLLM (telemetry) and let it silently drop
# params a given model rejects instead of 400-ing.
litellm.drop_params = True
litellm.telemetry = False

logger = logging.getLogger(__name__)

#: Maps our provider enum onto the LiteLLM route prefix. The inverse of
#: ``pricing_source._PROVIDER_MAP`` for the four supported providers
#: (note ``GROK`` → ``xai``).
PROVIDER_LITELLM_PREFIX: dict[LLMProvider, str] = {
    LLMProvider.OPENAI: "openai",
    LLMProvider.ANTHROPIC: "anthropic",
    LLMProvider.DEEPSEEK: "deepseek",
    LLMProvider.GROK: "xai",
}


@lru_cache(maxsize=128)
def _model_flag(model: str, flag: str) -> bool:
    """Read a boolean capability ``flag`` for ``model`` from LiteLLM's model map.

    Unknown models (no map entry) return ``False`` so we never send a param the
    model may not accept.
    """
    try:
        info = litellm.get_model_info(model)
    except Exception:
        return False
    return bool(info.get(flag))


@lru_cache(maxsize=128)
def _supports_reasoning(model: str) -> bool:
    """Whether ``model`` accepts a ``reasoning_effort`` param at all.

    ``litellm.supports_reasoning`` is the right gate — rather than "is this a
    reasoning model" — because it is ``False`` both for non-reasoning models
    (``gpt-4o``, ``deepseek-chat``, ``claude-3-5-sonnet``) *and* for models that
    reason internally but reject the param (``xai/grok-4`` errors on it, while
    ``grok-4.5`` and ``grok-3-mini`` accept it).
    """
    try:
        return bool(litellm.supports_reasoning(model=model))
    except Exception:
        return False


def litellm_model(provider: LLMProvider, bare_model: str) -> str:
    """Return the LiteLLM-prefixed model string for ``provider``/``bare_model``.

    If ``bare_model`` already carries a ``prefix/`` route it is returned as-is,
    so callers can pass either a bare id (``claude-opus-4-8``) or a prefixed one
    (``anthropic/claude-opus-4-8``).
    """
    if "/" in bare_model:
        return bare_model
    prefix = PROVIDER_LITELLM_PREFIX.get(provider)
    if prefix is None:
        raise ValueError(f"Unsupported LLM provider: {provider}")
    return f"{prefix}/{bare_model}"


class InstructorClient(BaseLLMClient):
    """Instructor + LiteLLM-backed client implementing ``BaseLLMClient``.

    ``model`` is a LiteLLM-prefixed string (e.g. ``anthropic/claude-sonnet-5``).
    """

    SUPPORTS_PDF_INPUT = True

    def __init__(self, api_key: str, model: str, **kwargs: Any) -> None:
        super().__init__(api_key, model, **kwargs)
        # Structured-output coercion mode depends on the provider family:
        #   - OpenAI-compatible (OpenAI, DeepSeek, xAI) → ``Mode.JSON`` (native
        #     ``response_format={"type": "json_object"}``, no function tools).
        #     Avoids the gpt-5.4+ "function tools + reasoning_effort not
        #     supported on /v1/chat/completions" conflict and lets reasoning
        #     stay enabled.
        #   - Anthropic (not OpenAI-compatible) → ``Mode.TOOLS`` (tool_use),
        #     Anthropic's most reliable structured path via LiteLLM.
        mode = instructor.Mode.TOOLS if self._is_anthropic else instructor.Mode.JSON
        self._client = instructor.from_litellm(litellm.completion, mode=mode)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _is_anthropic(self) -> bool:
        return self.model.startswith("anthropic/")

    def reasoning_kwargs(self, effort: str = "low", *, structured: bool) -> dict[str, Any]:
        """Reasoning kwargs for this model on this path, or ``{}`` if unsupported.

        Gates, in order:

        1. ``litellm.supports_reasoning`` — the model must accept the param (see
           :func:`_supports_reasoning`; notably excludes ``grok-4``, which
           reasons but rejects it).
        2. **Structured path on Anthropic only.** ``Mode.TOOLS`` forces the tool
           call, and Claude 4.5 and earlier — whose only thinking mode is a
           fixed ``budget_tokens`` budget — reject that combination outright:
           ``"Thinking may not be enabled when tool_choice forces tool use."``
           Adaptive-thinking models (Claude 4.6+, ``supports_adaptive_thinking``)
           accept it. OpenAI-compatible providers are unaffected because their
           structured path is ``Mode.JSON`` — no function tools involved.
        3. **Anthropic, both paths.** Thinking requires ``temperature`` 1, so the
           returned dict pins it: ``"`temperature` may only be set to 1 when
           thinking is enabled or in adaptive mode"``. ``litellm.drop_params``
           does not help here — the param is supported, just not at another
           value — so the caller's temperature has to be replaced, not dropped.
        """
        if not _supports_reasoning(self.model):
            return {}
        if (
            structured
            and self._is_anthropic
            and not _model_flag(self.model, "supports_adaptive_thinking")
        ):
            return {}
        kwargs: dict[str, Any] = {"reasoning_effort": effort}
        if self._is_anthropic:
            kwargs["temperature"] = 1.0
        return kwargs

    def _build_messages(self, spec: PromptSpec) -> list[dict]:
        """Translate a ``PromptSpec`` into LiteLLM/OpenAI-style messages.

        For Anthropic the system prompt is a content-block list carrying an
        ``ephemeral`` cache breakpoint; for everyone else it is a plain string
        (OpenAI auto-caches the prefix and the ``prompt_cache_key`` routing hint
        is attached separately via ``extra_body``).
        """
        messages: list[dict] = []
        if spec.system:
            if self._is_anthropic:
                messages.append(
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": spec.system,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                )
            else:
                messages.append({"role": "system", "content": spec.system})
        messages.append({"role": "user", "content": spec.user})
        return messages

    def _cache_kwargs(self, spec: PromptSpec) -> dict:
        """Return completion kwargs carrying the OpenAI cache-routing hint.

        LiteLLM drops a bare ``prompt_cache_key=`` kwarg, so it must ride inside
        ``extra_body``. Anthropic caching is handled by the message
        ``cache_control`` block, so nothing is added there.
        """
        if not self._is_anthropic and spec.cache_key:
            return {"extra_body": {"prompt_cache_key": spec.cache_key}}
        return {}

    def _log_usage(self, response: Any, elapsed: float, spec: PromptSpec, kind: str) -> None:
        """Log input/cached token counts across both provider families."""
        usage = getattr(response, "usage", None)
        # OpenAI-compatible: usage.prompt_tokens_details.cached_tokens
        details = getattr(usage, "prompt_tokens_details", None) if usage else None
        cached = getattr(details, "cached_tokens", 0) if details else 0
        # Anthropic (LiteLLM surfaces this on usage directly).
        if not cached and usage is not None:
            cached = getattr(usage, "cache_read_input_tokens", 0) or 0
        total = getattr(usage, "prompt_tokens", 0) if usage else 0
        logger.info(
            "[TIMING] %s %s call completed in %.2fs (cached_tokens=%d/%d key=%s)",
            self.model,
            kind,
            elapsed,
            cached or 0,
            total or 0,
            spec.cache_key or "-",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_json_from_pdf(
        self,
        pdf_bytes: bytes,
        prompt: str,
        response_model: type[BaseModel] | None = None,
        schema: dict | None = None,
        *,
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> BaseModel | dict:
        """Extract structured JSON from a PDF via native document input.

        Uses LiteLLM's unified ``file`` content block, which it transforms into
        Anthropic's ``document`` block or OpenAI's ``file`` block depending on
        the routed provider — one code path for both. Structured output is
        coerced with Instructor exactly like :meth:`generate_json`.
        """
        model_cls, return_dict = self._resolve_response_model(response_model, schema)
        pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")

        call_kwargs: dict = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        # Document before the prompt text: Anthropic recommends
                        # placing the PDF first for best extraction quality.
                        {
                            "type": "file",
                            "file": {
                                "file_data": f"data:application/pdf;base64,{pdf_b64}",
                                "filename": "document.pdf",
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "response_model": model_cls,
            "max_retries": 2,
            "api_key": self.api_key,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        logger.info("[TIMING] Starting %s PDF extraction", self.model)
        api_start = time.time()
        result = self._client.chat.completions.create(**call_kwargs)
        logger.info(
            "[TIMING] %s PDF extraction completed in %.2fs",
            self.model,
            time.time() - api_start,
        )

        model_result: BaseModel = result
        return model_result.model_dump() if return_dict else model_result

    def generate(self, spec: PromptSpec, temperature: float = 0.7, **kwargs: Any) -> str:
        """Plain text completion via ``litellm.completion``."""
        call_kwargs: dict = {
            "model": self.model,
            "messages": self._build_messages(spec),
            "api_key": self.api_key,
            "temperature": temperature,
            **self._cache_kwargs(spec),
            **kwargs,
        }
        try:
            api_start = time.time()
            response = litellm.completion(**call_kwargs)
            self._log_usage(response, time.time() - api_start, spec, "text")
        except Exception as e:
            logger.error(f"{self.model} generation failed: {e}")
            raise
        # content is None on an empty/refused completion; keep the ``-> str``
        # contract so callers' ``.strip()``/concatenation never hit ``None``.
        content: str = response.choices[0].message.content or ""
        return content

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

    def generate_json(
        self,
        spec: PromptSpec,
        response_model: type[BaseModel] | None = None,
        schema: dict | None = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        validator: Callable[[dict], None] | None = None,
        **kwargs: Any,
    ) -> BaseModel | dict:
        """Structured output via Instructor (tool-calling / ``Mode.TOOLS``).

        Prefers ``response_model`` (returns a validated instance). Falls back to
        building a throwaway model from a raw ``schema`` dict (returns a plain
        ``dict``). Raises ``ValueError`` if neither is supplied.
        """
        model_cls, return_dict = self._resolve_response_model(response_model, schema)

        call_kwargs: dict = {
            "model": self.model,
            "messages": self._build_messages(spec),
            "response_model": model_cls,
            "max_retries": max_retries,
            "api_key": self.api_key,
            "temperature": temperature,
            **self._cache_kwargs(spec),
            **kwargs,
        }

        try:
            logger.info(
                "[TIMING] Starting %s JSON call (response_model=%s, key=%s)",
                self.model,
                model_cls.__name__,
                spec.cache_key or "-",
            )
            api_start = time.time()
            # ``create_with_completion`` also returns the raw ModelResponse so
            # prompt-cache hit counts (cached_tokens) stay observable on the
            # primary structured-output path, mirroring ``generate``.
            result, raw_response = self._client.chat.completions.create_with_completion(
                **call_kwargs
            )
            self._log_usage(raw_response, time.time() - api_start, spec, "JSON")
        except Exception as e:
            logger.error(f"{self.model} JSON generation failed: {e}")
            raise

        model_result: BaseModel = result
        if return_dict:
            data: dict = model_result.model_dump()
            if validator is not None:
                validator(data)
            return data

        if validator is not None:
            validator(model_result.model_dump())
        return model_result

    @staticmethod
    def _resolve_response_model(
        response_model: type[BaseModel] | None,
        schema: dict | None,
    ) -> tuple[type[BaseModel], bool]:
        """Pick the Pydantic model to hand Instructor.

        Returns ``(model_cls, return_dict)`` where ``return_dict`` signals that
        the caller passed a raw ``schema`` dict and expects a plain dict back.
        """
        if response_model is not None:
            return response_model, False
        if schema is not None:
            return _model_from_schema(schema), True
        raise ValueError("generate_json requires either response_model or schema")


# ---------------------------------------------------------------------------
# Raw-dict schema → throwaway Pydantic model (legacy schema= path)
# ---------------------------------------------------------------------------

_JSON_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _model_from_schema(schema: dict) -> type[BaseModel]:
    """Build a throwaway Pydantic model from a JSON-schema dict.

    Supports the flat/nested object subset our call sites emit. Cached on the
    schema's identity via its JSON string so repeated calls reuse one class.
    """
    import json

    return _model_from_schema_cached(json.dumps(schema, sort_keys=True))


@lru_cache(maxsize=64)
def _model_from_schema_cached(schema_json: str) -> type[BaseModel]:
    import json

    schema = json.loads(schema_json)
    return _build_model(schema, name="DynamicResponse")


def _build_model(schema: dict, *, name: str) -> type[BaseModel]:
    props: dict = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields: dict[str, tuple] = {}
    for key, prop in props.items():
        py_type = _py_type(prop, field_name=f"{name}_{key}")
        default = ... if key in required else None
        if default is None:
            py_type = py_type | None  # type: ignore[operator]
        fields[key] = (py_type, default)
    model: type[BaseModel] = create_model(name, **fields)  # type: ignore[call-overload]
    return model


def _py_type(prop: dict, *, field_name: str) -> Any:
    prop_type = prop.get("type")
    if isinstance(prop_type, list):
        prop_type = next((t for t in prop_type if t != "null"), "string")
    if prop_type == "object" and "properties" in prop:
        return _build_model(prop, name=field_name.title().replace("_", ""))
    if prop_type == "array":
        items = prop.get("items")
        if not items:
            # Untyped items: accept any list rather than forcing list[str],
            # which would reject a list of objects/numbers.
            return list
        item_type = _py_type(items, field_name=field_name + "Item")
        return list[item_type]  # type: ignore[valid-type]
    return _JSON_TYPE_MAP.get(prop_type or "string", str)
