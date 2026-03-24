# Backend Architecture Refactoring

## Overview

Systematic refactoring of the backend to address architectural issues identified by a three-angle code review (business analysis, technical architecture, devil's advocate). Each task corresponds to one finding. The refactoring introduces dependency injection, async-native workflows, a proper domain service layer, a job lifecycle state machine, and eliminates scattered business logic from API handlers.

## Context

- Files involved:
  - `src/api/main.py` (existing — 1212-line FastAPI app with 9+ globals and business logic in handlers)
  - `src/agents/preparation_workflow.py` (existing — preparation workflow with sync nodes, global `_repository`)
  - `src/agents/retry_workflow.py` (existing — retry workflow with sync nodes, duplicated code)
  - `src/agents/application_workflow.py` (existing — application workflow, all stubs)
  - `src/services/job_repository.py` (existing — 941-line repo with abstract base + InMemory + SQLite)
  - `src/services/job_queue.py` (existing — async queue with global singleton)
  - `src/services/cv_composer.py` (existing — 452-line god class)
  - `src/services/job_source.py` (existing — job source adapters)
  - `src/services/scheduler.py` (existing — APScheduler wrapper)
  - `src/services/browser_automation.py` (existing — Playwright stealth browser)
  - `src/models/unified.py` (existing — JobRecord god object, flat status enum)
  - `src/models/cv.py` (existing — duplicate CV / CVLLMOutput models)
  - `src/models/job.py` (existing — ScrapedJob, JobPosting)
  - `src/models/mvp.py` (existing — legacy MVP models)
  - `src/config/settings.py` (existing — Pydantic settings)
  - `src/context.py` (to be created — AppContext dataclass)
  - `src/models/state_machine.py` (to be created — lifecycle state machine)
  - `src/services/job_orchestrator.py` (to be created — JobOrchestrator domain service)
  - `src/services/hitl_processor.py` (to be created — HITLProcessor domain service)
  - `src/services/cv_validator.py` (to be created — CV validation extracted from composer)
  - `src/agents/_shared.py` (to be created — shared workflow utilities)
  - `tests/unit/test_state_machine.py` (to be created)
  - `tests/unit/test_job_orchestrator.py` (to be created)
  - `tests/unit/test_hitl_processor.py` (to be created)
  - `tests/unit/test_context.py` (to be created)
- Related patterns: Repository ABC (`JobRepository`), Factory pattern (`JobSourceFactory`, `LLMClientFactory`), Pydantic BaseModel for all models
- Dependencies: All existing — no new external packages needed

## Development Approach

- **Testing approach**: Code first, then tests
- Complete each task fully before moving to the next
- Tasks are ordered by dependency: foundational changes first (DI, models, state machine), then consumers (workflows, services, API)
- Use `uv` for package management (not pip)
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**
- **Validation after each task**: `uv run pytest tests/ -x -q && uv run mypy src/`

## Implementation Steps

### Task 1: DI container — Replace global state with AppContext

Addresses **Finding #1** (Global State Antipattern). Replace 9+ module-level globals in `api/main.py` and `_repository` singletons in all 3 workflows with a single `AppContext` dataclass.

**Files:**
- Create: `src/context.py`
- Modify: `src/api/main.py`
- Modify: `src/agents/preparation_workflow.py`
- Modify: `src/agents/retry_workflow.py`
- Modify: `src/agents/application_workflow.py`
- Modify: `src/services/job_queue.py`
- Create: `tests/unit/test_context.py`

- [x] Create `src/context.py` with a frozen `AppContext` dataclass holding: `repository: JobRepository`, `settings: Settings`, `prep_workflow: CompiledStateGraph`, `retry_workflow: CompiledStateGraph`, `job_queue: JobQueue | None`, `scheduler: LinkedInSearchScheduler | None`, `browser: LinkedInAutomation | None`. Add a `create_app_context()` factory function that builds and returns a fully initialized `AppContext`
- [x] Remove `_repository` global, `set_repository()`, and `get_repository()` from `preparation_workflow.py`. Instead, accept `repository` as a parameter in each node function via LangGraph's `config["configurable"]` dict. Update `create_preparation_workflow()` to not depend on module globals
- [x] Apply the same removal of `_repository` global, `set_repository()`, `get_repo()` from `retry_workflow.py` and `application_workflow.py`. Remove the circular import where `retry_workflow` imports `get_repository` from `preparation_workflow`
- [x] Remove `_job_queue` global, `get_job_queue()`, `set_job_queue()` from `job_queue.py`. The queue is now held by `AppContext`
- [x] Refactor `api/main.py`: remove all module-level globals (`preparation_workflow`, `retry_workflow`, `job_repository`, `workflow_threads`, `workflow_created_at`, `unified_threads`, `_linkedin_scheduler`, `_linkedin_browser`, `_queue_consumer_task`, `_linkedin_init_lock`, `_consumer_restart_count`, etc.). Replace with FastAPI lifespan that creates `AppContext` and stores it in `app.state.ctx`. Each endpoint retrieves context via `request.app.state.ctx`
- [x] Replace `workflow_threads` and `unified_threads` plain dicts with a single thread-safe tracking structure (e.g., use `asyncio.Lock` around a dict, or store tracking info in the repository instead)
- [x] Write unit tests in `tests/unit/test_context.py`: test `AppContext` creation, test that `create_app_context()` returns a properly wired context, test that all fields are accessible
- [x] Update existing tests that use `set_repository()` or mock module globals to use the new DI pattern
- [x] Run project test suite — must pass before task 2

### Task 2: Make workflow nodes async-native

Addresses **Finding #2** (asyncio.run() in sync nodes). Convert all workflow node functions to `async def` and use LangGraph's `ainvoke()`/`astream()` API. Remove all `asyncio.run()` and `asyncio.to_thread()` hacks.

**Files:**
- Modify: `src/agents/preparation_workflow.py`
- Modify: `src/agents/retry_workflow.py`
- Modify: `src/agents/application_workflow.py`
- Modify: `src/services/job_queue.py` (process_queue consumer)
- Modify: `src/api/main.py` (workflow invocation)

- [x] Convert all 5 node functions in `preparation_workflow.py` to `async def`: `extract_job_node`, `filter_job_node`, `compose_cv_node`, `generate_pdf_node`, `save_to_db_node`. Replace every `asyncio.run(repo.xxx())` with `await repo.xxx()`. Replace `asyncio.run(adapter.extract())` with `await adapter.extract()`
- [x] Convert all 4 node functions in `retry_workflow.py` to `async def`: `load_from_db_node`, `compose_cv_node`, `generate_pdf_node`, `update_db_node`. Replace all `asyncio.run()` calls with `await`
- [x] Convert all 5 node functions in `application_workflow.py` to `async def`. Replace all `asyncio.run()` calls with `await`
- [x] Update `process_queue()` in `job_queue.py`: remove `asyncio.to_thread(lambda: list(workflow.stream(...)))` wrapper. Use `await workflow.ainvoke(initial_state, config=config)` directly since the consumer is already async
- [x] Update `api/main.py`: replace `run_workflow_async()`, `run_preparation_workflow_async()`, `run_retry_workflow_async()` background task functions. They should now `await workflow.ainvoke()` instead of calling sync `workflow.invoke()` in a thread. Use `asyncio.create_task()` instead of `background_tasks.add_task()` for fire-and-forget workflow execution
- [x] Verify that no `asyncio.run()` calls remain anywhere in `src/agents/` or `src/services/job_queue.py` (grep to confirm)
- [x] Update affected unit tests to use `pytest-asyncio` for async node testing
- [x] Run project test suite — must pass before task 3

### Task 3: Add thread-safe state management

Addresses **Finding #3** (Race conditions on shared mutable state). Fix the InMemoryJobRepository TOCTOU race and protect shared tracking state.

**Files:**
- Modify: `src/services/job_repository.py` (InMemoryJobRepository)
- Modify: `src/api/main.py` (tracking state — if not already removed in Task 1)

- [x] Add an `asyncio.Lock` to `InMemoryJobRepository`. Wrap `create()`, `update()`, `delete()` in `async with self._lock:` to prevent TOCTOU race conditions on the `_jobs` dict
- [x] Ensure `SQLiteJobRepository.update()` uses a single atomic query (verify Piccolo ORM handles this — it should since it's a single UPDATE statement)
- [x] If any shared tracking dicts remain after Task 1 (e.g., for in-progress workflow tracking), protect them with `asyncio.Lock` or migrate tracking into the repository itself
- [x] Write tests: test concurrent `update()` calls on `InMemoryJobRepository` using `asyncio.gather()` to verify no lost updates
- [x] Run project test suite — must pass before task 4

### Task 4: Remove NotImplementedError swallowing

Addresses **Finding #4** (Silent stub failures). Replace `except NotImplementedError` catch-all blocks with explicit error propagation. Stubs should fail loudly, not fabricate data.

**Files:**
- Modify: `src/agents/preparation_workflow.py`
- Modify: `src/agents/retry_workflow.py`
- Modify: `src/agents/application_workflow.py`

- [ ] In `preparation_workflow.py:extract_job_node`: remove the `except NotImplementedError` block (lines ~193-222) that fabricates stub data with `title="Position"`. Instead, let `NotImplementedError` propagate. The node's outer try/except for general `Exception` at the end will catch it and set `error_message` + `current_step="failed"` properly
- [ ] In `preparation_workflow.py:save_to_db_node`: remove the `except NotImplementedError` block (lines ~483-488) that silently drops repo.create(). If the repository doesn't support `create()`, this is a fatal configuration error — let it raise
- [ ] Apply the same pattern to `retry_workflow.py`: remove `except NotImplementedError` in `load_from_db_node` (lines ~122-135) and `update_db_node` (lines ~349-353). These should fail with clear errors, not silently proceed with stale state
- [ ] Apply the same pattern to `application_workflow.py`: remove `except NotImplementedError` in `load_from_db_node` and `update_db_node`
- [ ] Add specific, descriptive error messages when `NotImplementedError` is the root cause (e.g., `"URL job extraction is not yet implemented. Use source='manual' instead."`)
- [ ] Update tests that relied on stub fallback behavior
- [ ] Run project test suite — must pass before task 5

### Task 5: Extract domain services from API handlers

Addresses **Finding #5** (115 lines of business logic in API). Create `JobOrchestrator` and `HITLProcessor` domain services. API handlers become thin adapters.

**Files:**
- Create: `src/services/job_orchestrator.py`
- Create: `src/services/hitl_processor.py`
- Modify: `src/api/main.py`
- Create: `tests/unit/test_job_orchestrator.py`
- Create: `tests/unit/test_hitl_processor.py`

- [ ] Create `src/services/job_orchestrator.py` with class `JobOrchestrator(repository, prep_workflow, settings)`. Move job submission logic from `api/main.py:submit_job` endpoint into `orchestrator.submit_job(request) -> JobSubmitResponse`. Move status query logic (including the dual-source-of-truth resolution) into `orchestrator.get_status(job_id) -> JobStatusResponse`. Move workflow dispatch into `orchestrator.trigger_workflow(job_id, state, config)`
- [ ] Create `src/services/hitl_processor.py` with class `HITLProcessor(repository, retry_workflow)`. Move the 115 lines from `api/main.py:submit_hitl_decision` into `processor.process_decision(job_id, decision) -> HITLDecisionResponse`. Move pending retrieval from `api/main.py:get_hitl_pending` into `processor.get_pending() -> list[PendingApproval]`. Move history retrieval into `processor.get_history() -> list[ApplicationHistoryItem]`
- [ ] Add both services to `AppContext` dataclass (from Task 1)
- [ ] Refactor API endpoints to be thin adapters: each endpoint extracts context, calls the appropriate service method, and returns the result. Target: no endpoint handler longer than ~20 lines
- [ ] Write unit tests for `JobOrchestrator` with mocked repository and workflow
- [ ] Write unit tests for `HITLProcessor` with mocked repository — test approve/decline/retry flows, test validation (retry without feedback raises error), test that declined jobs can't be re-decided
- [ ] Run project test suite — must pass before task 6

### Task 6: Split JobRecord and add CV composition history

Addresses **Finding #6** (God-object JobRecord). Split `JobRecord` into focused models. Add `CVCompositionAttempt` for retry history tracking.

**Files:**
- Modify: `src/models/unified.py`
- Create: `src/models/cv_attempt.py`
- Modify: `src/services/job_repository.py` (abstract interface + both implementations)
- Modify: `src/services/job_orchestrator.py`
- Modify: `src/services/hitl_processor.py`
- Modify: `src/agents/preparation_workflow.py`
- Modify: `src/agents/retry_workflow.py`

- [ ] Refactor `JobRecord` in `unified.py`: remove CV-specific fields (`cv_json`, `pdf_path`, `user_feedback`, `retry_count`) and application fields (`application_url`, `application_type`, `application_result`, `applied_at`). Keep: `job_id`, `source`, `mode`, `status`, `workflow_step`, `job_posting`, `raw_input`, `created_at`, `updated_at`, `error_message`. Add `current_cv_json: dict | None` and `current_pdf_path: str | None` as denormalized quick-access fields
- [ ] Create `src/models/cv_attempt.py` with `CVCompositionAttempt(BaseModel)`: `job_id: str`, `attempt_number: int`, `user_feedback: str | None`, `cv_json: dict`, `pdf_path: str | None`, `created_at: datetime`
- [ ] Enrich `HITLDecision` in `unified.py`: add `decided_at: datetime = Field(default_factory=datetime.utcnow)` and `reasoning: str | None = None`
- [ ] Add `create_cv_attempt()`, `get_cv_attempts(job_id)`, and `get_latest_cv_attempt(job_id)` methods to the `JobRepository` abstract class
- [ ] Implement the new methods in `InMemoryJobRepository` (using a `_cv_attempts: dict[str, list[CVCompositionAttempt]]`)
- [ ] Implement the new methods in `SQLiteJobRepository` (new Piccolo table `CVAttempt`)
- [ ] Update `preparation_workflow.py:save_to_db_node` and `retry_workflow.py:update_db_node` to create a `CVCompositionAttempt` record alongside updating `JobRecord`
- [ ] Update `HITLProcessor` and `JobOrchestrator` to use the new model structure
- [ ] Update existing tests and add tests for the new repository methods
- [ ] Run project test suite — must pass before task 7

### Task 7: Define job lifecycle state machine

Addresses **Finding #7** (No state transition validation). Create separate `WorkflowStep` and `BusinessState` enums. Add transition validation in the repository.

**Files:**
- Create: `src/models/state_machine.py`
- Modify: `src/models/unified.py`
- Modify: `src/services/job_repository.py`
- Modify: `src/agents/preparation_workflow.py`
- Modify: `src/agents/retry_workflow.py`
- Modify: `src/agents/application_workflow.py`
- Modify: `src/services/job_orchestrator.py`
- Modify: `src/services/hitl_processor.py`
- Create: `tests/unit/test_state_machine.py`

- [ ] Create `src/models/state_machine.py` with two enums: `WorkflowStep` (extracting, filtering, composing_cv, generating_pdf, loading, applying) and `BusinessState` (queued, processing, cv_ready, pending_review, approved, declined, retrying, applying, applied, failed). Add `ALLOWED_TRANSITIONS: dict[BusinessState, set[BusinessState]]` mapping each state to its valid successors. Add a `validate_transition(current: BusinessState, target: BusinessState) -> bool` function that checks the map and raises `InvalidStateTransition` if disallowed
- [ ] Update `JobRecord` in `unified.py` to use the new enums: `status: BusinessState`, `workflow_step: WorkflowStep | None`
- [ ] Add transition validation in `JobRepository.update()`: when `status` is in the updates dict, call `validate_transition(current_status, new_status)` before applying. Both `InMemoryJobRepository` and `SQLiteJobRepository` must enforce this
- [ ] Update all workflow nodes to use the new enum values instead of string literals (e.g., `state["status"] = BusinessState.PROCESSING` instead of `state["current_step"] = "extracting"`)
- [ ] Update `JobOrchestrator` and `HITLProcessor` to use enum values
- [ ] Update API response serialization to handle enum → string conversion for JSON responses
- [ ] Write comprehensive tests in `tests/unit/test_state_machine.py`: test all valid transitions succeed, test invalid transitions raise `InvalidStateTransition`, test terminal states have no successors (except `failed → retrying`)
- [ ] Update existing tests that compare status strings to use enum values
- [ ] Run project test suite — must pass before task 8

### Task 8: Fix queue consumer silent death

Addresses **Finding #8** (Queue consumer gives up silently after 5 restarts). Make the failure observable and recoverable.

**Files:**
- Modify: `src/api/main.py` (consumer restart logic)
- Modify: `src/services/job_queue.py`

- [ ] Extract consumer restart logic from `api/main.py` (lines ~124-200) into a `ConsumerManager` class in `src/services/job_queue.py`. Fields: `max_restarts`, `backoff_base`, `restart_count`, `is_healthy: bool`. Method: `start(queue, workflow, ...)`, `stop()`, `health_check() -> dict`
- [ ] When max restarts exceeded, set `is_healthy = False` and log at CRITICAL level (not just ERROR). Include instructions in the log message on how to manually restart
- [ ] Add a `/api/health` enhancement: include `queue_consumer_healthy: bool` in the health check response so monitoring can detect the silent death
- [ ] In `job_queue.py:put_batch()`, when queue is full and jobs are dropped, emit a WARNING that includes the total jobs dropped AND the job titles/IDs that were dropped (not just a count)
- [ ] Write tests for `ConsumerManager`: test restart count tracking, test health_check reflects state, test max restarts behavior
- [ ] Run project test suite — must pass before task 9

### Task 9: Deduplicate workflow code

Addresses **Finding #9** (Code duplication across workflows). Extract shared node logic into `src/agents/_shared.py`.

**Files:**
- Create: `src/agents/_shared.py`
- Modify: `src/agents/preparation_workflow.py`
- Modify: `src/agents/retry_workflow.py`
- Modify: `src/agents/application_workflow.py`

- [ ] Create `src/agents/_shared.py` with shared utilities extracted from the three workflow files
- [ ] Extract `_init_llm_client(llm_provider, llm_model)` — identical in all 3 workflows — into `_shared.py` as `create_llm_client()`
- [ ] Extract `load_master_cv()` from `preparation_workflow.py` into `_shared.py`
- [ ] Extract the shared CV composition logic (initializing `CVComposer`, calling `compose_cv()`, handling errors) into a shared async function `compose_cv(state, repository, settings) -> dict` in `_shared.py`. Both `preparation_workflow.compose_cv_node` and `retry_workflow.compose_cv_node` should call this
- [ ] Extract PDF generation logic (filename construction, path sanitization, `PDFGenerator` initialization) into a shared `generate_pdf(state, settings) -> dict` in `_shared.py`. Both workflows' `generate_pdf_node` should call this, with retry_workflow passing an extra version suffix
- [ ] Remove the now-duplicated code from all three workflow files. Each node function should be a thin wrapper calling into `_shared.py`
- [ ] Verify no behavior change by running existing tests
- [ ] Run project test suite — must pass before task 10

### Task 10: Split CVComposer and fix hallucination checks

Addresses **Findings #10 and #11** (CVComposer does too much; hallucination checks never raise). Extract validation into `CVValidator`. Make hallucination policy configurable and actually enforced.

**Files:**
- Create: `src/services/cv_validator.py`
- Modify: `src/services/cv_composer.py`
- Modify: `src/config/settings.py`

- [ ] Create `src/services/cv_validator.py` with class `CVValidator`. Move these methods from `CVComposer`: `_validate_contact()`, `_validate_languages()`, `_validate_interests()`, `_validate_output()` (hallucination checks). `CVValidator.__init__` takes `master_cv: dict` and `policy: HallucinationPolicy`
- [ ] Add `HallucinationPolicy` enum to `cv_validator.py`: `STRICT` (raise `CVHallucinationError` on fabricated companies/institutions), `WARN` (log warning, current behavior), `DISABLED` (skip checks)
- [ ] Wire the existing `settings.cv_composer_enable_hallucination_checks` to actually control the policy: `True` → `STRICT`, `False` → `DISABLED`. Add a new setting `cv_composer_hallucination_policy: str = "strict"` for fine-grained control (`strict`, `warn`, `disabled`)
- [ ] Update `CVComposer.compose_cv()` to accept a `CVValidator` instance and call `validator.validate(tailored_cv, master_cv)` after composition. Remove the moved methods from `CVComposer`
- [ ] In `STRICT` mode, `CVHallucinationError` should include the specific fabricated entities so the error message is actionable
- [ ] Update `tests/unit/test_cv_composer.py` with tests for the new validation: test STRICT mode raises on hallucinated company, test WARN mode logs but doesn't raise, test DISABLED mode skips entirely
- [ ] Run project test suite — must pass before task 11

### Task 11: Remove legacy MVP endpoints and consolidate models

Addresses **Finding #12** (Two codepath divergence). Remove legacy `/api/cv/*` endpoints entirely. Remove `src/models/mvp.py`. Unified endpoints handle both MVP and full mode.

**Files:**
- Modify: `src/api/main.py`
- Delete: `src/models/mvp.py`
- Modify: `src/models/unified.py` (absorb any needed MVP fields)

- [ ] Remove the 3 legacy endpoints from `api/main.py`: `POST /api/cv/generate` (lines ~320-382), `GET /api/cv/status/{job_id}` (lines ~385-424), `GET /api/cv/download/{job_id}` (lines ~427-470)
- [ ] Remove `workflow_threads` and `workflow_created_at` tracking dicts (legacy tracking — should already be gone from Task 1, verify)
- [ ] Remove `run_workflow_async()` helper if it was only used by legacy endpoints
- [ ] Check if `JobDescriptionInput` from `mvp.py` is used by unified endpoints. If so, move it to `unified.py`. If not, delete the import
- [ ] Delete `src/models/mvp.py` entirely (after confirming `CVGenerationResponse`, `CVGenerationStatus` are not referenced elsewhere)
- [ ] Remove all imports of `mvp` models throughout the codebase
- [ ] If the UI references legacy endpoint paths, update the UI API client (check `ui/src/` for `/api/cv/` references)
- [ ] Update tests that target legacy endpoints — remove or migrate to unified endpoint equivalents
- [ ] Run project test suite — must pass before task 12

### Task 12: Verify acceptance criteria

- [ ] Manual test: start the API server (`uv run uvicorn src.api.main:app --reload`), submit a job via `POST /api/jobs/submit`, verify status via `GET /api/jobs/{id}/status`, verify HITL flow via `GET /api/hitl/pending` and `POST /api/hitl/{id}/decide`
- [ ] Manual test: verify that invalid state transitions are rejected (e.g., try to approve an already-declined job — should get 400)
- [ ] Manual test: verify that legacy `/api/cv/*` endpoints return 404
- [ ] Run full test suite: `uv run pytest tests/ -v`
- [ ] Run type checker: `uv run mypy src/`
- [ ] Grep for remaining `asyncio.run(` calls in `src/` — should find zero
- [ ] Grep for remaining `_repository` globals in `src/agents/` — should find zero
- [ ] Grep for remaining `NotImplementedError` catches in `src/agents/` — should find zero

### Task 13: Update documentation

- [ ] Update `CLAUDE.md`: reflect new architecture (AppContext DI, domain services, state machine, async workflows), update directory structure section, update implementation status table, remove references to legacy endpoints
- [ ] Update `CLAUDE.md` common tasks sections: "Adding a New Workflow Step" should reference `_shared.py` and async node pattern
- [ ] Move this plan to `docs/plans/completed/`
