# Feature Specification: Per-Operation LLM Model Picker

## Overview
- **Feature**: Per-operation LLM model selection in Settings UI with cost transparency
- **Status**: Draft
- **Created**: 2026-04-18
- **Author**: User + Claude Code

## Problem Statement

The system makes LLM calls for several distinct operations (CV composition, job filtering, filter-prompt generation), each with very different cost and quality trade-offs. Today the model is chosen globally in `.env` (`PRIMARY_LLM_PROVIDER` + `{provider}_MODEL`), so a user who wants a premium model for CV writing but a cheap model for bulk job filtering has no way to express that. Users also cannot see the cost they are committing to when they change models.

We want to let each user, in the Settings UI, pick an LLM model **per operation** from a dropdown that shows `PROVIDER MODEL (COST)`, where the cost is current per-million-token input/output pricing.

## Goals & Success Criteria

- Users can pick a different model for each distinct LLM operation from the Settings page.
- Dropdown labels include provider, model, and input/output cost per 1M tokens so users see cost before committing.
- Model preferences persist per user (like `search_preferences` and `filter_preferences`).
- Global `.env` defaults remain the fallback when a user has no preference.
- All existing LLM operations respect the user's per-operation choice at runtime.
- **Success Metrics**:
  - All 4 active LLM call sites read user-scoped model preferences at runtime.
  - `/api/llm/models` returns a correctly filtered catalog per operation.
  - Zero regressions: default behavior (no user preference set) matches pre-change behavior.

## User Stories

1. As a **cost-conscious user**, I want to pick `DeepSeek deepseek-chat ($0.28 / $0.42 per 1M)` for job filtering and `OpenAI GPT-4o ($2.50 / $10.00 per 1M)` for CV writing, so that I minimize spend on bulk operations without sacrificing quality where it matters.
2. As a **quality-focused user**, I want to see which models support strict JSON schemas, so that I don't accidentally pick a model that will produce malformed CV output.
3. As a **new user**, I want sensible defaults already selected so I never see an empty dropdown and can immediately use the app without configuring each operation.
4. As an **API/CLI power user**, I want to continue overriding `llm_provider`/`llm_model` at job submission time, so that my existing scripts keep working.

## Functional Requirements

### Core Capabilities

- **FR-1**: Settings page exposes three model-picker dropdowns:
  - **CV Generation** — used for Job Summary Extraction + Full CV Composition (share one model).
  - **Job Filtering** — used for the LLM scoring of each LinkedIn job posting.
  - **Filter Prompt Generation** — used when a user clicks "Generate Prompt" in the Filter Preferences section.
- **FR-2**: Each dropdown option is rendered as `PROVIDER MODEL ($INPUT / $OUTPUT per 1M)` — e.g., `Anthropic Claude Sonnet 4.6 ($3.00 / $15.00 per 1M)`.
- **FR-3**: Each dropdown is **never empty**. When the user has no preference stored, the dropdown pre-selects the matching global default resolved from `settings.primary_llm_provider` + `{provider}_model`.
- **FR-4**: Dropdowns filter models by operation capability:
  - **CV Generation** and **Job Filtering** use `generate_json()` with strict schemas — only show models flagged `supports_strict_schema=True` OR `supports_json_object=True`.
  - **Filter Prompt Generation** uses `generate()` (plain text) — show all models.
- **FR-5**: A new backend endpoint `GET /api/llm/models` returns the catalog, optionally filtered by `?operation=<cv_generation|job_filtering|filter_prompt_generation>`.
- **FR-6**: User's model selections persist via `PUT /api/users/me` under a new `model_preferences` field.
- **FR-7**: Runtime: every LLM call site reads the user's `model_preferences`, falls back to global `.env` defaults if unset, and never prompts the user mid-workflow.
- **FR-8**: If a user's stored model references a provider whose API key is not configured, the call fails at runtime with a clear error. (UI does **not** proactively disable those options.)

### User Flows

