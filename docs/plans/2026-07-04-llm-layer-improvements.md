# LLM Layer Improvements

## Overview

Fixes correctness bugs and modernizes the LLM provider layer (`src/llm/`) based on a
review against current Claude/OpenAI API best practices:

1. **Broken Anthropic model IDs** — `model_catalog.py` uses dotted IDs
   (`claude-sonnet-4.6`) that 404 against the real API (dashed: `claude-sonnet-4-6`);
   the catalog is also a year stale. Fix the static catalog and add a dynamic
   pricing source (LiteLLM community JSON) with disk cache + static fallback.
2. **Blind retries** — `generate_json` retries identical requests on invalid JSON.
   The dominant real cause is `max_tokens` truncation, which identical retries can
   never fix. Add truncation detection and retry-with-feedback (previous bad output
   + specific error in the retry prompt).
3. **Deprecated Anthropic API usage** — `output_format` param (deprecated → use
   `output_config.format`), beta header no longer needed, `temperature` passed
   unconditionally (400s on Opus 4.7+/Sonnet 5), Pydantic `ge`/`le` constraints
   produce `minimum`/`maximum` keys that structured outputs reject.
4. **Uncalibrated filter scores** — the job filter asks for a raw 0-100 self-rated
   score with no anchoring examples; two-threshold routing (30/70) makes calibration
   load-bearing. Add few-shot examples and reorder the schema so reasoning is
   generated before the score.
5. **Small cleanups** — shallow `basic_validate_json_schema` (DeepSeek path),
   `kwargs.pop("max_tokens")` inside the Anthropic retry loop losing the caller's
   value on attempt 2+.

### Research: dynamic model/pricing loading

- Anthropic Models API (`GET /v1/models`) returns IDs, context window, and
  capabilities — **no pricing**. OpenAI `GET /v1/models` returns only IDs — no
  pricing, no capabilities. Neither vendor exposes prices programmatically.
- De-facto standard pricing source: LiteLLM's community-maintained
  `model_prices_and_context_window.json`
  (`https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`)
  — per-model input/output cost per token, context window, and capability flags
  (`supports_response_schema`) for OpenAI, Anthropic, DeepSeek, xAI and others.
  Updated within days of model launches.
- Chosen approach (user decision): **LiteLLM JSON + static fallback.** Fetch on
  startup + daily refresh, cache to disk, fall back to the fixed static catalog
  when offline. Provider `/v1/models` cross-checking deliberately skipped.

## Context (from discovery)

- Files involved: `src/llm/model_catalog.py`, `src/llm/base.py`,
  `src/llm/schema_strict.py`, `src/llm/providers/anthropic.py`,
  `src/llm/providers/_openai_compatible.py`, `src/config/settings.py`,
  `src/context.py`, `src/services/jobs/scheduler.py`,
  `src/services/cv/cv_composer.py`, `src/services/jobs/job_filter.py`,
  `src/models/job_filter.py`, `prompts/job_filter/default_filter_prompt.system.txt`
- Patterns to reuse: `reasoning_model_prefixes` gating in `_openai_compatible.py`
  (model-prefix feature gating), `PromptSpec` system/user split (feedback must be
  appended to the user side to preserve the cacheable prefix), `AppContext` DI,
  APScheduler for periodic jobs.
- Current-gen Anthropic models + pricing (verified 2026-07-04):
  `claude-opus-4-8` ($5/$25 per 1M), `claude-sonnet-5` ($3/$15),
  `claude-sonnet-4-6` ($3/$15), `claude-haiku-4-5` ($1/$5).
  `temperature`/`top_p`/`top_k` are rejected (400) on Opus 4.7+, Opus 4.8,
  Sonnet 5, and Fable 5.

## Development Approach

- **Testing approach**: Regular (code first, then tests) — user preference
- Complete each task fully before moving to the next
- Make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
  - tests are not optional — they are a required part of the checklist
  - cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** — no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- All LLM SDK calls mocked in tests — no network
- Maintain backward compatibility: `generate_json` signature gains only optional
  params; catalog helpers keep signatures with a `catalog` parameter defaulting to
  the static list

