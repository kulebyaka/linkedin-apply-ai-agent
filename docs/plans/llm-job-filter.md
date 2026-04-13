# LLM-Based Job Filtering for Hidden Disqualifiers

## Overview

Implement an LLM-powered job filter that detects hidden disqualifiers (fake remote, buried hard requirements, misleading titles), scores overall job suitability (0-100), and routes jobs based on two configurable thresholds. Users configure filter preferences and view/edit the generated LLM prompt via the existing Settings page. Filter results (score + red flags) display on HITL review cards.

## Context

- Files involved:
  - `src/services/job_filter.py` (existing skeleton — full rewrite)
  - `src/models/job_filter.py` (to be created — FilterResult, FilterPreferences models)
  - `src/models/state_machine.py` (existing — add FILTERED_OUT state)
  - `src/models/unified.py` (existing — add filter_result to JobRecord, PendingApproval)
  - `src/models/user.py` (existing — add UserFilterPreferences to User model)
  - `src/services/tables.py` (existing — add filter_preferences column to UserTable)
  - `src/services/user_repository.py` (existing — add filter preferences CRUD)
  - `src/agents/preparation_workflow.py` (existing — wire filter node)
  - `src/agents/_shared.py` (existing — add filter utility if needed)
  - `src/api/main.py` (existing — add filter preferences endpoints)
  - `src/config/settings.py` (existing — add default filter thresholds)
  - `prompts/job_filter/` (to be created — filter prompt templates)
  - `ui/src/lib/components/settings/FilterPreferencesSection.svelte` (to be created)
  - `ui/src/lib/api/settings.ts` (existing — add filter preferences API calls)
  - `ui/src/routes/settings/+page.svelte` (existing — add FilterPreferencesSection)
  - `ui/src/lib/types/index.ts` (existing — add FilterResult type)
  - `ui/src/lib/components/review/JobCard.svelte` (existing — add filter badge/red flags)
  - `tests/unit/test_job_filter.py` (to be created)
- Related patterns:
  - `CVComposer` / `CVValidator` pattern for LLM service + validation
  - `CVPromptManager` for external prompt file loading
  - `UserSearchPreferences` on `UserTable` for per-user JSON settings stored in SQLite
  - `BaseLLMClient.generate_json()` for structured LLM output with schema enforcement
  - `BusinessState` enum + `ALLOWED_TRANSITIONS` for state machine
  - Settings page sections pattern: `ProfileSection`, `CVUploadSection`, `SearchPreferencesSection`
- Dependencies: No new external dependencies

## Development Approach

- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- Follow existing async patterns (`async def` nodes, `asyncio.to_thread` for sync LLM calls)
- Per-user data: filter preferences stored on `UserTable` (like `search_preferences`)
- All job data is user-scoped via `user_id`
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**

## Design Decisions

### Two-Threshold Routing
- **Hard reject threshold** (default: 30): Jobs scoring below this OR with hard disqualifiers → saved as `filtered_out` with short LLM summary, skip CV generation pipeline
- **Warning threshold** (default: 70): Jobs scoring between reject and warning thresholds → continue pipeline, show warning badge + red flags in HITL review
- Jobs scoring above warning threshold → clean pass, no warnings
- Both thresholds configurable per-user on the Settings page

### Filter Result Storage
- New `FilterResult` Pydantic model: `score: int`, `red_flags: list[str]`, `disqualified: bool`, `disqualifier_reason: str | None`, `reasoning: str`
- Stored as `filter_result: dict | None` on `JobRecord`
- Exposed in `PendingApproval` for HITL review cards

### Settings UI: Two-Textarea Prompt Editor
1. **Textarea 1** ("Your Preferences"): Natural language description of what the user doesn't want. Placeholder with sample text.
2. **"Generate Prompt" button**: Calls `POST /api/users/me/filter-preferences/generate-prompt` — LLM converts natural language into a structured filter prompt.
3. **Textarea 2** ("Filter Prompt"): Shows the generated prompt. User can freely edit. Saved as the actual prompt sent to the LLM during filtering.
4. Thresholds: Two number inputs for reject/warning thresholds.

