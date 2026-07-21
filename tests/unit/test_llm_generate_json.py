"""Tests for generate_json truncation detection + retry-with-feedback.

Covers both provider families: OpenAI-compatible (``choices[0].finish_reason``)
and Anthropic (``response.stop_reason``). All SDK calls are mocked.
"""

import types

import pytest

from src.llm.base import LLMTruncatedError
from src.llm.prompt_spec import PromptSpec
from src.llm.providers.anthropic import AnthropicClient
from src.llm.providers.deepseek import DeepSeekClient
from src.llm.providers.openai import OpenAIClient

SPEC = PromptSpec(system="sys", user="do the thing", cache_key="")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _openai_response(content: str, finish_reason: str = "stop"):
    message = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(message=message, finish_reason=finish_reason)
    return types.SimpleNamespace(choices=[choice], usage=None)


def _make_openai(responses):
    client = OpenAIClient(api_key="test", model="gpt-4o")
    calls: list[dict] = []
    it = iter(responses)

    def fake_create(**kwargs):
        calls.append(kwargs)
        return next(it)

    client.client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=fake_create)
        )
    )
    return client, calls


def _make_deepseek(responses):
    client = DeepSeekClient(api_key="test", model="deepseek-chat")
    calls: list[dict] = []
    it = iter(responses)

    def fake_create(**kwargs):
        calls.append(kwargs)
        return next(it)

    client.client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=fake_create)
        )
    )
    return client, calls


def _anthropic_response(text: str, stop_reason: str = "end_turn"):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(text=text, type="text")],
        stop_reason=stop_reason,
    )


def _make_anthropic(responses):
    client = AnthropicClient(api_key="test", model="claude-sonnet-5")
    calls: list[dict] = []
    it = iter(responses)

    def fake_create(**kwargs):
        calls.append(kwargs)
        return next(it)

    client.client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=fake_create)
    )
    return client, calls


# ---------------------------------------------------------------------------
# OpenAI-compatible
# ---------------------------------------------------------------------------


def test_openai_truncation_doubles_then_raises():
    client, calls = _make_openai(
        [
            _openai_response("{partial", finish_reason="length"),
            _openai_response("{still partial", finish_reason="length"),
        ]
    )
    with pytest.raises(LLMTruncatedError):
        client.generate_json(SPEC, max_tokens=100)
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 100
    assert calls[1]["max_tokens"] == 200  # doubled once


def test_openai_truncation_recovers_after_doubling():
    client, calls = _make_openai(
        [
            _openai_response("{partial", finish_reason="length"),
            _openai_response('{"ok": true}', finish_reason="stop"),
        ]
    )
    result = client.generate_json(SPEC, max_tokens=50)
    assert result == {"ok": True}
    assert calls[1]["max_tokens"] == 100


def test_openai_invalid_json_feeds_previous_output_and_error():
    client, calls = _make_openai(
        [
            _openai_response("not json at all", finish_reason="stop"),
            _openai_response('{"x": 1}', finish_reason="stop"),
        ]
    )
    result = client.generate_json(SPEC)
    assert result == {"x": 1}
    # Second request's user message carries the feedback block.
    retry_user = calls[1]["messages"][-1]["content"]
    assert "Your previous response was invalid" in retry_user
    assert "not json at all" in retry_user


def test_openai_validator_failure_feeds_message_into_retry():
    def validator(data: dict) -> None:
        if not data.get("ok"):
            raise ValueError("field ok must be true")

    client, calls = _make_openai(
        [
            _openai_response('{"ok": false}', finish_reason="stop"),
            _openai_response('{"ok": true}', finish_reason="stop"),
        ]
    )
    result = client.generate_json(SPEC, validator=validator)
    assert result == {"ok": True}
    retry_user = calls[1]["messages"][-1]["content"]
    assert "field ok must be true" in retry_user
    assert '{"ok": false}' in retry_user