## Testing Strategy

- **Unit tests**: required for every task; mock `openai.OpenAI` / `anthropic.Anthropic`
  clients and `httpx` — assert on request shapes, not live responses
- **E2E tests**: none required (no UI changes); Task 7 includes a manual live smoke
  test under Post-Completion

## Progress Tracking

- Mark completed items with `[x]` immediately when done
- Add newly discovered tasks with ➕ prefix
- Document issues/blockers with ⚠️ prefix
- Update plan if implementation deviates from original scope

## Implementation Steps

### Task 1: Fix static catalog and settings defaults (correctness — do first)

The static catalog remains the offline fallback, so it must be correct regardless
of Task 2.

- [x] `src/llm/model_catalog.py`: replace dotted Anthropic IDs with dashed real IDs
      and refresh the lineup — `claude-opus-4-8` (5.00/25.00), `claude-sonnet-5`
      (3.00/15.00), `claude-sonnet-4-6` (3.00/15.00), `claude-haiku-4-5` (1.00/5.00);
      update `PRICING_SNAPSHOT_DATE`
- [x] `src/config/settings.py`: change `anthropic_model` default
      `"claude-sonnet-4.5"` → `"claude-sonnet-4-5"`
- [x] grep repo for other dotted `claude-*` strings (`src/agents/_shared.py`
      docstring, tests, UI fixtures) and fix
- [x] write test: no Anthropic catalog entry contains a dotted version segment
      (regex guard so this cannot regress)
- [x] write tests: `get_catalog_for_operation` filtering/sorting still correct with
      the new entries
- [x] run tests — must pass before task 2

### Task 2: Dynamic pricing catalog from LiteLLM JSON

- [x] create `src/llm/pricing_source.py`: async fetcher for
      `https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`
      via httpx (~10s timeout)
- [x] parser → `list[ModelCatalogEntry]`: keep `litellm_provider in
      {openai, anthropic, deepseek, xai}` and `mode == "chat"`; strip provider
      prefixes from keys (`xai/grok-4` → `grok-4`, `deepseek/deepseek-chat` →
      `deepseek-chat`); map `input_cost_per_token * 1e6` → `input_cost_per_1m`
      (same for output); `supports_response_schema` → `supports_strict_schema`
- [x] noise filter: skip dated snapshots (key matching `-\d{8}$` / `@\d{8}` when an
      alias exists) and non-chat variants (audio/realtime/embedding — name blocklist,
      since LiteLLM tags some realtime variants `mode:"chat"`); dedup prefixed+bare
- [x] disk cache `data/model_catalog_cache.json` with `fetched_at`, TTL 24h; load
      order: fresh cache → refetch → stale cache → static `MODEL_CATALOG`
- [x] wire into `AppContext` (`src/context.py`): `model_catalog` field + async
      `refresh_model_catalog()`; loaded at startup as a non-blocking background task.
      ➕ DEVIATION: daily refresh runs on a dedicated `ModelCatalogScheduler`
      (`src/services/jobs/model_catalog_scheduler.py`) reusing the `IntervalScheduler`
      (APScheduler) base — not attached to the LinkedIn search scheduler, which may be
      disabled. Started/stopped in the app lifespan.
- [x] switch catalog consumers (models-for-operation API endpoint feeding the UI
      dropdown) to read the context-held catalog; `get_catalog_for_operation` gains a
      `catalog` param defaulting to the static list (`build_label` is per-entry — no
      catalog param needed)
- [x] write tests: parser against a saved LiteLLM JSON fixture (prefix stripping,
      cost math, capability mapping, noise filter)
- [x] write tests: fallback chain (fetch fails → stale cache → static) and TTL
      logic; httpx mocked
- [x] run tests — must pass before task 3

### Task 3: Truncation detection + retry-with-feedback in `generate_json`

Applies to both `src/llm/providers/_openai_compatible.py` and
`src/llm/providers/anthropic.py`.

- [x] add `LLMTruncatedError` to `src/llm/base.py` (+ `DEFAULT_MAX_TOKENS`,
      `build_retry_feedback` helper)