**Flow 1 — First-time Settings view**
1. User navigates to `/settings`.
2. UI calls `GET /api/llm/models?operation=cv_generation`, `?operation=job_filtering`, `?operation=filter_prompt_generation` (or a single bulk call returning all 3 lists).
3. User's current `model_preferences` is loaded via `/api/auth/me`.
4. If a preference for an operation exists, that option is selected; otherwise the global-default option (matching `.env`) is pre-selected.
5. User sees three filled dropdowns.

**Flow 2 — Changing a model**
1. User opens the "CV Generation" dropdown, selects `Anthropic Claude Sonnet 4.6 ($3.00 / $15.00 per 1M)`.
2. User clicks "Save Model Preferences" (new section CTA).
3. Frontend calls `PUT /api/users/me` with `model_preferences: { cv_generation: { provider: "anthropic", model: "claude-sonnet-4.6" }, ... }`.
4. Backend updates `UserTable.model_preferences_json`, returns updated `User`.
5. Success indicator shown.

**Flow 3 — Job submission picks up user choice**
1. Preparation workflow starts for user `u123`.
2. Before invoking `create_llm_client()`, node resolves model from the user's `model_preferences.cv_generation` (falls back to global default).
3. LLM call uses the resolved provider + model.

**Flow 4 — Job filter uses user choice**
1. `filter_job_node` reads `config["configurable"]["user_model_preferences"]`.
2. Passes `llm_provider`/`llm_model` from `model_preferences.job_filtering` into `create_llm_client()`.
3. Same pattern for the `/api/users/me/filter-preferences/generate-prompt` endpoint, which resolves from `model_preferences.filter_prompt_generation`.

### Data Model

**New Pydantic model** (`src/models/user.py`):

```python
class ModelChoice(BaseModel):
    provider: str  # "openai" | "deepseek" | "grok" | "anthropic"
    model: str     # e.g. "gpt-4o", "claude-sonnet-4.6"

class UserModelPreferences(BaseModel):
    cv_generation: ModelChoice | None = None
    job_filtering: ModelChoice | None = None
    filter_prompt_generation: ModelChoice | None = None
```

**User model additions**:

```python
class User(BaseModel):
    # ...existing fields...
    model_preferences: UserModelPreferences | None = None
```

**UserUpdateRequest additions**:

```python
class UserUpdateRequest(BaseModel):
    # ...existing fields...
    model_preferences: UserModelPreferences | None = None
```

**New model catalog module** (`src/llm/model_catalog.py`):

