"""Cache-aware prompt payload shared by all LLM provider clients.

``PromptSpec`` separates the static, cacheable portion of a prompt
(``system``) from the variable portion (``user``) so that OpenAI-compatible
providers can form a long, stable prefix that hits the automatic prompt
cache (≥1024 tokens). The ``cache_key`` is passed through as OpenAI's
``prompt_cache_key`` parameter to improve routing under fan-out.

Anthropic ignores ``cache_key`` and routes ``system`` into the top-level
``system=`` parameter on ``messages.create``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptSpec:
    """Two-part prompt payload with an optional cache routing hint.

    - ``system``: static content (instructions, schema, per-user master CV).
      Goes into role="system" — forms the cacheable prefix. ``None`` falls
      back to a single user message.
    - ``user``: variable content (job description, job_summary, feedback).
      Goes into role="user" — recomputed each call.
    - ``cache_key``: hint for OpenAI's ``prompt_cache_key`` routing. Format
      ``<call_site>:<user_id>`` (e.g. ``filter:42``). Pass an empty string
      for one-off calls with no user scope.
    """

    system: str | None
    user: str
    cache_key: str