- [x] after each call, check `choices[0].finish_reason` (OpenAI-compatible) /
      `response.stop_reason` (Anthropic); on `length`/`max_tokens`: retry once with
      `max_tokens` doubled, then raise `LLMTruncatedError` — never blind-retry the
      identical request on truncation
- [x] on retry triggered by `json.JSONDecodeError` or schema validation: append to
      the retry's **user** message (after the cacheable prefix) the previous bad
      output (truncated ~1k chars) + the specific error text
- [x] add optional `validator: Callable[[dict], None]` param to `generate_json`
      (raises `ValueError` with message → same feedback loop)
- [x] pass Pydantic validators from consumers: `CVComposer._summarize_job`
      (`JobSummary`), `JobFilter.evaluate_job` (`FilterResult`) — validation errors
      re-prompt instead of failing the job
- [x] write tests: truncated response → one doubled-`max_tokens` retry →
      `LLMTruncatedError` if still truncated
- [x] write tests: invalid-then-valid sequence → second request body contains prior
      output + error text; validator failure feeds Pydantic message into retry
- [x] run tests — must pass before task 4

### Task 4: Modernize the Anthropic client

All in `src/llm/providers/anthropic.py` unless noted.

- [x] replace deprecated `output_format` with
      `output_config={"format": {"type": "json_schema", "schema": ...}}` in
      `generate_json` and `generate_json_from_pdf`; remove the
      `structured-outputs-2025-11-13` beta header (feature is GA)
- [x] temperature gating: add `SAMPLING_UNSUPPORTED_PREFIXES = ("claude-opus-4-7",
      "claude-opus-4-8", "claude-sonnet-5", "claude-fable")` and skip `temperature`
      for matching models (also applied to `generate` + `generate_json_from_pdf`)
- [x] add `make_schema_anthropic_safe()` to `src/llm/schema_strict.py`: recursively
      strip constraints structured outputs reject (`minimum`, `maximum`,
      `minLength`, `maxLength`, `multipleOf`, `minItems`/`maxItems`/`uniqueItems`,
      …) and ensure `additionalProperties: false`; applied in `generate_json` +
      `generate_json_from_pdf` (stripped constraints remain enforced client-side
      via the Task 3 validator). Property keys named like a constraint are preserved.
- [x] add usage logging parity with the OpenAI base (input/cached token counts)
- [x] write tests (mocked `Anthropic` client, request-shape assertions):
      `output_config` present + no beta header; no `temperature` for
      `claude-sonnet-5`; sanitized schema contains no `minimum`/`maximum`
- [x] write tests: `generate_json_from_pdf` uses `output_config` shape
- [x] run tests — must pass before task 5

### Task 5: Filter prompt calibration

- [x] reorder `FilterResult` fields in `src/models/job_filter.py` to: `reasoning`,
      `red_flags`, `disqualified`, `disqualifier_reason`, `score` — JSON schema
      property order follows field order, so the model commits to evidence before
      the verdict (stored JSON is key-based; DB/UI unaffected)
- [x] `prompts/job_filter/default_filter_prompt.system.txt`: add 3 few-shot examples
      (condensed posting → full JSON output) anchoring the bands — user-criteria
      hard exclusion (~15), warn-band with 2 red flags (~58), clean match (~92)
- [x] add normalization rules ("score reflects ONLY the listed criteria; do not
      reward prestige/salary") and state routing semantics (below 30 auto-reject,
      30-69 warning badge, 70+ clean) so scores land meaningfully relative to thresholds
- [x] update the prompt's output-field list to match the new field order
- [x] write test: `FilterResult.model_json_schema()` property order assertion
- [x] verify existing `JobFilter` tests pass; prompt template still renders with
      `$user_criteria_section` substitution
- [x] run tests — must pass before task 6

### Task 6: Cleanups

- [x] `src/llm/base.py`: reimplement `basic_validate_json_schema` on the
      `jsonschema` library (added dep via `uv add jsonschema`); keeps name/signature,
      wraps `jsonschema.ValidationError` → `ValueError` (with the failing path) so the
      Task 3 feedback loop picks up the message (upgrades the DeepSeek path to full
      validation)