def test_openai_raises_valueerror_after_exhausting_retries():
    client, _ = _make_openai([_openai_response("nope") for _ in range(3)])
    with pytest.raises(ValueError, match="Failed to generate valid JSON after 3"):
        client.generate_json(SPEC, max_retries=3)


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def test_anthropic_truncation_doubles_then_raises():
    client, calls = _make_anthropic(
        [
            _anthropic_response("{partial", stop_reason="max_tokens"),
            _anthropic_response("{still", stop_reason="max_tokens"),
        ]
    )
    with pytest.raises(LLMTruncatedError):
        client.generate_json(SPEC, max_tokens=1000)
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 1000
    assert calls[1]["max_tokens"] == 2000


def test_anthropic_invalid_json_feeds_feedback():
    client, calls = _make_anthropic(
        [
            _anthropic_response("garbage", stop_reason="end_turn"),
            _anthropic_response('{"y": 2}', stop_reason="end_turn"),
        ]
    )
    result = client.generate_json(SPEC)
    assert result == {"y": 2}
    retry_user = calls[1]["messages"][-1]["content"]
    assert "Your previous response was invalid" in retry_user
    assert "garbage" in retry_user


def test_anthropic_validator_failure_feeds_message():
    def validator(data: dict) -> None:
        if data.get("score", 0) < 50:
            raise ValueError("score too low")

    client, calls = _make_anthropic(
        [
            _anthropic_response('{"score": 10}', stop_reason="end_turn"),
            _anthropic_response('{"score": 90}', stop_reason="end_turn"),
        ]
    )
    result = client.generate_json(SPEC, validator=validator)
    assert result == {"score": 90}
    assert "score too low" in calls[1]["messages"][-1]["content"]


def test_anthropic_custom_max_tokens_preserved_across_retries():
    # A JSON-invalid retry (not truncation) must reuse the caller's max_tokens.
    client, calls = _make_anthropic(
        [
            _anthropic_response("not json", stop_reason="end_turn"),
            _anthropic_response('{"ok": true}', stop_reason="end_turn"),
        ]
    )
    result = client.generate_json(SPEC, max_tokens=1234)
    assert result == {"ok": True}
    assert calls[0]["max_tokens"] == 1234
    assert calls[1]["max_tokens"] == 1234


# ---------------------------------------------------------------------------
# DeepSeek (non-strict path) — full jsonschema validation
# ---------------------------------------------------------------------------

# Top-level 'person' is present (the old shallow validator passed), but the
# nested required 'age' is missing — only full validation catches this.
NESTED_SCHEMA = {
    "type": "object",
    "properties": {
        "person": {
            "type": "object",
            "properties": {"age": {"type": "integer"}},
            "required": ["age"],
        }
    },
    "required": ["person"],
}


def test_deepseek_nested_schema_violation_is_caught():
    client, _ = _make_deepseek(
        [_openai_response('{"person": {}}', finish_reason="stop") for _ in range(3)]
    )
    with pytest.raises(ValueError, match="Failed to generate valid JSON"):
        client.generate_json(SPEC, schema=NESTED_SCHEMA, max_retries=3)


def test_deepseek_nested_schema_recovers_after_feedback():
    client, calls = _make_deepseek(
        [
            _openai_response('{"person": {}}', finish_reason="stop"),
            _openai_response('{"person": {"age": 30}}', finish_reason="stop"),
        ]
    )
    result = client.generate_json(SPEC, schema=NESTED_SCHEMA)
    assert result == {"person": {"age": 30}}
    # feedback carried the schema-validation error into the retry
    assert "Schema validation failed" in calls[1]["messages"][-1]["content"]


def test_openai_custom_max_tokens_preserved_across_retries():
    client, calls = _make_openai(
        [
            _openai_response("not json", finish_reason="stop"),
            _openai_response('{"ok": true}', finish_reason="stop"),
        ]
    )
    result = client.generate_json(SPEC, max_tokens=777)
    assert result == {"ok": True}
    assert calls[0]["max_tokens"] == 777
    assert calls[1]["max_tokens"] == 777
