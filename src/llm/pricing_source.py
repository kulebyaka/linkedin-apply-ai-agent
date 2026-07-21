"""Dynamic model catalog sourced from LiteLLM's community pricing JSON.

Neither Anthropic nor OpenAI expose pricing programmatically, and their
``/v1/models`` endpoints return only IDs (plus, for Anthropic, capabilities).
LiteLLM maintains a community JSON that carries, per model, the input/output
cost, context window, and capability flags for OpenAI, Anthropic, DeepSeek and
xAI — updated within days of model launches. We use it as the source of the
**up-to-date model list *and* prices**, and fall back to the static
``MODEL_CATALOG`` when offline.

Load order (see :func:`load_catalog`): fresh disk cache → live refetch →
stale disk cache → static ``MODEL_CATALOG``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import LLMProvider
from .model_catalog import MODEL_CATALOG, ModelCatalogEntry

logger = logging.getLogger(__name__)

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"  # noqa: E501

# LiteLLM's ``litellm_provider`` value → our provider enum. Only these four
# providers are surfaced; xAI is spelled "xai" in LiteLLM but "grok" for us.
_PROVIDER_MAP: dict[str, LLMProvider] = {
    "openai": LLMProvider.OPENAI,
    "anthropic": LLMProvider.ANTHROPIC,
    "deepseek": LLMProvider.DEEPSEEK,
    "xai": LLMProvider.GROK,
}

# Some non-text modalities are still tagged ``mode: "chat"`` in LiteLLM
# (e.g. realtime/audio variants). Drop any model whose id contains one of
# these tokens — they are not usable for our JSON-generation call sites.
_NAME_BLOCKLIST: tuple[str, ...] = (
    "realtime",
    "audio",
    "transcribe",
    "tts",
    "whisper",
    "embedding",
    "moderation",
    "image",
    "dall-e",
    "search",
)

# Deny-list of model-id patterns that are noise in a picker: fine-tuned models
# (``ft:gpt-4o:...``) and preview/experimental variants. These leak through the
# structural filters because they are still ``mode: chat`` with a price.
_DENY = re.compile(
    r"(?:^ft:|:ft-|-(?:preview|exp|experimental|nightly|alpha|beta)\b)",
    re.IGNORECASE,
)

# Dated snapshot suffix, matching (in priority order):
#   - dash ISO date:  "-2024-05-13"  (OpenAI/Grok modern snapshots)
#   - packed date:    "-20250514" / "@20250514"  (Anthropic)
#   - legacy 4-digit: "-0613" / "-1106" / "-1212"  (older OpenAI/Grok MMDD)
# A single trailing digit (``grok-4``, ``claude-opus-4-8``) never matches.
_DATED_SUFFIX = re.compile(r"(?:-\d{4}-\d{2}-\d{2}|[-@]\d{8}|-\d{4})$")

# Legacy small-context models (4k/8k/16k) are a modernity proxy: every current
# model these providers ship has a far larger window. Drop anything below this.
_MIN_CONTEXT_TOKENS = 32_000


def _strip_provider_prefix(key: str, litellm_provider: str) -> str:
    """Strip a ``provider/`` prefix from a LiteLLM model key.

    LiteLLM prefixes non-OpenAI/Anthropic keys (``xai/grok-4``,
    ``deepseek/deepseek-chat``); OpenAI/Anthropic keys are bare.
    """
    prefix = f"{litellm_provider}/"
    if key.startswith(prefix):
        return key[len(prefix) :]
    return key


def _make_entry(model_id: str, provider: str, meta: dict) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        provider=_PROVIDER_MAP[provider],
        model=model_id,
        display_name=model_id,
        input_cost_per_1m=round(meta["input_cost_per_token"] * 1e6, 6),
        output_cost_per_1m=round(meta["output_cost_per_token"] * 1e6, 6),
        supports_strict_schema=bool(meta.get("supports_response_schema")),
        supports_json_object=True,
        supports_plain_text=True,
    )


def parse_litellm_json(data: dict, *, now: datetime | None = None) -> list[ModelCatalogEntry]:
    """Parse the LiteLLM pricing JSON into ``ModelCatalogEntry`` records.

    Applies a strict, layered filter so the picker shows only current models
    (see :mod:`src.llm.model_catalog` for the rationale). In order:

    1. **Structural gate** — keep only ``mode: chat`` models for the four
       supported providers with a known input/output price, drop non-text
       ``mode: chat`` variants (``_NAME_BLOCKLIST``), drop fine-tunes /
       previews (``_DENY``), drop models past their ``deprecation_date``, and
       drop legacy small-context models (< ``_MIN_CONTEXT_TOKENS``).
    2. **Snapshot collapse** — group by base alias (stripping dated suffixes
       like ``-2024-05-13`` / ``-20250514`` / ``-0613``); keep the bare alias
       when present, otherwise the single newest dated snapshot.

    Converts per-token cost → per-1M and maps ``supports_response_schema`` →
    ``supports_strict_schema``. Duplicate (provider, model) pairs are
    de-duplicated. ``now`` (default: current UTC) anchors the deprecation cut.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)
    today = now.date().isoformat()

    # First pass: structural gate → candidate chat models worth showing.
    candidates: list[tuple[str, str, dict]] = []  # (model_id, litellm_provider, meta)
    for key, meta in data.items():
        if not isinstance(meta, dict):
            continue
        provider = meta.get("litellm_provider")
        if provider not in _PROVIDER_MAP:
            continue
        if meta.get("mode") != "chat":
            continue
        input_cost = meta.get("input_cost_per_token")
        output_cost = meta.get("output_cost_per_token")
        if input_cost is None or output_cost is None:
            continue
        model_id = _strip_provider_prefix(key, provider)
        lowered = model_id.lower()
        if any(token in lowered for token in _NAME_BLOCKLIST):
            continue
        if _DENY.search(model_id):
            continue
        deprecation = meta.get("deprecation_date")
        if isinstance(deprecation, str) and deprecation <= today:
            continue
        max_input = meta.get("max_input_tokens") or meta.get("max_tokens")
        if isinstance(max_input, (int, float)) and max_input < _MIN_CONTEXT_TOKENS:
            continue
        candidates.append((model_id, provider, meta))

    # Second pass: collapse dated snapshots. Group by (provider, base alias);
    # prefer any undated member, else keep the newest dated snapshot (dates
    # sort lexically within a family: "-2024-08-06" > "-2024-05-13").
    groups: dict[tuple[str, str], list[tuple[str, dict, bool]]] = {}
    for model_id, provider, meta in candidates:
        dated = _DATED_SUFFIX.search(model_id)
        base = model_id[: dated.start()] if dated else model_id
        groups.setdefault((provider, base), []).append((model_id, meta, bool(dated)))

    entries: list[ModelCatalogEntry] = []
    seen: set[tuple[str, str]] = set()
    for (provider, _base), members in groups.items():
        undated = [(mid, meta) for mid, meta, is_dated in members if not is_dated]
        chosen = undated if undated else [max(members, key=lambda m: m[0])[:2]]
        for model_id, meta in chosen:
            dedup_key = (provider, model_id)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            entries.append(_make_entry(model_id, provider, meta))
    return entries