### State Machine
- New terminal state: `BusinessState.FILTERED_OUT = "filtered_out"`
- Transitions: `QUEUED → FILTERED_OUT`, `PROCESSING → FILTERED_OUT`
- `FILTERED_OUT` is terminal (no outgoing transitions)

## Implementation Steps

### Task 1: Data Models and State Machine

**Files:**
- Create: `src/models/job_filter.py`
- Modify: `src/models/state_machine.py`
- Modify: `src/models/unified.py`
- Modify: `src/models/user.py`

- [x] Create `src/models/job_filter.py` with:
  - `FilterResult(BaseModel)`: `score: int` (0-100), `red_flags: list[str]`, `disqualified: bool`, `disqualifier_reason: str | None`, `reasoning: str`
  - `UserFilterPreferences(BaseModel)`: `natural_language_prefs: str` (textarea 1 content), `custom_prompt: str | None` (textarea 2 content, null means use default), `reject_threshold: int = 30`, `warning_threshold: int = 70`, `enabled: bool = True`
- [x] Add `BusinessState.FILTERED_OUT = "filtered_out"` to `src/models/state_machine.py`
- [x] Add `FILTERED_OUT` to `ALLOWED_TRANSITIONS`: reachable from `QUEUED` and `PROCESSING`, terminal (empty outgoing set)
- [x] Add `filter_result: dict | None = None` field to `JobRecord` in `src/models/unified.py`
- [x] Add `filter_result: dict | None = None` field to `PendingApproval` in `src/models/unified.py`
- [x] Add `filter_preferences: UserFilterPreferences | None = None` field to `User` model in `src/models/user.py`
- [x] Add `filter_preferences: UserFilterPreferences | None = None` to `UserUpdateRequest` in `src/models/user.py`
- [x] Write unit tests for `FilterResult` and `UserFilterPreferences` validation, and `FILTERED_OUT` state transitions
- [x] Run project test suite - must pass before task 2

### Task 2: Database Schema and Repository Updates

**Files:**
- Modify: `src/services/tables.py`
- Modify: `src/services/user_repository.py`
- Modify: `src/services/job_repository.py`

- [x] Add `filter_preferences = JSON(null=True)` column to `UserTable` in `src/services/tables.py`
- [x] Add `filter_result = JSON(null=True)` column to `Job` table in `src/services/tables.py`
- [x] Update `UserRepository.update()` to handle `filter_preferences` field (serialize `UserFilterPreferences` to dict, same pattern as `search_preferences`)
- [x] Update `UserRepository._row_to_user()` to parse `filter_preferences` JSON into `UserFilterPreferences` model
- [x] Update `SQLiteJobRepository` to read/write `filter_result` field on `JobRecord` (same pattern as existing JSON fields)
- [x] Update `InMemoryJobRepository` to store/return `filter_result` field
- [x] Write unit tests for filter preferences CRUD and filter_result persistence in both repository implementations
- [x] Run project test suite - must pass before task 3

### Task 3: Job Filter Service Implementation

**Files:**
- Rewrite: `src/services/job_filter.py`
- Create: `prompts/job_filter/default_filter_prompt.txt`
- Create: `prompts/job_filter/generate_prompt_from_prefs.txt`

