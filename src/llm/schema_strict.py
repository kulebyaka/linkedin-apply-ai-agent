"""JSON schema preparation for OpenAI-compatible strict mode.

Both OpenAI and Grok require:
  - Root must be `type: "object"`
  - Every object must have `additionalProperties: false`
  - Every object's properties must appear in `required`

OpenAI's variant is a superset of Grok's, so this single implementation works
for both providers.
"""

from __future__ import annotations

import copy


def make_schema_strict(schema: dict) -> dict:
    """Return a deep-copied schema reshaped to satisfy strict mode constraints.

    - Wraps top-level arrays in an object (root must be object).
    - Adds `additionalProperties: false` to every object schema.
    - Promotes every property into `required`.
    - Recurses through `properties`, `items`, `anyOf`/`oneOf`/`allOf`, and `$defs`.
    """
    schema = copy.deepcopy(schema)

    if schema.get("type") == "array":
        schema = {
            "type": "object",
            "properties": {"items": schema},
            "required": ["items"],
            "additionalProperties": False,
        }

    _make_strict_recursive(schema)
    return schema


def _make_strict_recursive(obj) -> None:
    if not isinstance(obj, dict):
        return

    type_val = obj.get("type")
    is_object = type_val == "object" or (
        isinstance(type_val, list) and "object" in type_val
    )

    if is_object:
        if "additionalProperties" not in obj:
            obj["additionalProperties"] = False
        if "properties" in obj:
            obj["required"] = list(obj["properties"].keys())

    if "properties" in obj:
        for prop_schema in obj["properties"].values():
            _make_strict_recursive(prop_schema)

    if "items" in obj:
        _make_strict_recursive(obj["items"])

    for key in ("anyOf", "oneOf", "allOf"):
        if key in obj:
            for sub_schema in obj[key]:
                _make_strict_recursive(sub_schema)

    if "$defs" in obj:
        for def_schema in obj["$defs"].values():
            _make_strict_recursive(def_schema)


# JSON Schema keywords that Anthropic's structured outputs reject. Pydantic's
# ``ge``/``le``/``max_length`` constraints emit these, so they must be stripped
# before the schema is sent (the constraints stay enforced client-side via the
# generate_json ``validator`` callback). ``format`` is intentionally kept —
# Anthropic supports the standard string formats (date-time, email, …).
_ANTHROPIC_UNSUPPORTED_KEYS: tuple[str, ...] = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
)


def make_schema_anthropic_safe(schema: dict) -> dict:
    """Return a deep-copied schema safe for Anthropic structured outputs.

    Recursively strips numeric/length/array/object constraint keywords that
    the API rejects, and ensures every object has ``additionalProperties:
    false``. Unlike :func:`make_schema_strict`, it does not promote every
    property into ``required`` — Anthropic tolerates partial ``required``.
    """
    schema = copy.deepcopy(schema)
    _make_anthropic_safe_recursive(schema)
    return schema


def _make_anthropic_safe_recursive(obj) -> None:
    if not isinstance(obj, dict):
        return

    for key in _ANTHROPIC_UNSUPPORTED_KEYS:
        obj.pop(key, None)

    type_val = obj.get("type")
    is_object = type_val == "object" or (
        isinstance(type_val, list) and "object" in type_val
    )
    if is_object and "additionalProperties" not in obj:
        obj["additionalProperties"] = False

    if "properties" in obj and isinstance(obj["properties"], dict):
        for prop_schema in obj["properties"].values():
            _make_anthropic_safe_recursive(prop_schema)

    if "items" in obj:
        items = obj["items"]
        if isinstance(items, list):
            for item in items:
                _make_anthropic_safe_recursive(item)
        else:
            _make_anthropic_safe_recursive(items)

    for key in ("anyOf", "oneOf", "allOf"):
        if key in obj:
            for sub_schema in obj[key]:
                _make_anthropic_safe_recursive(sub_schema)

    for defs_key in ("$defs", "definitions"):
        if defs_key in obj and isinstance(obj[defs_key], dict):
            for def_schema in obj[defs_key].values():
                _make_anthropic_safe_recursive(def_schema)
