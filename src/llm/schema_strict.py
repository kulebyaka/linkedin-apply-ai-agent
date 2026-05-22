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