```python
class ModelCatalogEntry(BaseModel):
    provider: LLMProvider
    model: str                         # API identifier
    display_name: str                  # human-readable name
    input_cost_per_1m: float           # USD
    output_cost_per_1m: float          # USD
    supports_strict_schema: bool       # strict JSON schema enforcement
    supports_json_object: bool         # json_object mode (weaker fallback)
    supports_plain_text: bool = True

MODEL_CATALOG: list[ModelCatalogEntry] = [
    # OpenAI
    ModelCatalogEntry(provider=OPENAI, model="gpt-5.2",       display_name="GPT-5.2",       input_cost_per_1m=1.75,  output_cost_per_1m=14.00, supports_strict_schema=True,  supports_json_object=True),
    ModelCatalogEntry(provider=OPENAI, model="gpt-5-mini",    display_name="GPT-5 mini",    input_cost_per_1m=0.25,  output_cost_per_1m=2.00,  supports_strict_schema=True,  supports_json_object=True),
    ModelCatalogEntry(provider=OPENAI, model="gpt-5-nano",    display_name="GPT-5 nano",    input_cost_per_1m=0.05,  output_cost_per_1m=0.40,  supports_strict_schema=True,  supports_json_object=True),
    ModelCatalogEntry(provider=OPENAI, model="gpt-4o",        display_name="GPT-4o",        input_cost_per_1m=2.50,  output_cost_per_1m=10.00, supports_strict_schema=True,  supports_json_object=True),
    ModelCatalogEntry(provider=OPENAI, model="gpt-4o-mini",   display_name="GPT-4o mini",   input_cost_per_1m=0.15,  output_cost_per_1m=0.60,  supports_strict_schema=True,  supports_json_object=True),
    # Anthropic
    ModelCatalogEntry(provider=ANTHROPIC, model="claude-opus-4.6",   display_name="Claude Opus 4.6",   input_cost_per_1m=5.00, output_cost_per_1m=25.00, supports_strict_schema=True, supports_json_object=True),
    ModelCatalogEntry(provider=ANTHROPIC, model="claude-sonnet-4.6", display_name="Claude Sonnet 4.6", input_cost_per_1m=3.00, output_cost_per_1m=15.00, supports_strict_schema=True, supports_json_object=True),
    ModelCatalogEntry(provider=ANTHROPIC, model="claude-haiku-4.5",  display_name="Claude Haiku 4.5",  input_cost_per_1m=1.00, output_cost_per_1m=5.00,  supports_strict_schema=True, supports_json_object=True),
    # DeepSeek
    ModelCatalogEntry(provider=DEEPSEEK, model="deepseek-chat",     display_name="DeepSeek Chat",     input_cost_per_1m=0.28, output_cost_per_1m=0.42, supports_strict_schema=False, supports_json_object=True),
    ModelCatalogEntry(provider=DEEPSEEK, model="deepseek-reasoner", display_name="DeepSeek Reasoner", input_cost_per_1m=0.28, output_cost_per_1m=0.42, supports_strict_schema=False, supports_json_object=True),
    # xAI Grok
    ModelCatalogEntry(provider=GROK, model="grok-4",      display_name="Grok 4",       input_cost_per_1m=3.00, output_cost_per_1m=15.00, supports_strict_schema=True, supports_json_object=True),
    ModelCatalogEntry(provider=GROK, model="grok-4-fast", display_name="Grok 4.1 Fast", input_cost_per_1m=0.20, output_cost_per_1m=0.50,  supports_strict_schema=True, supports_json_object=True),
    ModelCatalogEntry(provider=GROK, model="grok-3",      display_name="Grok 3",       input_cost_per_1m=3.00, output_cost_per_1m=15.00, supports_strict_schema=True, supports_json_object=True),
]
```

> **Pricing disclaimer**: Prices reflect public pricing as of 2026-04-18 (see References). This is a hardcoded constant and must be manually updated when provider pricing changes.

**Piccolo ORM schema addition** (`src/services/db/tables.py`):

```python
class UserTable(Table):
    # ...existing columns...
    model_preferences_json = JSON(null=True, default=None)
```

A new Piccolo migration adds the `model_preferences_json` column. Existing user rows get `NULL`, which `UserRepository` maps to `model_preferences=None`.

### Integration Points

- **`src/agents/_shared.py`**: `create_llm_client(llm_provider, llm_model)` is already override-aware. No change to signature.
- **`src/agents/preparation_workflow.py`**:
  - `filter_job_node` reads `model_preferences.job_filtering` from `config["configurable"]["user_model_preferences"]` and passes to `create_llm_client()`.
  - CV composition already accepts `llm_provider`/`llm_model` from workflow state via `compose_cv()` — the state must now be populated from the user's `model_preferences.cv_generation`.
- **`src/services/jobs/job_orchestrator.py`**: When submitting a job, read the user's `model_preferences.cv_generation` and use it as the fallback if `JobSubmitRequest.llm_provider`/`llm_model` are not provided.
- **`src/api/main.py`**:
  - New `GET /api/llm/models` endpoint.
  - `generate_filter_prompt` endpoint (line ~421) reads the authenticated user's `model_preferences.filter_prompt_generation` and passes it to `create_llm_client()`.
- **`src/services/auth/user_repository.py`**: CRUD methods serialize/deserialize the new JSON column.
- **Frontend** (`ui/src/lib/components/settings/`): Add a new component `ModelPreferencesSection.svelte`.

## Technical Design

### Architecture

