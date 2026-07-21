# Feature Specification: Migrate the LLM Layer to Instructor + LiteLLM

## Overview
- **Feature**: Replace the hand-rolled multi-provider LLM abstraction (`src/llm/`) with [Instructor](https://python.useinstructor.com/) for structured output, backed by [LiteLLM](https://github.com/BerriAI/litellm) for provider routing.
- **Status**: Draft
- **Created**: 2026-07-21
- **Author**: User + Claude Code

## Problem Statement

`src/llm/` is ~1,400 lines of custom infrastructure that reimplements what two mature, broadly-adopted libraries now do for us:

- Four provider adapters (`OpenAIClient`, `GrokClient`, `DeepSeekClient`, `AnthropicClient`) plus an `OpenAICompatibleClient` base and a factory.
- A hand-written `generate_json` retry-with-feedback loop (append bad output + error to the user message, re-ask up to `max_retries`).
- Per-provider JSON-Schema massaging: `make_schema_strict` (OpenAI/Grok), `make_schema_anthropic_safe` (Anthropic), `basic_validate_json_schema` (DeepSeek post-hoc validation).
- A truncation handler that doubles `max_tokens` once, then raises `LLMTruncatedError`.
- Model/pricing catalog logic that *already* sources data from LiteLLM's `model_prices_and_context_window.json`.

This is maintenance surface that duplicates Instructor's validation/re-ask loop and LiteLLM's provider normalization. Every new model quirk (e.g. `SAMPLING_UNSUPPORTED_PREFIXES` for Opus 4.7/4.8, Sonnet 5, Fable) is a manual patch. Instructor is Pydantic-native — the same idiom the rest of the codebase already uses (`FilterResult`, `JobSummary`, `CVLLMOutput`) — so the structured-output call sites map almost 1:1.

## Goals & Success Criteria

- Replace the four custom provider clients and the bespoke `generate_json` loop with Instructor (`instructor.from_litellm(...)`) + LiteLLM routing.
- Preserve the public `BaseLLMClient` interface so call sites (`CVComposer`, `JobFilter`, `pdf_extraction`) change minimally.
- **No behavioral regression** on the two things that cost real money and were hard-won: prompt caching and structured-output correctness.
- Reduce `src/llm/` net line count and eliminate per-provider schema-transform code where LiteLLM/Instructor make it redundant.
- **Success Metrics**:
  - All existing `tests/unit/test_llm_*`, `test_job_filter_*`, `test_anthropic_client`, `test_pdf_extraction_api` pass (updated where interface genuinely changed, not weakened).
  - A parity check confirms `cache_read_input_tokens` (Anthropic) and `cached_tokens` (OpenAI) still fire on the second identical call for CV compose and job filter.
  - Job filter and CV compose produce schema-valid `FilterResult` / `CVLLMOutput` across all four providers in a smoke run.
  - Net deletion in `src/llm/` (target: remove `_openai_compatible.py`, `providers/openai.py`, `grok.py`, `deepseek.py`, most of `anthropic.py`, and `schema_strict.py` if the drop_params bet holds).

## User Stories

1. As the **maintainer**, I want provider support and model-quirk handling to come from a maintained library, so that new models work without me patching prefix tuples.
2. As the **maintainer**, I want structured output to be Pydantic-model-driven end to end, so that the schema, validation, and re-ask loop are one thing instead of three.
3. As a **user of the app**, I want no increase in latency or LLM cost, so prompt caching must keep working after the migration.
4. As the **maintainer**, I want a single code path for text, JSON, and PDF-input generation, so there aren't two SDK stacks to reason about.

## Functional Requirements

### Core Capabilities

- **CR-1 — Unified provider routing**: One Instructor client built via `instructor.from_litellm(completion)` handles OpenAI, Anthropic, DeepSeek, and Grok. Provider+model+key selection stays driven by `settings.primary_llm_provider` and the per-provider `*_model` / `*_api_key` settings, resolved in `create_llm_client()` (`src/agents/_shared.py`). **All four providers stay** — the recent branch work only *curates the model picker list*, it does not drop a provider.
- **CR-1a — Model-string prefixing**: `create_llm_client` must convert a bare model id (the form the catalog stores after `_strip_provider_prefix`, e.g. `claude-opus-4-8`, `grok-4`, `deepseek-chat`, `gpt-4o`) into a LiteLLM-prefixed string via a provider→prefix map. **Gotcha**: our enum value is `grok` but LiteLLM's prefix is `xai/`. The map is the *inverse* of `pricing_source._PROVIDER_MAP`: `{OPENAI:"openai", ANTHROPIC:"anthropic", DEEPSEEK:"deepseek", GROK:"xai"}` → `f"{prefix}/{bare_model}"`.
- **CR-2 — Structured JSON output**: `generate_json` returns a validated result for a caller-supplied Pydantic model (or JSON-schema dict for the one ad-hoc case). Instructor performs client-side Pydantic validation and re-asks on failure (replacing the custom `build_retry_feedback` loop).
- **CR-3 — Plain text generation**: `generate` (used by `JobFilter.generate_prompt_from_preferences`) routes through `litellm.completion()` directly (no Instructor — it's unstructured) and returns the message content string.
- **CR-4 — Native PDF input**: `generate_json_from_pdf` is reimplemented on top of Instructor's multimodal/document support so it also gets `response_model` validation. Must work on both OpenAI and Anthropic PDF-capable models. `supports_pdf` capability check (`src/api/routes/users.py`) is preserved.
- **CR-5 — Prompt caching preserved (both providers)** — *wiring idioms verified by spike, see Appendix A*:
  - **Anthropic**: send `PromptSpec.system` as a **content-block list** with `cache_control: {type: "ephemeral"}` on the block. LiteLLM maps this to Anthropic's top-level `system` array carrying `cache_control` (**confirmed on the wire**). Preserves the per-user cached prefix (instructions + schema + master CV).
  - **OpenAI-compatible**: `PromptSpec.cache_key` must be passed as **`extra_body={"prompt_cache_key": ...}`** — a bare `prompt_cache_key=` kwarg is **silently dropped by LiteLLM** (confirmed). Note OpenAI auto-caches on prefix regardless, so this is only the routing hint.
- **CR-6 — Retry semantics**: Instructor's built-in (Tenacity-based) retry replaces the custom validation/JSON retry-with-feedback loop. `max_retries` from call sites maps to Instructor's `max_retries`.

### User Flows

This is an internal refactor — no end-user UI flow changes. The developer-facing flow:

1. `create_llm_client(provider, model)` resolves settings and returns a `BaseLLMClient` (now Instructor-backed).
2. `CVComposer` / `JobFilter` call `self.llm.generate_json(spec, response_model=..., temperature=...)`.
3. The adapter builds LiteLLM messages from `PromptSpec` (system + user, with cache breakpoints), calls the Instructor-patched `completion`, and returns the validated model instance (or its `.model_dump()` where callers expect a dict).
4. `pdf_extraction.run_extraction` calls `generate_json_from_pdf(pdf_bytes, prompt, response_model)`.

### Data Model

No persisted data model changes. Interface-level model changes:

- **`PromptSpec`** (`src/llm/prompt_spec.py`) — **kept**. It is the caching contract. `system`/`user`/`cache_key` semantics unchanged; the adapter now translates it into LiteLLM message blocks with cache breakpoints.
- **`generate_json` signature** — gains an optional `response_model: type[BaseModel] | None`. The existing `schema: dict | None` and `validator` params are retained for the one call site that passes a raw dict (`JobFilter.generate_refinement` → `_REFINEMENT_SCHEMA`). Preference order: `response_model` > `schema`. For the raw-dict case, either (a) introduce a small `FilterRefinement` Pydantic model, or (b) use Instructor's JSON/dict mode. **Recommended: (a)** — it's two fields and removes the last non-Pydantic path.
- **`LLMTruncatedError`** — **removed** (see Design Trade-offs; truncation handling is dropped in favor of Instructor retries). Any test/asserts on this type are updated.

### Integration Points

- `src/agents/_shared.py::create_llm_client` — the single construction site; rewired to build the Instructor client. Everything downstream is unaffected by the provider switch.
- `src/services/cv/cv_composer.py` — `_summarize_job` (`JobSummary`) and `_compose_all_sections` (`CVLLMOutput`) switch from `schema=...model_json_schema()` + `validator=lambda` to `response_model=...`.
- `src/services/jobs/job_filter.py` — `evaluate_job` (`FilterResult`), `generate_refinement` (`FilterRefinement`), `generate_prompt_from_preferences` (text via `generate`).
- `src/services/cv/pdf_extraction.py` — `generate_json_from_pdf` path.
- `src/api/routes/users.py` — `LLMClientFactory.supports_pdf` check retained (or reimplemented as a capability lookup).
- `src/models/cv.py:199` — docstring example referencing `llm.generate_json(prompt, schema=schema)` updated.
- `src/llm/model_catalog.py` / `pricing_source.py` — **unchanged**; already LiteLLM-sourced, and the recent picker-curation filter (`parse_litellm_json`: blocklist, deny fine-tunes/previews, deprecation + `_MIN_CONTEXT_TOKENS` cuts, dated-snapshot collapse) is orthogonal to the call path. LiteLLM becoming a runtime dep is strictly additive. **Two facts this layer imposes on the migration**: (a) it stores *bare* model ids → see CR-1a for prefix reattachment; (b) its `supports_strict_schema` flag (from LiteLLM `supports_response_schema`) **no longer gates the request** under Instructor `Mode.TOOLS` — it becomes display-only picker metadata. Do not branch the new client on it.

## Technical Design

### Architecture

**Keep the `BaseLLMClient` adapter; replace its guts (big-bang).** A single new implementation (working name `InstructorClient`) satisfies the existing `BaseLLMClient` ABC (`generate`, `generate_json`, `generate_json_from_pdf`) and is returned by the factory for every provider. Provider identity collapses into a LiteLLM model string (`"anthropic/claude-sonnet-4-5"`, `"openai/gpt-4o"`, `"deepseek/deepseek-chat"`, `"xai/grok-2-1212"`) plus the right API key.

**Structured-output mechanism (verified):** `instructor.from_litellm(litellm.completion)` defaults to **`Mode.TOOLS`** — structured output is coerced via **tool-calling** (forced `tool_choice`), *not* the strict `output_config`/`response_format` structured-outputs format that the current `AnthropicClient`/`OpenAIClient` use. This is the single most important finding: the tool `input_schema` path is far more lenient about JSON-Schema constraint keywords than the strict path, which is what makes dropping `schema_strict.py` viable (see Trade-offs + Appendix A).

```
create_llm_client(provider, model)
        │  resolves settings → (litellm_model_str, api_key)
        ▼
InstructorClient(BaseLLMClient)
   ├── client = instructor.from_litellm(litellm.completion)
   ├── generate(spec, temperature)            → litellm.completion(...).choices[0].message.content
   ├── generate_json(spec, response_model)     → client.chat.completions.create(response_model=..., max_retries=...)
   └── generate_json_from_pdf(bytes, prompt)   → Instructor multimodal (document/image block) + response_model
              │
              ▼
        LiteLLM completion  ──►  OpenAI / Anthropic / DeepSeek / xAI
        (drop_params=True; cache_control + prompt_cache_key passed through)
```

The factory (`LLMClientFactory`) is simplified to always return `InstructorClient` (or retired, with `create_llm_client` constructing directly). `LLMProvider` enum stays for settings validation.

### Technology Stack

- **New runtime dependencies**: `instructor`, `litellm`.
- **Retained**: `pydantic>=2.5.3`, `pydantic-settings`. `openai` / `anthropic` SDKs remain transitive deps of LiteLLM; direct imports in `src/llm/providers/*` are removed.
- **Removed/retired code** (pending the drop_params validation gate): `src/llm/providers/_openai_compatible.py`, `providers/openai.py`, `providers/grok.py`, `providers/deepseek.py`; the bulk of `providers/anthropic.py`; `schema_strict.py`; `basic_validate_json_schema`, `build_retry_feedback`, `LLMTruncatedError`, `DEFAULT_MAX_TOKENS` from `base.py`.
- **`jsonschema`** dep: keep only if something else uses it; otherwise remove with `basic_validate_json_schema`.

### Data Persistence

None. No DB, migration, or on-disk format change. `data/model_catalog_cache.json` behavior is untouched.

### API / Interface Design

`BaseLLMClient` (unchanged public shape, `response_model` added):

```python
class BaseLLMClient(ABC):
    SUPPORTS_PDF_INPUT: bool = False
    model: str

    def generate(self, spec: PromptSpec, temperature: float = 0.7, **kwargs) -> str: ...

    def generate_json(
        self,
        spec: PromptSpec,
        response_model: type[BaseModel] | None = None,  # NEW, preferred
        schema: dict | None = None,                     # retained for ad-hoc dicts
        temperature: float = 0.4,
        max_retries: int = 3,
        validator: Callable[[dict], None] | None = None,  # mostly obsolete; kept for dict path
        **kwargs,
    ) -> dict | BaseModel: ...

    def generate_json_from_pdf(
        self, pdf_bytes: bytes, prompt: str,
        response_model: type[BaseModel] | None = None,
        *, temperature: float = 0.1, max_tokens: int = 8192,
    ) -> dict | BaseModel: ...
```

Call-site shape after migration (job filter):

```python
result = self.llm.generate_json(spec, response_model=FilterResult, temperature=self.TEMPERATURE)
# result is already a validated FilterResult — the trailing FilterResult(**raw) re-validation is deleted.
```

**Cache-control translation** (adapter internal): `generate_json` converts `PromptSpec` into LiteLLM messages, attaching `cache_control` to the system content block for Anthropic models and setting `prompt_cache_key=spec.cache_key` in the completion kwargs for OpenAI-compatible models.

## Code Inventory: Delete / Keep

Audit of `src/llm/` (and adjacent) against what Instructor + LiteLLM provide natively. LOC are approximate. "Confidence" = how safe the deletion is.

### Delete — redundant once the migration lands

| # | Code | ~LOC | Replaced by | Confidence |
|---|---|---|---|---|
| D1 | `providers/_openai_compatible.py` | 231 | LiteLLM provider routing | High (core) |
| D2 | `providers/anthropic.py` (all but any bespoke bits worth porting) | ~200 | LiteLLM + Instructor | High (core) |
| D3 | `providers/openai.py`, `providers/grok.py`, `providers/deepseek.py` | 127 | LiteLLM provider strings (`openai/`, `xai/`, `deepseek/`) | High (core) |
| D4 | `factory.py` (+ `LLMClientFactory`) | 35 | Collapses to one `InstructorClient` | High |
| D5 | `build_retry_feedback` + both retry-with-feedback loops | ~40 | Instructor Tenacity retry + Pydantic re-ask | High |
| D6 | `basic_validate_json_schema` **and the `jsonschema` dependency** (only consumer) | ~20 + dep | Instructor validates the Pydantic model client-side | High |
| D7 | `LLMTruncatedError` + truncation-doubling + `DEFAULT_MAX_TOKENS` | ~30 | Dropped per decision (rely on Instructor retries) | High (decided) |
| D8 | Temperature gating: `_is_reasoning_model`, `SAMPLING_UNSUPPORTED_PREFIXES`, `reasoning_model_prefixes` | ~20 | `litellm.drop_params=True` | Medium |
| D9 | Custom PDF base64/document-block construction inside `generate_json_from_pdf` (both OpenAI Responses + Anthropic document paths) | ~90 | Instructor multimodal | Medium |
| D10 | `schema_strict.py` — **both** `make_schema_strict` and `make_schema_anthropic_safe` | ~210 | Instructor uses tool-calling (`Mode.TOOLS`), not strict-schema, so no consumer | Medium-High — **behind the live-call gate** (spec Trade-offs / Appendix A) |

**Estimated deletion: ~800+ LOC and one dependency (`jsonschema`).** `provider.py` (re-export shim) and `base.py` shrink to just the retained `BaseLLMClient` ABC + `LLMProvider` enum.

### Keep — not redundant (do not delete)

| Code | Why it stays |
|---|---|
| `prompt_spec.py` (`PromptSpec`) | App-specific cache-prefix structure; LiteLLM has no equivalent. The caching contract (CR-5). |
| `pricing_source.py` — **entire file, incl. HTTP fetch / disk cache / TTL / stale-fallback** | **Kept by explicit decision (2026-07-21).** LiteLLM ships the same data in-process as `litellm.model_cost`, but the daily live-fetch path gives fresher model lists than the pinned library version — a deliberate product choice. Not touched by this migration. |
| `model_catalog_scheduler.py` + `AppContext.refresh_model_catalog` | Drives the daily refresh of the kept `pricing_source`. Stays. |
| `model_catalog.py` (`MODEL_CATALOG` fallback, `ModelCatalogEntry`, `parse_litellm_json` curation, `get_catalog_for_operation`, `build_label`) | Product logic — which models the picker shows. LiteLLM gives raw data, not curation. This is the recent branch work. |
| `BaseLLMClient.SUPPORTS_PDF_INPUT` + `LLMClientFactory.supports_pdf` capability check | `litellm.supports_pdf_input` **does not exist** in litellm 1.93.0 — cannot be replaced by a library call. Keep the flag (re-home `supports_pdf` if the factory is removed). |
| `LLMProvider` enum | Still needed for settings validation and the provider→LiteLLM-prefix map (CR-1a). |

### Caution — do NOT lean on LiteLLM library lookups for the picker
The spike found `litellm.supports_function_calling`/`model_cost` lookups are sensitive to exact key form (a bare `claude-3-5-sonnet-20241022` resolved to `False`/not-found). Your curated `MODEL_CATALOG` + `parse_litellm_json` remain the source of truth for capability flags shown in the UI; do not swap them for `litellm.supports_*` calls.

## Non-Functional Requirements

- **Performance**: No latency regression. Prompt caching (CR-5) is the primary control; acceptance test must observe non-zero cached tokens on repeat calls. LiteLLM's per-call overhead is negligible at this app's request volume (single self-hosted user).
- **Security**: API keys continue to come from `Settings` / `.env`; no keys logged. No new external network egress beyond the same provider endpoints (LiteLLM calls them directly; **no** LiteLLM proxy server is introduced).
- **Observability**: Preserve the `[TIMING]` log lines and the input/cached-token usage logging currently in `_log_usage`. Read usage off LiteLLM's response (`response.usage`, `_hidden_params`) or Instructor's `create_with_completion` to keep the cached-token log line.
- **Error Handling**: Instructor raises `instructor.exceptions.InstructorRetryException` (wrapping the last validation/provider error) after exhausting `max_retries`. Adapter catches and re-raises as the domain errors call sites already expect (`JobFilterError`, `CVCompositionError`) — those wrappers live in the services and are unchanged. Truncation is no longer a distinct typed error (see trade-offs).

## Implementation Considerations

### Design Trade-offs

- **Keep adapter vs. rewrite call sites** → **Keep `BaseLLMClient`.** Smallest blast radius; `CVComposer`/`JobFilter`/`pdf_extraction` and their tests barely move. The internal provider implementations are replaced big-bang in one PR, but the seam callers depend on is stable, so rollback = revert one adapter file.
- **Instructor + LiteLLM vs. native SDKs** → **LiteLLM.** One `completion` surface for all four providers; we already depend on LiteLLM's data for pricing, so it's a coherent single source. Cost: `litellm` becomes a runtime dep (heavier install). Accepted.
- **Drop truncation-doubling** (user decision) → Remove `LLMTruncatedError` and the `max_tokens`-doubling. **Risk**: a genuinely over-budget response now surfaces as an Instructor validation/parse failure and burns `max_retries` identical-ish attempts instead of deterministically growing the budget, and the typed `LLMTruncatedError` contract disappears. **Mitigation**: set sensible default `max_tokens` per call site (CV compose is the large one); optionally have the adapter bump `max_tokens` modestly on Instructor retry via a hook if this proves flaky in practice. Documented as an accepted simplification, revisitable.
- **Trust LiteLLM `drop_params` for provider quirks** (user decision) → Drop `SAMPLING_UNSUPPORTED_PREFIXES`, `make_schema_anthropic_safe`, **and** `make_schema_strict`. **Spike-confirmed rationale**: Instructor uses tool-calling (`Mode.TOOLS`), so the schema is sent as a tool `input_schema` — not the strict `output_config`/`response_format` format that requires `make_schema_*`. On the Anthropic wire, `FilterResult`'s `minimum`/`maximum` ride along inside the tool `input_schema`, which Anthropic tolerates (400-risk: **LOW**). Meanwhile Instructor validates the response against the Pydantic model client-side and re-asks, so `ge`/`le`/`max_length` stay enforced regardless of the wire. `litellm.drop_params=True` covers the sampling gate (temperature on Opus 4.8 / Sonnet 5 / Fable). **Residual risk (small)**: an Anthropic API version could reject a constraint keyword even in tool schemas. **Validation gate**: keep one live Anthropic call in the smoke suite (`evaluate_job`, which carries `minimum`/`maximum`) green before deleting the shims. If it 400s on schema shape, restore `make_schema_anthropic_safe` as a thin pre-flight transform only.
- **Ad-hoc dict schema** (`_REFINEMENT_SCHEMA`) → introduce a 2-field `FilterRefinement` Pydantic model so every path is `response_model`-based and the `schema`/`validator` params can eventually be retired.

### Dependencies

- Add `instructor` and `litellm` to `pyproject.toml`; `uv lock` / `uv sync` (project uses **uv**, not pip).
- LiteLLM model-string mapping for each provider (esp. xAI/Grok prefix and DeepSeek) must be confirmed against LiteLLM's provider docs.
- No blockers; no infra changes (no proxy server).

### Testing Strategy

- **Unit**: Rework `tests/unit/test_llm_generate_json.py`, `test_anthropic_client.py`, `test_llm_models_api.py` against the new adapter. Mock at the LiteLLM boundary (`litellm.completion`) or use Instructor's test utilities so we assert: (a) `response_model` validation returns the right type, (b) retry fires on invalid output, (c) cache_control is attached for Anthropic and `prompt_cache_key` for OpenAI, (d) `drop_params` path doesn't send temperature to sampling-restricted models.
- **Integration/smoke** (the parity gate): one live (or VCR-recorded) call per provider for `evaluate_job` and `compose_cv`, asserting schema validity and — for the caching providers — non-zero cached tokens on a repeat call. This is also the **drop_params validation gate** for the Anthropic schema-shape risk.
- **PDF**: extend `test_pdf_extraction_api.py` to cover the Instructor multimodal path on OpenAI + Anthropic.
- **Regression**: full `pytest` (uv) green; `mypy src/` clean; `black` formatted.

## Out of Scope

- Deploying a **LiteLLM proxy / network gateway** (virtual keys, multi-tenant budgets, dashboard). Not warranted for a single self-hosted app; explicitly deferred. (See prior research: revisit only if multi-tenant / multi-app.)
- Adopting **Pydantic AI** or any agent framework — would overlap with the existing LangGraph orchestration.
- Semantic caching, guardrails, PII redaction (Portkey-style features).
- Changing prompts, temperatures, thresholds, or CV/filter business logic.
- Model catalog / pricing source changes (already LiteLLM-based). **Explicitly declined (2026-07-21)**: the available simplification of feeding in-process `litellm.model_cost` into `parse_litellm_json` to drop the HTTP-fetch/cache/scheduler plumbing — rejected to keep the fresher daily live-fetch. `pricing_source.py` stays as-is. See Code Inventory → Keep.
- Adding new providers beyond the current four.

## Open Questions

1. **Anthropic schema-shape 400s** — *Largely RESOLVED by spike (Appendix A).* Instructor uses tool-calling, so `FilterResult`'s `minimum`/`maximum` ride the tolerant tool `input_schema` path → LOW 400-risk; `make_schema_anthropic_safe` and `make_schema_strict` appear unnecessary. **Residual**: one live Anthropic call (needs a real key) to confirm no 400 before deleting the shims — kept as the validation gate, not a blocker.
2. **Cache-control wiring** — *RESOLVED by spike (Appendix A).* Anthropic `cache_control` on system content-blocks reaches the wire correctly; OpenAI needs `extra_body={"prompt_cache_key": ...}` (bare kwarg is dropped). **Residual**: a live call to confirm `cache_read_input_tokens` / `cached_tokens` are actually **non-zero** on repeat (needs a real key) — mechanism is proven, only the runtime effect is unconfirmed.
3. **Usage/token logging** — Best way to keep the `cached_tokens` log line: `create_with_completion` vs. reading `response.usage` off LiteLLM. Confirm cached-read fields are populated for both providers.
4. **Truncation fallout** — After dropping `LLMTruncatedError`, do large CV compositions ever hit `max_tokens` and waste retries? Decide default `max_tokens` per call site; add a retry-time bump hook only if observed.
5. **Grok/DeepSeek model strings + tool-calling** — Confirm correct LiteLLM prefixes (`xai/`, `deepseek/` — see CR-1a) and that **tool-calling** works for each, since Instructor defaults to `Mode.TOOLS` (DeepSeek historically lacked strict schemas but supports function calling — verify). Unverified in the spike, which only exercised Anthropic + OpenAI. Since the picker now lists dynamically-fetched models, also confirm every listed model supports tool use (all current `mode:chat` models from these four providers do; the filter already drops legacy/non-chat variants) — or fall back to Instructor `Mode.JSON` for any that don't.

## Appendix A — Spike results (2026-07-21, offline HTTP-intercept)

Method: `instructor.from_litellm(litellm.completion)` with dummy keys; patched `httpx.Client.send` to capture the outbound request body and abort before any network call (zero spend, no real key). Versions: **instructor 1.15.4, litellm 1.93.0**.

| Finding | Result |
|---|---|
| Instructor default mode (`from_litellm`) | **`Mode.TOOLS`** (tool-calling, forced `tool_choice`) |
| Anthropic request shape | `tools` + `tool_choice={type:tool,name:...}`; **no** `output_config`/`response_format` |
| `FilterResult` constraints on Anthropic wire | `minimum`,`maximum` **present** in tool `input_schema` → tolerated by tool path (LOW 400-risk) |
| `CVLLMOutput` constraints | none in raw schema → none on wire |
| Anthropic `system` + `cache_control` | `system` sent as **content-block list** with `cache_control` present ✅ |
| OpenAI `prompt_cache_key` as bare kwarg | **dropped** (`None` on wire) ❌ |
| OpenAI `prompt_cache_key` via `extra_body` | **`'filter:spike-user'`** on wire ✅ |

**Not covered (need a real key, deferred to the migration's smoke gate):** does Anthropic actually accept the tool schema without a 400; do `cache_read_input_tokens`/`cached_tokens` come back non-zero on a repeat call; Grok + DeepSeek behavior.

## References
- Current layer: `src/llm/base.py`, `provider.py`, `factory.py`, `prompt_spec.py`, `schema_strict.py`, `providers/*.py`
- Call sites: `src/agents/_shared.py`, `src/services/cv/cv_composer.py`, `src/services/jobs/job_filter.py`, `src/services/cv/pdf_extraction.py`, `src/api/routes/users.py`
- Related plan: `docs/plans/2026-07-04-llm-layer-improvements.md`
- [Instructor docs](https://python.useinstructor.com/) · [Instructor + LiteLLM](https://docs.litellm.ai/docs/tutorials/instructor) · [Instructor structured outputs + Anthropic prompt caching](https://python.useinstructor.com/blog/2024/10/23/structured-outputs-and-prompt-caching-with-anthropic/)
- [LiteLLM prompt caching](https://docs.litellm.ai/docs/completion/prompt_caching) · [LiteLLM Anthropic provider](https://docs.litellm.ai/docs/providers/anthropic)