- [x] Create `prompts/job_filter/default_filter_prompt.txt` — the default filter prompt template with placeholders for job title, company, location, description, and user preferences context. Should instruct the LLM to check for: fake remote, hidden hard requirements (clearance, visa, degree, relocation), misleading titles, experience inflation, and score overall suitability 0-100
- [x] Create `prompts/job_filter/generate_prompt_from_prefs.txt` — meta-prompt that takes the user's natural language preferences (textarea 1) and generates a structured filter prompt (textarea 2)
- [x] Rewrite `src/services/job_filter.py`:
  - `JobFilter.__init__(self, llm_client: BaseLLMClient, prompts_dir: str | None = None)` — mirrors `CVComposer` pattern
  - `FILTER_RESULT_SCHEMA` — JSON schema derived from `FilterResult.model_json_schema()`
  - `async def evaluate_job(self, job_posting: dict, user_filter_prefs: UserFilterPreferences | None = None) -> FilterResult` — main entry point. Uses custom prompt from `user_filter_prefs.custom_prompt` if set, otherwise default template. Calls `llm.generate_json()` with `FILTER_RESULT_SCHEMA`. Returns validated `FilterResult`.
  - `async def generate_prompt_from_preferences(self, natural_language_prefs: str) -> str` — calls LLM with meta-prompt to generate a filter prompt from user's natural language input. Returns the generated prompt string.
  - `def should_reject(self, result: FilterResult, reject_threshold: int = 30) -> bool` — returns True if `result.disqualified` or `result.score < reject_threshold`
  - `def should_warn(self, result: FilterResult, warning_threshold: int = 70) -> bool` — returns True if `result.score < warning_threshold` and not rejected
- [x] Make `evaluate_job` use `asyncio.to_thread()` for the sync `llm.generate_json()` call (same pattern as `CVComposer`)
- [x] Write unit tests for `JobFilter` with mocked LLM client: test evaluate_job, should_reject, should_warn, generate_prompt_from_preferences, custom prompt usage, default prompt fallback
- [x] Run project test suite - must pass before task 4

### Task 4: Wire Filter into Preparation Workflow

**Files:**
- Modify: `src/agents/preparation_workflow.py`
- Modify: `src/agents/_shared.py`
- Modify: `src/config/settings.py`

- [ ] Add default filter threshold settings to `src/config/settings.py`: `job_filter_reject_threshold: int = 30`, `job_filter_warning_threshold: int = 70`, `job_filter_enabled: bool = True`
- [ ] Add `filter_result: dict | None` field to `PreparationWorkflowState` TypedDict
- [ ] Implement `filter_job_node` in `preparation_workflow.py`:
  - Load user's filter preferences from repository (need user_id from state/config)
  - If filtering disabled (globally or per-user `enabled=False`), pass through
  - Create `JobFilter` instance using `create_llm_client()` from `_shared.py`
  - Call `await job_filter.evaluate_job(job_posting, user_filter_prefs)`
  - Store `FilterResult` in state as `filter_result`
  - If `should_reject()`: set `state["current_step"] = BusinessState.FILTERED_OUT`, save to DB with filter reason, route to END
  - If `should_warn()`: continue pipeline (filter_result will be saved with job record later)
  - Otherwise: clean pass, continue pipeline
- [ ] Update `route_after_extract` to support a new routing option: after `filter_job`, check if filtered out → route to `save_filtered_out` or END
- [ ] Add conditional routing after `filter_job`: if `filter_result` indicates rejection → route to a `save_filtered_out_node` → END; otherwise → `compose_cv`
- [ ] Implement `save_filtered_out_node`: creates a minimal `JobRecord` with `status=FILTERED_OUT`, `filter_result`, `job_posting`, no CV data. Saves to repository.
- [ ] Update `save_to_db_node` to include `filter_result` from state in the `JobRecord`
- [ ] Update `PendingApproval` construction in `HITLProcessor` (or wherever pending approvals are built) to include `filter_result` from `JobRecord`
- [ ] Write unit tests: test filter_job_node with mocked LLM returning reject/warn/pass results, test routing logic, test save_filtered_out_node
- [ ] Run project test suite - must pass before task 5

### Task 5: API Endpoints for Filter Preferences

**Files:**
- Modify: `src/api/main.py`

- [ ] Add `GET /api/users/me/filter-preferences` endpoint: returns user's `UserFilterPreferences` (or default values if not set)
- [ ] Add `PUT /api/users/me/filter-preferences` endpoint: accepts `UserFilterPreferences`, saves to user via `UserRepository.update()`
- [ ] Add `POST /api/users/me/filter-preferences/generate-prompt` endpoint: accepts `{"natural_language_prefs": "..."}`, creates `JobFilter` with primary LLM, calls `generate_prompt_from_preferences()`, returns `{"prompt": "..."}`
- [ ] All endpoints require authentication (`CurrentUser` dependency)
- [ ] Write API tests using `TestClient` for all three endpoints
- [ ] Run project test suite - must pass before task 6