Follows the project's established patterns:
- **Repository pattern**: `UserRepository.update_model_preferences()` mirrors existing `update_search_preferences()`/`update_filter_preferences()`.
- **Domain service boundary**: LLM selection logic stays inside `create_llm_client()` — the domain services (`JobOrchestrator`, `HITLProcessor`) only thread the user's preference into workflow state.
- **Workflow state threading**: Model prefs flow through `config["configurable"]["user_model_preferences"]`, matching how `repository` and `user_repository` are already passed in.
- **Catalog as pure data**: `src/llm/model_catalog.py` is a stateless module (Pydantic list). Helper functions `get_catalog_for_operation()` and `get_default_choice(provider)` live next to it.
- **Thin API adapter**: `GET /api/llm/models` is a ~10-line handler that reads the catalog, filters by operation, returns the list. Not authenticated (catalog is public info).

### Technology Stack

- **Backend**: Existing stack — FastAPI, Pydantic v2, Piccolo ORM.
- **Frontend**: Existing stack — Svelte 5, TypeScript.
- **No new dependencies.**

### Data Persistence

User model preferences are stored as a JSON column (`model_preferences_json`) on `UserTable`, matching the existing pattern for `search_preferences_json` and `filter_preferences_json`. The catalog itself is in-code (Python constant), never persisted.

### API / Interface Design

**New endpoint — `GET /api/llm/models`**

```
Query params:
  operation (optional): "cv_generation" | "job_filtering" | "filter_prompt_generation"

Response 200:
{
  "models": [
    {
      "provider": "openai",
      "model": "gpt-4o",
      "display_name": "GPT-4o",
      "label": "OpenAI GPT-4o ($2.50 / $10.00 per 1M)",
      "input_cost_per_1m": 2.50,
      "output_cost_per_1m": 10.00,
      "supports_strict_schema": true
    },
    ...
  ]
}
```

Behavior:
- No operation → returns full catalog.
- `operation=cv_generation` or `operation=job_filtering` → returns only entries where `supports_strict_schema=True OR supports_json_object=True` (all current entries qualify, but future additions of text-only models get filtered).
- `operation=filter_prompt_generation` → returns all entries.
- Models whose provider API key is missing are **still returned** (per FR-8 / user choice: "Allow selection, fail at runtime").

**Updated endpoint — `PUT /api/users/me`**

Accepts new optional `model_preferences` field matching `UserModelPreferences`. Existing fields unchanged.

**Existing API — `POST /api/jobs/submit`**

Retains optional `llm_provider` and `llm_model` fields (per user decision: "Keep optional override in API" for power users). UI does not expose these; settings are the single source of truth for the UI.

**Priority resolution for a job submission**:
1. `JobSubmitRequest.llm_provider`/`llm_model` (if provided by API caller)
2. User's `model_preferences.cv_generation`
3. Global `.env` default

### Frontend Component

**New component**: `ui/src/lib/components/settings/ModelPreferencesSection.svelte`

```
┌────────────────────────────────────────────────────┐
│  LLM Model Preferences                              │
│                                                     │
│  Pick which model to use for each operation.        │
│  Costs shown are per 1 million tokens.              │
│                                                     │
│  CV Generation                                      │
│  ┌───────────────────────────────────────────┐     │
│  │ OpenAI GPT-4o ($2.50 / $10.00 per 1M)  ▼ │     │
│  └───────────────────────────────────────────┘     │
│  Used for tailoring your CV to each job posting.   │
│                                                     │
│  Job Filtering                                      │
│  ┌───────────────────────────────────────────┐     │
│  │ DeepSeek Chat ($0.28 / $0.42 per 1M)   ▼ │     │
│  └───────────────────────────────────────────┘     │
│  Used for scoring each LinkedIn job posting.       │
│                                                     │
│  Filter Prompt Generation                           │
│  ┌───────────────────────────────────────────┐     │
│  │ Claude Haiku 4.5 ($1.00 / $5.00 per 1M) ▼│     │
│  └───────────────────────────────────────────┘     │
│  Used when you click "Generate Prompt".            │
│                                                     │
│  [ Save Model Preferences ]                         │
└────────────────────────────────────────────────────┘
```

