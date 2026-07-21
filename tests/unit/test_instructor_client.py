"""Unit tests for the Instructor + LiteLLM-backed ``InstructorClient``.

Uses the spike's ``httpx.Client.send`` intercept so no network / API keys are
needed: the outbound request is captured (and either aborted before the wire,
or answered with a crafted tool-call response for the structured-output path).
"""

from __future__ import annotations

import contextlib
import json
from unittest.mock import patch

import httpx
import pytest
from pydantic import BaseModel

from src.llm.base import LLMProvider
from src.llm.prompt_spec import PromptSpec
from src.llm.providers.instructor_client import (
    InstructorClient,
    litellm_model,
)


class _AbortError(Exception):
    """Sentinel raised by the intercept to stop before the network."""


def _capture_and_abort(captured: list):
    def fake_send(self, request, *args, **kwargs):  # noqa: ANN001
        captured.append(request)
        raise _AbortError()

    return fake_send


def _tool_call_responder(tool_name: str, payload: dict, *, cached_tokens: int = 0):
    """Return a fake ``httpx.Client.send`` that answers with a tool call.

    Mimics the OpenAI-compatible ``tool_calls`` shape Instructor's ``Mode.TOOLS``
    extracts and validates against the response model.
    """

    def fake_send(self, request, *args, **kwargs):  # noqa: ANN001
        body = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(payload),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {"cached_tokens": cached_tokens},
            },
        }
        return httpx.Response(200, json=body, request=request)

    return fake_send


class Person(BaseModel):
    name: str
    age: int


# ---------------------------------------------------------------------------
# litellm_model prefix helper
# ---------------------------------------------------------------------------


class TestLiteLLMModel:
    def test_prefixes_bare_model(self):
        assert litellm_model(LLMProvider.ANTHROPIC, "claude-opus-4-8") == (
            "anthropic/claude-opus-4-8"
        )
        assert litellm_model(LLMProvider.OPENAI, "gpt-4o") == "openai/gpt-4o"
        assert litellm_model(LLMProvider.DEEPSEEK, "deepseek-chat") == ("deepseek/deepseek-chat")

    def test_grok_maps_to_xai(self):
        assert litellm_model(LLMProvider.GROK, "grok-4") == "xai/grok-4"

    def test_already_prefixed_passthrough(self):
        assert litellm_model(LLMProvider.ANTHROPIC, "anthropic/claude-x") == ("anthropic/claude-x")


# ---------------------------------------------------------------------------
# Prompt-caching wire shape
# ---------------------------------------------------------------------------


class TestPromptCaching:
    def test_anthropic_system_carries_cache_control(self):
        captured: list = []
        client = InstructorClient(api_key="test", model="anthropic/claude-sonnet-5")
        spec = PromptSpec(system="SYSTEM PROMPT", user="hi", cache_key="filter:1")

        with patch.object(httpx.Client, "send", _capture_and_abort(captured)):
            with contextlib.suppress(Exception):
                client.generate(spec)

        body = json.loads(captured[-1].content)
        system = body["system"]
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert system[0]["text"] == "SYSTEM PROMPT"

    def test_openai_prompt_cache_key_present_via_extra_body(self):
        captured: list = []
        client = InstructorClient(api_key="test", model="openai/gpt-4o")
        spec = PromptSpec(system="SYS", user="hi", cache_key="filter:1")

        with patch.object(httpx.Client, "send", _capture_and_abort(captured)):
            with contextlib.suppress(Exception):
                client.generate(spec)

        body = json.loads(captured[-1].content)
        assert body.get("prompt_cache_key") == "filter:1"

    def test_openai_no_cache_key_when_absent(self):
        captured: list = []
        client = InstructorClient(api_key="test", model="openai/gpt-4o")
        spec = PromptSpec(system="SYS", user="hi", cache_key="")

        with patch.object(httpx.Client, "send", _capture_and_abort(captured)):
            with contextlib.suppress(Exception):
                client.generate(spec)

        body = json.loads(captured[-1].content)
        assert "prompt_cache_key" not in body

    def test_anthropic_never_sends_prompt_cache_key(self):
        captured: list = []
        client = InstructorClient(api_key="test", model="anthropic/claude-sonnet-5")
        spec = PromptSpec(system="SYS", user="hi", cache_key="filter:1")

        with patch.object(httpx.Client, "send", _capture_and_abort(captured)):
            with contextlib.suppress(Exception):
                client.generate(spec)

        body = json.loads(captured[-1].content)
        assert "prompt_cache_key" not in body


# ---------------------------------------------------------------------------
# generate_json (structured output)
# ---------------------------------------------------------------------------