### Task 6: Settings UI — Filter Preferences Section

**Files:**
- Create: `ui/src/lib/components/settings/FilterPreferencesSection.svelte`
- Modify: `ui/src/lib/api/settings.ts`
- Modify: `ui/src/routes/settings/+page.svelte`

- [ ] Add API functions to `ui/src/lib/api/settings.ts`:
  - `getFilterPreferences(): Promise<UserFilterPreferences>`
  - `updateFilterPreferences(prefs: UserFilterPreferences): Promise<User>`
  - `generateFilterPrompt(naturalLanguagePrefs: string): Promise<{prompt: string}>`
- [ ] Create `FilterPreferencesSection.svelte` following existing section component patterns (brutalist/neobrutalist style matching other sections):
  - **Enable/disable toggle** for filtering
  - **Textarea 1**: "Your Preferences" — placeholder text like "I don't want jobs that require security clearance, on-site presence, or less than 3 years experience. I'm looking for remote senior backend roles with Python/Go."
  - **"Generate Prompt" button**: calls `generateFilterPrompt()`, shows loading spinner during LLM call, populates textarea 2 with result
  - **Textarea 2**: "Filter Prompt" — shows generated or custom prompt, editable. Placeholder with a sample default prompt.
  - **Two number inputs**: "Reject threshold" (default 30) and "Warning threshold" (default 70) with labels explaining what they do
  - **Save button**: calls `updateFilterPreferences()` with all fields
  - Success/error toast feedback
- [ ] Add `FilterPreferencesSection` to `ui/src/routes/settings/+page.svelte` — load filter preferences in `onMount`, pass as prop
- [ ] Manual test: verify the full flow — type preferences, generate prompt, edit prompt, set thresholds, save, reload page and verify persistence
- [ ] Run project test suite - must pass before task 7

### Task 7: HITL Review Card — Filter Results Display

**Files:**
- Modify: `ui/src/lib/types/index.ts`
- Modify: `ui/src/lib/components/review/JobCard.svelte`

- [ ] Add `FilterResult` type to `ui/src/lib/types/index.ts`: `{ score: number; red_flags: string[]; disqualified: boolean; disqualifier_reason: string | null; reasoning: string }`
- [ ] Add `filter_result?: FilterResult` to `PendingApproval` type
- [ ] Update `JobCard.svelte` metadata footer to show filter results when present:
  - Color-coded score badge: green (>= warning threshold), yellow (between reject and warning), red (below reject or disqualified)
  - Red flags list: show as small tags/chips below the score badge
  - If no filter_result, don't show anything (backwards compatible with manual/URL jobs)
- [ ] Manual test: submit a LinkedIn-sourced job with filter enabled, verify score and red flags appear on HITL review card
- [ ] Run project test suite - must pass before task 8

### Task 8: Verify acceptance criteria

- [ ] End-to-end test: configure filter preferences via Settings UI → trigger LinkedIn search (or use fixture replay) → verify jobs are filtered → check filtered_out jobs are saved with reason → check warning jobs appear in HITL with badge → check clean-pass jobs appear without badge
- [ ] Verify filtered_out jobs do NOT appear in HITL pending queue
- [ ] Verify filter is skipped for manual/URL source jobs (only LinkedIn)
- [ ] Run full test suite: `pytest`
- [ ] Run linter: `uv run ruff check src/ tests/`
- [ ] Run svelte check: `cd ui && npm run check`

### Task 9: Update documentation

- [ ] Update `CLAUDE.md`:
  - Add `JobFilter` to Implementation Status table (mark as Complete)
  - Add filter-related settings to Configuration section
  - Add filter endpoints to API Endpoints table
  - Add `FilterResult` and `UserFilterPreferences` to Data Models section
  - Add `FILTERED_OUT` state to State Machine section
  - Update directory structure if new files added
- [ ] Update `README.md` if user-facing changes
- [ ] Move this plan to `docs/plans/completed/`
