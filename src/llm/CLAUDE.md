# LLM Layer — Internals

Loaded when working under `src/llm/`. Root `claude.md` carries only the one-paragraph summary;
the non-obvious contracts live here.

## Multi-LLM support (Instructor + LiteLLM)

- A single `InstructorClient(BaseLLMClient)` (`src/llm/providers/instructor_client.py`) backs
  **all** providers. Structured output is coerced via Instructor's tool-calling mode
  (`instructor.from_litellm(litellm.completion)` defaults to `Mode.TOOLS`); provider routing is
  delegated to LiteLLM through prefixed model strings (`anthropic/…`, `openai/…`, `xai/…`,
  `deepseek/…`).
- `create_llm_client` (`src/agents/_shared.py`) resolves settings, reattaches the LiteLLM route
  prefix via `litellm_model(provider, bare_model)` (note `GROK → xai`), and returns an
  `InstructorClient`. There is **no** `LLMClientFactory` anymore.
- Abstract `BaseLLMClient` interface preserved; `generate_json` gained a preferred
  `response_model: type[BaseModel]` param (typed `@overload`s so callers get the model type back).
- Easy switching via environment variables; `litellm.drop_params = True` drops sampling params a
  model rejects (e.g. `temperature` on Opus 4.x / Sonnet 5) instead of gating per-model.

## Structured output (Instructor `Mode.TOOLS`, all providers)

- Callers pass a **Pydantic `response_model`** to `generate_json` / `generate_json_from_pdf`;
  Instructor coerces the output via **tool-calling** (`Mode.TOOLS`) and returns a validated
  instance. Example: `self.llm.generate_json(spec, response_model=FilterResult, temperature=…)`.
- The tool `input_schema` path is lenient about JSON-Schema constraint keywords (`minimum`/
  `maximum`/`maxLength`/…), so the old per-provider strict-schema reshaping was **removed**
  (`src/llm/schema_strict.py` is deleted). Confirmed by a live Anthropic gate: `FilterResult`
  (which carries `minimum`/`maximum`) returns no 400 under `Mode.TOOLS`, and prompt caching fires
  (`cache_read_input_tokens` non-zero on repeat). See
  `docs/plans/completed/instructor-migration-plan.md`, Task 6.
- A raw `schema: dict` is still accepted by `generate_json` (builds a throwaway model, returns a
  plain `dict`) for ad-hoc call sites, but every first-party call now uses `response_model`.
- `provider_supports_pdf(provider)` (`src/llm/base.py`) tracks PDF capability — LiteLLM 1.93.0
  has no `supports_pdf_input` lookup. OpenAI + Anthropic support PDF; Grok + DeepSeek do not.

## Prompt caching (preserved both providers)

- **Anthropic**: `PromptSpec.system` is emitted as a content-block list with
  `cache_control: {"type": "ephemeral"}`; LiteLLM maps it onto Anthropic's top-level `system`
  array carrying the cache breakpoint.
- **OpenAI-compatible**: `PromptSpec.cache_key` rides in `extra_body={"prompt_cache_key": …}` (a
  bare kwarg is dropped by LiteLLM). OpenAI auto-caches on the stable prefix regardless.

## `generate_json` resilience

- **Retries**: Instructor's built-in (Tenacity) retry handles invalid/failed structured output,
  bounded by `max_retries` (default 3). The old hand-rolled truncation-doubling +
  retry-with-feedback loop (`LLMTruncatedError`, `build_retry_feedback`) is **gone**. Large CV
  compositions pass a generous `max_tokens=8192` in `CVComposer._compose_all_sections` instead.
- **Validator**: `generate_json(..., validator=callable)` still runs a caller-supplied check on
  the parsed dict; first-party call sites rely on `response_model` validation instead.

## Model catalog (dynamic — up-to-date model list + prices)

- `src/llm/model_catalog.py` holds a **static** `MODEL_CATALOG` (dashed real IDs, e.g.
  `claude-opus-4-8`) used as the offline fallback.
- `src/llm/pricing_source.py` fetches the community-maintained LiteLLM pricing JSON
  (`model_prices_and_context_window.json`) — the source of the current model **list** *and*
  prices for OpenAI/Anthropic/DeepSeek/xAI. Load order: fresh disk cache → live refetch →
  stale cache → static fallback. Disk cache at `data/model_catalog_cache.json` (TTL 24h).
- Wired via `AppContext.model_catalog` + `AppContext.refresh_model_catalog()`; loaded at
  startup (non-blocking) and refreshed daily by `ModelCatalogScheduler`
  (`src/services/jobs/model_catalog_scheduler.py`). The `/api/llm/models` endpoint reads the
  context-held catalog. Config: `MODEL_CATALOG_DYNAMIC_ENABLED`, `MODEL_CATALOG_CACHE_PATH`,
  `MODEL_CATALOG_REFRESH_HOURS`, `MODEL_CATALOG_URL`.

Implementation lives in `src/llm/providers/instructor_client.py`. `src/llm/provider.py` is only a
27-line **re-export shim** kept so existing `from src.llm.provider import …` imports keep working —
don't look for logic there.

## Adding a new provider

Providers are added through LiteLLM + Instructor — there is a single `InstructorClient`, no
per-provider class to write:

1. Add the provider to the `LLMProvider` enum (`src/llm/base.py`).
2. Add the LiteLLM route prefix to `PROVIDER_LITELLM_PREFIX` in
   `src/llm/providers/instructor_client.py` (confirm the correct LiteLLM prefix, e.g. `xai/`,
   `deepseek/`; verify the provider supports **tool calling** for `Mode.TOOLS`, else fall back to
   Instructor `Mode.JSON`).
3. Add `*_api_key` / `*_model` settings to `settings.py` and the resolution branch in
   `create_llm_client` (`src/agents/_shared.py`).
4. If the provider accepts native PDF input, add it to `_PDF_CAPABLE_PROVIDERS`
   (`src/llm/base.py`, consumed by `provider_supports_pdf`).
5. Document in README. Prompt caching / cache-control wiring is handled generically by
   `InstructorClient` (Anthropic `cache_control` block vs OpenAI `extra_body` cache key).
