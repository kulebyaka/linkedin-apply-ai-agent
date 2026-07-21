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

# Dated snapshot suffix: "-20250514" or "@20250514".
_DATED_SUFFIX = re.compile(r"(?:[-@])\d{8}$")


def _strip_provider_prefix(key: str, litellm_provider: str) -> str:
    """Strip a ``provider/`` prefix from a LiteLLM model key.

    LiteLLM prefixes non-OpenAI/Anthropic keys (``xai/grok-4``,
    ``deepseek/deepseek-chat``); OpenAI/Anthropic keys are bare.
    """
    prefix = f"{litellm_provider}/"
    if key.startswith(prefix):
        return key[len(prefix) :]
    return key


def parse_litellm_json(data: dict) -> list[ModelCatalogEntry]:
    """Parse the LiteLLM pricing JSON into ``ModelCatalogEntry`` records.

    Keeps chat models for the four supported providers with a known price,
    strips provider prefixes, converts per-token cost → per-1M, maps
    ``supports_response_schema`` → ``supports_strict_schema``, drops
    non-text ``mode: chat`` variants, and skips dated snapshots when a bare
    alias for the same provider exists. Duplicate (provider, model) pairs are
    de-duplicated (first wins).
    """
    # First pass: filter to candidate chat models with a price.
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
        candidates.append((model_id, provider, meta))

    # Set of (provider, model_id) present after prefix stripping — used to
    # detect whether a dated snapshot has a bare alias to defer to.
    present = {(provider, model_id) for model_id, provider, _ in candidates}

    entries: list[ModelCatalogEntry] = []
    seen: set[tuple[str, str]] = set()
    for model_id, provider, meta in candidates:
        dated = _DATED_SUFFIX.search(model_id)
        if dated:
            alias = model_id[: dated.start()]
            if (provider, alias) in present:
                continue  # prefer the bare alias
        dedup_key = (provider, model_id)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        entries.append(
            ModelCatalogEntry(
                provider=_PROVIDER_MAP[provider],
                model=model_id,
                display_name=model_id,
                input_cost_per_1m=round(meta["input_cost_per_token"] * 1e6, 6),
                output_cost_per_1m=round(meta["output_cost_per_token"] * 1e6, 6),
                supports_strict_schema=bool(meta.get("supports_response_schema")),
                supports_json_object=True,
                supports_plain_text=True,
            )
        )
    return entries


async def fetch_catalog(
    url: str = LITELLM_URL, *, timeout: float = 10.0
) -> list[ModelCatalogEntry]:
    """Fetch + parse the LiteLLM catalog over HTTP.

    Raises on network error, non-2xx, invalid JSON, or an empty parse.
    """
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    entries = parse_litellm_json(data)
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
        entries = await fetch_catalog(url, timeout=timeout)
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