class TestGenerateJson:
    def test_returns_validated_response_model_instance(self):
        client = InstructorClient(api_key="test", model="openai/gpt-4o")
        spec = PromptSpec(system="SYS", user="give me a person", cache_key="")

        with patch.object(
            httpx.Client,
            "send",
            _tool_call_responder("Person", {"name": "Ada", "age": 36}),
        ):
            result = client.generate_json(spec, response_model=Person)

        assert isinstance(result, Person)
        assert result.name == "Ada"
        assert result.age == 36

    def test_raises_when_neither_response_model_nor_schema(self):
        client = InstructorClient(api_key="test", model="openai/gpt-4o")
        spec = PromptSpec(system="SYS", user="u", cache_key="")

        with pytest.raises(ValueError, match="response_model or schema"):
            client.generate_json(spec)

    def test_raw_schema_returns_plain_dict(self):
        client = InstructorClient(api_key="test", model="openai/gpt-4o")
        spec = PromptSpec(system="SYS", user="u", cache_key="")
        schema = {
            "type": "object",
            "properties": {
                "proposed_learned_block": {"type": "string"},
                "rationale": {"type": "string"},
            },
            "required": ["proposed_learned_block", "rationale"],
            "additionalProperties": False,
        }
        payload = {
            "proposed_learned_block": "## Auto-learned criteria\n- x",
            "rationale": "because",
        }

        with patch.object(
            httpx.Client,
            "send",
            _tool_call_responder("DynamicResponse", payload),
        ):
            result = client.generate_json(spec, schema=schema)

        assert isinstance(result, dict)
        assert result == payload

    def test_response_model_runs_validator(self):
        client = InstructorClient(api_key="test", model="openai/gpt-4o")
        spec = PromptSpec(system="SYS", user="u", cache_key="")

        seen: list[dict] = []

        with patch.object(
            httpx.Client,
            "send",
            _tool_call_responder("Person", {"name": "Ada", "age": 36}),
        ):
            client.generate_json(spec, response_model=Person, validator=seen.append)

        assert seen == [{"name": "Ada", "age": 36}]


class TestSupportsPdfFlag:
    def test_supports_pdf_input_true(self):
        assert InstructorClient.SUPPORTS_PDF_INPUT is True


# ---------------------------------------------------------------------------
# generate_json_from_pdf (native document input)
# ---------------------------------------------------------------------------


class Doc(BaseModel):
    title: str


def _anthropic_tool_use_responder(name: str, payload: dict, block_assert):
    """Answer with an Anthropic-native ``tool_use`` block.

    ``block_assert`` receives the outbound message content blocks so tests can
    assert the PDF rode as a ``document`` block.
    """

    def fake_send(self, request, *args, **kwargs):  # noqa: ANN001
        body = json.loads(request.content)
        block_assert(body["messages"][-1]["content"])
        rbody = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": name, "input": payload}],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        return httpx.Response(200, json=rbody, request=request)

    return fake_send


class TestGenerateJsonFromPdf:
    def test_openai_returns_validated_model(self):
        client = InstructorClient(api_key="test", model="openai/gpt-4o")
        seen_blocks: list = []

        def responder(name, payload):
            base = _tool_call_responder(name, payload)

            def fake_send(self, request, *args, **kwargs):  # noqa: ANN001
                seen_blocks.append(json.loads(request.content)["messages"][-1]["content"])
                return base(self, request, *args, **kwargs)

            return fake_send

        with patch.object(httpx.Client, "send", responder("Doc", {"title": "Hello"})):
            result = client.generate_json_from_pdf(b"%PDF-1.4 fake", "extract", Doc)

        assert isinstance(result, Doc)
        assert result.title == "Hello"
        # PDF rode as an OpenAI ``file`` content block.
        assert any(b.get("type") == "file" for b in seen_blocks[-1])

    def test_anthropic_sends_document_block_and_returns_model(self):
        client = InstructorClient(api_key="test", model="anthropic/claude-sonnet-5")
        captured_blocks: list = []

        result = None
        with patch.object(
            httpx.Client,
            "send",
            _anthropic_tool_use_responder("Doc", {"title": "World"}, captured_blocks.append),
        ):
            result = client.generate_json_from_pdf(b"%PDF-1.4 fake", "extract", Doc)

        assert isinstance(result, Doc)
        assert result.title == "World"
        # LiteLLM transformed the unified ``file`` block into Anthropic ``document``.
        assert any(b.get("type") == "document" for b in captured_blocks[-1])

    def test_raw_schema_returns_plain_dict(self):
        client = InstructorClient(api_key="test", model="openai/gpt-4o")
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }
        with patch.object(
            httpx.Client, "send", _tool_call_responder("DynamicResponse", {"title": "X"})
        ):
            result = client.generate_json_from_pdf(b"%PDF-1.4 fake", "extract", schema=schema)

        assert isinstance(result, dict)
        assert result == {"title": "X"}
