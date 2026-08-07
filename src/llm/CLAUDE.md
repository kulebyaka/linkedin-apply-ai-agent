# LLM Layer — Internals

Loaded when working under `src/llm/`. Root `claude.md` carries only the one-paragraph summary;
the non-obvious contracts live here.

## Multi-LLM support (Instructor + LiteLLM)

- A single `InstructorClient(BaseLLMClient)` (`src/llm/providers/instructor_client.py`) backs
  **all** providers. Provider routing is delegated to LiteLLM through prefixed model strings
  (`anthropic/…`, `openai/…`, `xai/…`, `deepseek/…`).
- `create_llm_client` (`src/agents/_shared.py`) resolves settings, reattaches the LiteLLM route
  prefix via `litellm_model(provider, bare_model)` (note `GROK → xai`), and returns an
  `InstructorClient`. There is **no** `LLMClientFactory` anymore.
- Abstract `BaseLLMClient` interface preserved; `generate_json` gained a preferred
  `response_model: type[BaseModel]` param (typed `@overload`s so callers get the model type back).
- Easy switching via environment variables; `litellm.drop_params = True` drops sampling params a
  model rejects (e.g. `temperature` on Opus 4.x / Sonnet 5) instead of gating per-model.

## Structured output (per-provider mode: JSON for OpenAI-compatible, TOOLS for Anthropic)

- Callers pass a **Pydantic `response_model`** to `generate_json` / `generate_json_from_pdf`;
  Instructor coerces the output and returns a validated instance. Example:
  `self.llm.generate_json(spec, response_model=FilterResult, temperature=…)`.
- **Mode is selected in `InstructorClient.__init__`**: OpenAI-compatible providers (OpenAI,
  DeepSeek, xAI) use `Mode.JSON` (native `response_format={"type": "json_object"}`, no function
  tools); Anthropic uses `Mode.TOOLS` (`tool_use`). Both paths validate against the Pydantic model
  and retry via Instructor/Tenacity. **Do not assume a single uniform mode** — that assumption is
  what broke structured output in production.
- **Why JSON mode for OpenAI-compatible**: OpenAI's gpt-5.4+ reasoning family (terra/sol/luna,
  5.1/5.2, …) rejects *function tools combined with reasoning* on `/v1/chat/completions`
  (`"Function tools with reasoning_effort are not supported … use /v1/responses or set
  reasoning_effort to 'none'"`). `Mode.JSON` sends no function tools, so the conflict never arises
  **and reasoning stays enabled** (no `reasoning_effort` override needed). This replaced an earlier
  `reasoning_effort="none"` injection.
- Schema constraint keywords (`minimum`/`maximum`/`maxLength`/…) are handled leniently on both
  paths, so the old per-provider strict-schema reshaping stays **removed**
  (`src/llm/schema_strict.py` deleted). A live Anthropic gate confirmed `FilterResult` (which
  carries `minimum`/`maximum`) returns no 400 under `Mode.TOOLS`, and prompt caching fires
  (`cache_read_input_tokens` non-zero on repeat). See
  `docs/plans/completed/instructor-migration-plan.md`, Task 6.
- A raw `schema: dict` is still accepted by `generate_json` (builds a throwaway model, returns a
  plain `dict`) for ad-hoc call sites, but every first-party call now uses `response_model`.
- `provider_supports_pdf(provider)` (`src/llm/base.py`) tracks PDF capability — LiteLLM 1.93.0
  has no `supports_pdf_input` lookup. OpenAI + Anthropic support PDF; Grok + DeepSeek do not.

## Reasoning effort (`reasoning_kwargs`)

- Call sites never set `reasoning_effort` themselves — they ask the client:
  `self.llm.reasoning_kwargs("low", structured=…)` returns completion kwargs or `{}`.
  `BaseLLMClient.reasoning_kwargs` defaults to `{}` (opt-in); `InstructorClient` overrides it.
- **Gates** (in order): `litellm.supports_reasoning(model)` must be True — it is `False` both for
  non-reasoning models (`gpt-4o`, `deepseek-chat`, `claude-3-5-sonnet`) *and* for models that reason
  but reject the param (`xai/grok-4`; `grok-4.5` / `grok-3-mini` accept it). Then, on the
  **structured path with Anthropic only**, `Mode.TOOLS` forces the tool call and Claude
  4.5-and-earlier reject that with thinking on (`"Thinking may not be enabled when tool_choice
  forces tool use."`); adaptive-thinking models (4.6+, `supports_adaptive_thinking`) are fine.
  OpenAI-compatible providers are unaffected — their structured path is `Mode.JSON`, no function
  tools.
- **Anthropic pins `temperature: 1.0`** in the returned dict on both paths (`"`temperature` may only
  be set to 1 when thinking is enabled or in adaptive mode"`). `litellm.drop_params` does *not*
  rescue this — the param is supported, just not at another value. So merge the reasoning kwargs
  **over** the call's own kwargs (`call_kwargs.update(...)`), never pass `temperature` alongside
  them or Python raises on the duplicate keyword.
- **Where it's enabled**: only `JobFilter.generate_prompt_from_preferences` and
  `JobFilter.generate_refinement` (`JobFilter.PROMPT_AUTHORING_REASONING = "low"`) — rare,
  user-triggered prompt-authoring work. Deliberately **not** on `evaluate_job`, CV composition, or
  PDF extraction: measured on the real filter prompt, reasoning cost ~7x the output tokens and ~5x
  the latency without changing a single verdict, and `FilterResult` already declares `reasoning`
  before `score` so the model reasons in-schema for ~150 tokens instead of ~3000.

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
- The release workflow deletes the disk cache on deploy, because `load_catalog` serves a <24h cache
  verbatim without re-parsing — a cache written by the outgoing image would otherwise mask a new
  release's catalog filter for up to 24h.

Implementation lives in `src/llm/providers/instructor_client.py`. `src/llm/provider.py` is only a
27-line **re-export shim** kept so existing `from src.llm.provider import …` imports keep working —
don't look for logic there.

## Adding a new provider

Providers are added through LiteLLM + Instructor — there is a single `InstructorClient`, no
per-provider class to write:

1. Add the provider to the `LLMProvider` enum (`src/llm/base.py`).
2. Add the LiteLLM route prefix to `PROVIDER_LITELLM_PREFIX` in
   `src/llm/providers/instructor_client.py` (confirm the correct LiteLLM prefix, e.g. `xai/`,
   `deepseek/`).
3. Decide its structured-output mode. `InstructorClient.__init__` currently branches on
   `_is_anthropic`: OpenAI-compatible providers get `Mode.JSON`, Anthropic gets `Mode.TOOLS`. A new
   OpenAI-compatible provider needs no change; anything else means extending that branch.
4. Add `*_api_key` / `*_model` settings to `settings.py` and the resolution branch in
   `create_llm_client` (`src/agents/_shared.py`).
5. If the provider accepts native PDF input, add it to `_PDF_CAPABLE_PROVIDERS`
   (`src/llm/base.py`, consumed by `provider_supports_pdf`).
6. Check `reasoning_kwargs`: `litellm.supports_reasoning` is the gate, but verify against the real
   API — it has been wrong in both directions (see the `grok-4` note above).
7. Document in README. Prompt caching / cache-control wiring is handled generically by
   `InstructorClient` (Anthropic `cache_control` block vs OpenAI `extra_body` cache key).