- [x] `src/llm/providers/anthropic.py`: `max_tokens` hoisted out of the retry loop
      (done as part of Task 3's rewrite — popped once before the loop)
- [x] write test: nested-schema violation now caught on the DeepSeek
      (non-strict-schema) path (+ recovery-after-feedback)
- [x] write test: custom `max_tokens` preserved across retry attempts (both providers)
- [x] run tests — must pass before task 7

### Task 7: Verify acceptance criteria

- [x] verify all requirements from Overview are implemented (incl. the added
      requirement: dynamic source supplies the up-to-date model *list*, not just prices)
- [x] run full test suite (`pytest tests/unit`) — 786 passed, 3 skipped (WeasyPrint
      libs unavailable locally; pre-existing)
- [x] run `mypy` on touched modules — no new error signatures vs master (verified by
      diffing normalized mypy output before/after; all remaining errors are the
      codebase's pre-existing untyped-SDK-call pattern; the two new modules are clean)
- [x] `black` — new modules (`pricing_source.py`, `model_catalog_scheduler.py`) are
      black-clean; the files clean on master stayed clean. ➕ NOTE: `black --check src/`
      is NOT clean repo-wide (49 pre-existing non-compliant files, unrelated to this
      work); a blanket reformat is out of scope to avoid unrelated churn.
- [x] test coverage: every task added focused unit tests (catalog, pricing_source,
      generate_json truncation/feedback/validator, anthropic client, filter model)

### Task 8: [Final] Update documentation

- [x] update `CLAUDE.md`: model catalog section (dynamic LiteLLM source + fallback),
      "strict schema support" notes (Anthropic beta header no longer required,
      `output_config.format`, schema sanitize, temperature gating), generate_json
      resilience notes, new env/config (cache path, refresh interval, url, enable flag)
- [x] update README if it lists supported models — README does not list models; no change

*Note: ralphex automatically moves completed plans to `docs/plans/completed/`*

## Technical Details

- **LiteLLM JSON shape** (per model key):
  `{"litellm_provider": "anthropic", "mode": "chat", "input_cost_per_token": 3e-06,
  "output_cost_per_token": 1.5e-05, "max_input_tokens": ..., "supports_response_schema": true}`
  — keys are model IDs, sometimes prefixed `provider/model` for non-OpenAI/Anthropic
  providers (`xai/`, `deepseek/`).
- **Cache file**: `{"fetched_at": "<iso8601>", "entries": [<ModelCatalogEntry dicts>]}`.
- **Feedback message format** (appended to retry user message):
  `"Your previous response was invalid.\nPrevious response (truncated):\n<...>\n
  Error: <error>\nReturn corrected JSON matching the schema."`
- **Retry flow in `generate_json`**: call → truncation check → parse → optional
  array-unwrap (OpenAI path) → schema/basic validation → optional `validator`
  callback → return; any failure after the truncation check builds a feedback spec
  and loops (bounded by existing `max_retries`).
- **Field-order reliance**: Pydantic v2 `model_json_schema()` emits `properties` in
  field-declaration order; generation follows schema order under strict/structured
  modes — this is what makes reasoning-before-score effective.

## Post-Completion

**Manual verification** (needs real API keys — not in CI):
- One live `JobFilter.evaluate_job` call per configured provider (OpenAI, Anthropic,
  DeepSeek, Grok): confirms real Anthropic model IDs resolve, `output_config`
  request shape accepted, temperature gating correct on a current-gen model
- Confirm the LiteLLM fetch populates the UI model dropdown with current models and
  prices; kill the network and confirm the static fallback keeps the app working
- Spot-check filter scores on a handful of real postings against the 30/70
  thresholds after the prompt calibration lands

**Deliberately out of scope** (separate future work):
- Anthropic prompt caching (`cache_control` on the system block) — biggest cost
  lever, tracked separately
- Provider fallback on transient errors, Batch API for scheduled filtering,
  async SDK clients