- Component is inserted into `ui/src/routes/settings/+page.svelte` above `StartSearchSection`.
- Consumes `auth.user.model_preferences` for current values; resolves the global default from the catalog if a slot is `null`.
- Catalog fetched in `onMount` via three parallel `GET /api/llm/models?operation=...` calls (or a single bulk call — implementer's choice).
- On save, calls `PUT /api/users/me` and updates `auth.setUser(updated)`.

## Non-Functional Requirements

- **Performance**: Catalog fetch is a static in-memory list — sub-millisecond server time. No caching needed.
- **Security**: The `/api/llm/models` endpoint is public (does not expose keys or user-identifying data). `/api/users/me` remains auth-protected via `get_current_user`. No new secrets introduced.
- **Observability**: Existing `create_llm_client()` already logs `Using LLM provider: X, model: Y` — this will now surface the user's choice, useful for cost debugging.
- **Error Handling**:
  - Missing API key at runtime → `create_llm_client()` raises `ValueError("API key not configured for provider: X")`. Existing behavior.
  - Invalid `ModelChoice` (provider not in `LLMProvider` enum) → Pydantic validation rejects at `PUT /api/users/me`. Returns 422.
  - Catalog entry removed but user still has a stored preference for it → `create_llm_client()` accepts any model string, so the LLM client will simply try that model ID. If the provider rejects it, the error surfaces at the call site.
  - Frontend handles `GET /api/llm/models` failure by falling back to the three global-default choices and showing a "Could not load model catalog — using defaults" banner.

## Implementation Considerations

### Design Trade-offs

- **Hardcoded catalog vs. runtime discovery**: Chose hardcoded. Provider model-list APIs don't return pricing, so we'd have to hardcode pricing anyway. Hardcoding the whole list keeps it simple and explicit. Cost: manual maintenance when provider pricing changes (add a "Pricing Snapshot" date in the module docstring).
- **Allow selection of unconfigured providers**: Chose "allow, fail at runtime" (per user choice). Simpler UI implementation; error surfaces clearly at job submission time. Downside: user might waste a round-trip discovering their key is missing. Acceptable for a self-hosted personal tool.
- **Share CV picker across job summary + full composition**: Chose shared. Current code calls both `generate_json()` calls inside the same `CVComposer` instance with one client. Splitting would require refactoring `compose_cv()` to manage two clients and double the UI complexity — not worth it unless a concrete need appears.
- **Keep submission-level override in `JobSubmitRequest`**: Chose yes (per user decision). Lets API/CLI callers override per-request without touching their saved settings. UI doesn't surface this, so the settings remain the UX source of truth.
- **Three separate pickers vs. one bulk picker**: Chose three. Different operations have different cost/quality trade-offs; a single picker defeats the purpose of this feature.

### Dependencies

None. This change is self-contained within the existing stack.

### Testing Strategy

**Unit tests** (per user choice: unit tests for catalog + settings only):

1. `tests/unit/llm/test_model_catalog.py` (new)
   - `get_catalog_for_operation("cv_generation")` excludes non-schema models (none today, but protects the contract).
   - `get_catalog_for_operation("filter_prompt_generation")` returns everything.
   - Each entry has positive prices, non-empty `display_name`, `label` string is well-formed.

2. `tests/unit/services/test_user_repository.py` (extend)
   - Create user → `model_preferences` is `None`.
   - `update_model_preferences()` persists all three slots, round-trips through JSON serialization.
   - Partial update (e.g., only `cv_generation`) preserves other slots.

3. `tests/unit/api/test_llm_models_endpoint.py` (new)
   - `GET /api/llm/models` returns the full catalog.
   - `GET /api/llm/models?operation=cv_generation` returns filtered list.
   - `GET /api/llm/models?operation=unknown` returns 422.

4. `tests/unit/api/test_users_me_endpoint.py` (extend)
   - `PUT /api/users/me` with valid `model_preferences` persists and returns updated user.
   - Invalid provider name → 422.

5. `tests/unit/agents/test_preparation_workflow.py` (extend)
   - `filter_job_node` uses user's `model_preferences.job_filtering` when provided.
   - Falls back to global settings when user pref is `None`.

**No Playwright E2E** per user decision — dropdown behavior is simple enough that unit + API contract coverage is sufficient.

**Manual verification steps**:
- Start API + UI (`.\scripts\dev.ps1`).
- Log in as a user with no `model_preferences` set — confirm three dropdowns pre-select global defaults from `.env`.
- Change each dropdown, save, reload page — confirm persistence.
- Submit a job — confirm logs show the user's chosen model, not the global default.

## Out of Scope

- **Per-user API keys**: Users continue to share the server's `.env` keys. Adding user-level key storage is a separate, larger feature (secret encryption, key rotation).
- **Cost tracking / billing**: This feature shows pricing but does not track actual per-user spend. A future feature could sum token usage per user.
- **Model availability discovery via provider API**: No auto-discovery; catalog is static.
- **Capability badges in UI**: Per user choice (recommended option), we filter unsuitable models out rather than showing badges.
- **Tier labels (Budget/Standard/Premium)**: Not shown; explicit `$` pricing is clearer.
- **Wiring `URLJobExtractor`**: That operation is still a stub (`NotImplementedError`). When implemented, it should follow the same pattern and use a new `url_extraction` slot — deferred until the extractor exists.
- **Temperature/max-tokens per operation**: Only model choice is exposed. Temperature stays hardcoded per operation for now.

## Open Questions

1. **Pricing staleness policy**: How often should we audit the hardcoded catalog? Proposed: annotate the module with a `PRICING_SNAPSHOT_DATE` constant and surface a comment telling maintainers to refresh quarterly.
2. **Reasoning models (o1/o3/gpt-5.2 Pro)**: Should we include them given their temperature restriction and higher latency? Proposed: exclude gpt-5.2 Pro from the initial catalog (very expensive, niche) and include the smaller GPT-5/GPT-4o family. Add later if requested.
3. **Migration for existing users**: The Piccolo migration adds a nullable column — existing rows get `NULL`, which `UserRepository` maps to `None`, which triggers the global-default fallback. No data migration needed. Should confirm the Piccolo migration is applied in production deploy docs.

## References

- **Existing LLM infra**:
  - `src/llm/provider.py` — `LLMProvider` enum, `BaseLLMClient`, `LLMClientFactory`
  - `src/agents/_shared.py:67` — `create_llm_client(llm_provider, llm_model)` — entry point to change behavior
  - `src/config/settings.py:36-56` — current global provider/model settings
- **LLM call sites to wire**:
  - `src/services/cv/cv_composer.py:187` — `generate_json` (Job Summary Extraction)
  - `src/services/cv/cv_composer.py:238` — `generate_json` (Full CV Composition)
  - `src/services/jobs/job_filter.py:82` — `generate_json` (Job Filtering)
  - `src/services/jobs/job_filter.py:129` — `generate` (Filter Prompt Generation)
  - `src/api/main.py:421` — filter-prompt endpoint (reads user, must thread model choice)
  - `src/agents/preparation_workflow.py:280` — filter node (must read `user_model_preferences` from config)
- **Existing per-user settings patterns to mirror**:
  - `src/models/user.py` — `User`, `UserSearchPreferences`, `UserUpdateRequest`
  - `src/services/auth/user_repository.py` — serialization pattern for JSON columns
  - `ui/src/lib/components/settings/FilterPreferencesSection.svelte` — UI pattern for save + `auth.setUser`
- **Pricing sources (snapshot 2026-04-18)**:
  - [OpenAI API Pricing](https://openai.com/api/pricing/)
  - [Anthropic Claude API Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
  - [DeepSeek API Pricing](https://api-docs.deepseek.com/quick_start/pricing)
  - [xAI Grok Models & Pricing](https://docs.x.ai/developers/models)
