"""Tests for the modernized Anthropic client (request shapes + schema safety)."""

import types

from src.llm.prompt_spec import PromptSpec
from src.llm.providers.anthropic import AnthropicClient
from src.llm.schema_strict import make_schema_anthropic_safe

SPEC = PromptSpec(system="sys", user="do it", cache_key="")

# A schema with the kinds of constraints Pydantic ge/le/max_length emit.
CONSTRAINED_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "name": {"type": "string", "minLength": 1, "maxLength": 50},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 0,
            "maxItems": 5,
            "uniqueItems": True,
        },
    },
    "required": ["score", "reasoning"],
}


def _response(text: str, stop_reason: str = "end_turn"):
    usage = types.SimpleNamespace(input_tokens=10, cache_read_input_tokens=0)
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(text=text, type="text")],
        stop_reason=stop_reason,
        usage=usage,
    )


def _client(model: str, responses):
    client = AnthropicClient(api_key="test", model=model)
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
# make_schema_anthropic_safe
# ---------------------------------------------------------------------------


def test_sanitizer_strips_unsupported_constraints():
    safe = make_schema_anthropic_safe(CONSTRAINED_SCHEMA)
    score = safe["properties"]["score"]
    name = safe["properties"]["name"]
    tags = safe["properties"]["tags"]
    assert "minimum" not in score and "maximum" not in score
    assert "minLength" not in name and "maxLength" not in name
    assert "minItems" not in tags and "maxItems" not in tags
    assert "uniqueItems" not in tags


def test_sanitizer_adds_additional_properties_false():
    safe = make_schema_anthropic_safe(CONSTRAINED_SCHEMA)
    assert safe["additionalProperties"] is False


def test_sanitizer_does_not_mutate_input():
    original = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0}}}
    make_schema_anthropic_safe(original)
    assert original["properties"]["n"]["minimum"] == 0  # untouched


def test_sanitizer_preserves_property_named_like_constraint():
    schema = {
        "type": "object",
        "properties": {"minimum": {"type": "integer"}},
    }
    safe = make_schema_anthropic_safe(schema)
    assert "minimum" in safe["properties"]  # property key survives


# ---------------------------------------------------------------------------
# Request shapes
# ---------------------------------------------------------------------------


def test_no_beta_header_configured():
    client = AnthropicClient(api_key="test", model="claude-sonnet-5")
    # SDK client is real; assert we did not inject the old beta header.
    headers = getattr(client.client, "default_headers", {}) or {}
    assert "anthropic-beta" not in {k.lower() for k in headers}


def test_generate_json_uses_output_config_and_sanitized_schema():
    client, calls = _client("claude-sonnet-4-6", [_response('{"score": 5, "reasoning": "x"}')])
    client.generate_json(SPEC, schema=CONSTRAINED_SCHEMA)
    kwargs = calls[0]
    assert "output_format" not in kwargs  # deprecated shape gone
    assert "output_config" in kwargs
    fmt = kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert "minimum" not in fmt["schema"]["properties"]["score"]


def test_temperature_gated_off_for_current_gen_models():
    for model in ("claude-opus-4-8", "claude-sonnet-5", "claude-opus-4-7", "claude-fable-5"):
        client, calls = _client(model, [_response('{"a": 1}')])
        client.generate_json(SPEC)
        assert "temperature" not in calls[0], f"{model} must omit temperature"


def test_temperature_kept_for_older_models():
    client, calls = _client("claude-sonnet-4-6", [_response('{"a": 1}')])
    client.generate_json(SPEC, temperature=0.4)
    assert calls[0]["temperature"] == 0.4


def test_generate_json_from_pdf_uses_output_config():
    client, calls = _client("claude-sonnet-4-6", [_response('{"ok": true}')])
    client.generate_json_from_pdf(b"%PDF-1.4 fake", "extract", schema=CONSTRAINED_SCHEMA)
    kwargs = calls[0]
    assert "output_format" not in kwargs
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert "maximum" not in kwargs["output_config"]["format"]["schema"]["properties"]["score"]


def test_generate_json_from_pdf_gates_temperature():
    client, calls = _client("claude-opus-4-8", [_response('{"ok": true}')])
    client.generate_json_from_pdf(b"%PDF", "extract")
    assert "temperature" not in calls[0]