async def fetch_catalog(
    url: str = LITELLM_URL, *, timeout: float = 10.0, now: datetime | None = None
) -> list[ModelCatalogEntry]:
    """Fetch + parse the LiteLLM catalog over HTTP.

    Raises on network error, non-2xx, invalid JSON, or an empty parse.
    """
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    entries = parse_litellm_json(data, now=now)
    if not entries:
        raise ValueError("LiteLLM catalog parsed to zero entries")
    return entries


@dataclass
class CachedCatalog:
    """A disk-cached catalog snapshot."""

    fetched_at: datetime
    entries: list[ModelCatalogEntry]


def read_cache(cache_path: str) -> CachedCatalog | None:
    """Load the disk cache, or ``None`` if absent/corrupt."""
    path = Path(cache_path)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(raw["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        entries = [ModelCatalogEntry(**item) for item in raw["entries"]]
        return CachedCatalog(fetched_at=fetched_at, entries=entries)
    except Exception:
        logger.warning("model catalog cache unreadable at %s", cache_path, exc_info=True)
        return None


def write_cache(cache_path: str, entries: list[ModelCatalogEntry], *, now: datetime) -> None:
    """Persist ``entries`` to the disk cache with a ``fetched_at`` stamp."""
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": now.isoformat(),
        "entries": [entry.model_dump() for entry in entries],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


async def load_catalog(
    *,
    cache_path: str,
    url: str = LITELLM_URL,
    ttl_hours: int = 24,
    static: list[ModelCatalogEntry] | None = None,
    timeout: float = 10.0,
    now: datetime | None = None,
) -> list[ModelCatalogEntry]:
    """Resolve the model catalog, never raising when offline.

    Order: fresh cache → live refetch (cached on success) → stale cache →
    static ``MODEL_CATALOG``.
    """
    if static is None:
        static = list(MODEL_CATALOG)
    if now is None:
        now = datetime.now(tz=timezone.utc)

    cached = read_cache(cache_path)
    if cached and (now - cached.fetched_at) < timedelta(hours=ttl_hours):
        logger.info("model catalog: using fresh disk cache (%d entries)", len(cached.entries))
        return cached.entries

    try:
        entries = await fetch_catalog(url, timeout=timeout, now=now)
        write_cache(cache_path, entries, now=now)
        logger.info("model catalog: fetched %d entries from LiteLLM", len(entries))
        return entries
    except Exception:
        logger.warning("model catalog: LiteLLM fetch failed; falling back", exc_info=True)

    if cached:
        logger.info("model catalog: using stale disk cache (%d entries)", len(cached.entries))
        return cached.entries

    logger.info("model catalog: using static fallback (%d entries)", len(static))
    return list(static)
