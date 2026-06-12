# Feature Specification: Persist Jobs at Discovery

## Overview
- **Feature**: Save scraped/submitted jobs to the database the moment they are discovered, not at the end of CV generation.
- **Status**: Draft (re-audited against codebase)
- **Created**: 2026-05-19
- **Revised**: 2026-05-31 — re-aligned with current code; `WorkflowDispatcher`, scrape-failure-recovery foundation, and state-enum names updated. None of the user-facing requirements have changed.
- **Author**: User + Claude Code

## Problem Statement

Today a `JobRecord` is only written to the repository at the very end of `preparation_workflow.save_to_db_node`, after extraction → filtering → CV composition → PDF generation have all succeeded. Two consequences:

1. **No transparency during processing.** The UI cannot show that a job has been picked up by the scraper or that CV composition is in flight — the job is invisible until the entire pipeline finishes (typically tens of seconds of LLM + PDF work per job).
2. **Queue loss on restart.** The pipeline relies on an in-process `asyncio.Queue` (`src/services/jobs/job_queue.py`). When the VPS deploy restarts the container, every job that the scraper had enqueued but the consumer had not yet finished is silently dropped. There is no on-disk record of those discoveries, so they are gone until the next scheduled scrape happens to find them again — and may not, since LinkedIn results rotate.

The goal is to persist a `JobRecord` at the earliest possible moment, advance its status as the workflow progresses, and recover any in-flight rows on startup.

## Current State of the Codebase (audit, 2026-05-31)

Since the original plan was written, the scrape-failure-recovery work landed (see `docs/plans/scrape-failure-recovery.md`) and a `WorkflowDispatcher` abstraction was introduced. Several pieces that the plan asks for are now already partly there; this section captures what is and isn't done so the implementation phase doesn't re-do existing work.

**Already in place — do not re-add:**
- `BusinessState.SCRAPE_FAILED` exists (`src/models/state_machine.py:55`), and `ALLOWED_TRANSITIONS` already permits `PROCESSING → QUEUED` and `SCRAPE_FAILED → QUEUED` for recovery re-entry. **No transition-map changes are required.**
- `JobRecord` already carries `scrape_attempts`, `last_scrape_error`, `last_scrape_attempt_at` (`src/models/unified.py`) and a `workflow_step: WorkflowStep | None` field (Pydantic-only — not persisted to the `job` table).
- `_should_retry_scrape(existing)` exists in `src/services/jobs/job_queue.py` and is the canonical predicate for "is this row eligible for another scrape pass?". The plan's pre-enqueue dedup must reuse it.
- `WorkflowDispatcher` (`src/agents/dispatcher.py`) centralizes preparation/retry dispatch. It already has a `create_failure_record=True` path that synthesizes a FAILED `JobRecord` if the workflow blows up before `save_to_db_node`. Once early-insert lands, callers can drop `create_failure_record=True` — the row will always exist and the dispatcher's update-only branch (via `ALLOWED_TRANSITIONS`) takes over.
- `JobOrchestrator.submit_job` already calls `ctx.register_workflow(...)` synchronously before dispatching, so `GET /api/jobs/{id}/status` works even when no DB row exists. This in-memory tracking remains useful for surfacing live `current_step`; it is **not** a substitute for a DB row.
- DB migrations are managed by `src/services/db/migrations.py` — a `MIGRATIONS` tuple of `Migration(name, ddl_callable)` entries applied on startup. Piccolo's own migration files are **not** the pattern here.

**Still missing — this plan delivers these:**
- Pre-enqueue / pre-dispatch `repo.create(JobRecord(status=QUEUED, ...))` at both discovery sites (scheduler + `JobOrchestrator.submit_job`).
- `recovery_attempts` and `last_recovery_attempt_at` columns on `JobRecord` + `job` table (+ two new entries in `MIGRATIONS`).
- `JobRepository.list_by_states(states, *, user_id=None)` — the abstract interface has `get_by_status` (single status, user-scoped) and `get_pending`, but no multi-state variant suitable for the recovery scan or admin queue view.
- `recover_in_flight_jobs(ctx)` startup hook (called from the `lifespan` in `src/api/main.py` after `AppContext` is built and the consumer/scheduler are wired).
- `extract_job_node` flipping `QUEUED → PROCESSING` as its first action (idempotent thanks to the self-transition rule already in `ALLOWED_TRANSITIONS`).
- Removal of the defensive `if existing is None: create else: update` branching in `save_to_db_node`, `save_filtered_out_node`, `save_scrape_failed_node`.
- `?states=` filter on `GET /api/hitl/pending` (route now lives in `src/api/routes/hitl.py`).
- `queued_count` / `processing_count` on `GET /api/health` (route in `src/api/routes/system.py`, currently unpacks `consumer_health` only).
- UI: extend `ui/src/lib/api/hitl.ts` and the dashboard route to render read-only in-flight cards.

## Goals & Success Criteria

- **Transparency**: every scraped/submitted job is visible in the UI with a live status (queued → processing → composing → ready/applied/failed).
- **Resilience**: a service restart loses zero jobs that had been discovered before the restart; they are picked up by the consumer after the restart.
- **No regressions**: existing dedup, filter, scrape-failure-retry, and HITL semantics continue to work.
- **Success Metrics**:
  - `jobs_discovered_total` counter equals scraper output count (no silent drops).
  - After a forced container restart with N jobs mid-flight, all N reach a terminal state without manual intervention (verified in staging).
  - `/api/health` reports a non-zero `queued_count` immediately after a scrape run, before the consumer has processed anything.

## User Stories

1. As a job-seeker, I want to see jobs appear in my dashboard as soon as the scraper finds them, so I know the system is working and can monitor the pipeline.
2. As an operator, I want a deploy/restart to not silently lose work, so I can ship updates during business hours without coordinating around the queue.
3. As a job-seeker, I want a clicked-through in-flight job to show me what stage it's in (queued / scraping / filtering / composing CV / generating PDF), so the wait feels intentional, not broken.

## Functional Requirements

### Core Capabilities

- A `JobRecord` is created with `status=QUEUED` **before** any work happens on it (before the scraper enqueues to the in-memory queue, and before `JobOrchestrator` dispatches the preparation workflow for manual submissions).
- The record's `status` advances through `QUEUED → PROCESSING → (COMPLETED | PENDING | FILTERED_OUT | SCRAPE_FAILED | FAILED)` as the workflow runs, reflecting the actual lifecycle. (Enum names per `src/models/state_machine.py`: `COMPLETED` is the MVP terminal; `PENDING` is the full-mode "awaiting HITL review" state.)
- On application startup, jobs found in non-terminal states are re-enqueued so the consumer can pick them up. A bounded `recovery_attempts` counter prevents poison rows from looping forever.
- The HITL/list API surfaces in-flight rows (queued, processing, etc.) when the caller opts in via a `states` filter.
- The UI shows in-flight jobs as read-only cards with a status badge + spinner; review actions only appear once `status=PENDING`.

### User Flows

#### Flow A — LinkedIn scrape (scheduler tick)

1. `LinkedInScraper.scrape_and_enrich()` returns a `list[ScrapedJob]` for a user (`src/services/jobs/scheduler.py:run_search`).
2. For each `ScrapedJob`, the scheduler computes `scoped_job_id = f"{job_id}:{user_id}"` and calls `ctx.repository.get(scoped_job_id)`.
3. If a record exists and `_should_retry_scrape(existing)` returns False → skip (and **do not** call `queue.put`). The existing `_should_retry_scrape` helper from `src/services/jobs/job_queue.py` is reused as-is — pull it up so the scheduler can import it.
4. Otherwise insert a new `JobRecord(status=QUEUED, source="linkedin", mode="full", job_posting={preview}, raw_input={full_scraped})` and **then** call `queue.put(scraped_job, user_id=plan_user_id)` so the consumer can pick it up. Today scheduler calls `queue.put_batch(jobs, user_id=...)` — replace with a per-job loop that does create-then-put. Keep `put_batch` for the fixture replay path.
5. Consumer (`process_queue`) dequeues, calls `repo.update(status=PROCESSING)`, then dispatches via `ctx.workflow_dispatcher.dispatch_preparation(...)`. The defensive dedup check in `process_queue` (lines ~205–222) becomes a belt-and-suspenders no-op for newly-discovered rows but stays for the race between overlapping scrape ticks.
6. Each workflow node updates `current_step` (already does today). Terminal nodes call `repo.update(status=TERMINAL_STATE)` instead of `repo.create(...)`. The dispatcher's `create_failure_record=True` flag can be flipped to `False` for scheduler dispatch — the row is guaranteed to exist.

#### Flow B — Manual submission (POST /api/jobs/submit)

1. `JobOrchestrator.submit_job` validates the request, generates `job_id`.
2. **NEW** before scheduling the background task, it `repo.create(JobRecord(status=QUEUED, job_posting={preview from raw_input}, raw_input={...}))`.
3. The existing synchronous `ctx.register_workflow(...)` call remains (it tracks live `current_step` in memory, useful for status polling between PROCESSING transitions).
4. Returns `JobSubmitResponse(job_id, status=QUEUED)` to the caller — now backed by a real DB row.
5. Background task invokes `ctx.workflow_dispatcher.dispatch_preparation(...)`, which `repo.update(status=PROCESSING)` first (or rather, `extract_job_node` does — see Integration Points) and then updates as nodes complete. With the row guaranteed to exist, `create_failure_record` becomes redundant; the dispatcher's `ALLOWED_TRANSITIONS`-aware `_mark_preparation_failed` branch handles failure updates.

#### Flow C — Startup recovery

1. App lifespan startup, after `AppContext` is created (`prep_workflow` compiled, `consumer_manager` started), run `await recover_in_flight_jobs(ctx)` in `src/api/main.py` `lifespan`.
2. Query `repo.list_by_states([QUEUED, PROCESSING, RETRYING])`. Filter out anything currently registered in `ctx._workflow_threads` (a recovery shouldn't double-dispatch a workflow that's still alive in this process — but on a fresh boot the in-memory tracker is empty, so this is just defensive).
3. For each row: increment `recovery_attempts`, stamp `last_recovery_attempt_at`. If `recovery_attempts > MAX_RECOVERY_ATTEMPTS` (e.g. 3) → `repo.update(status=FAILED, error_message="restart loop guard")`. Else → reconstruct a `QueueItem`/initial state from `raw_input` and dispatch:
   - `source == "linkedin"` → flip the row back to `QUEUED` if needed and `ctx.job_queue.put(...)` so the consumer picks it up via the normal path.
   - `source in ("url", "manual")` → call `ctx.workflow_dispatcher.dispatch_preparation(...)` directly with a new `thread_id` (don't `await`; wrap in `ctx.create_background_task(...)`).
4. Emit `logger.info("Recovered %d jobs on startup (%d exhausted, %d skipped)", recovered, exhausted, skipped)`.

#### Flow D — UI viewing an in-flight job

1. User opens dashboard. Front-end calls `GET /api/hitl/pending?states=queued,processing,pending`.
2. Cards rendered:
   - `pending` → existing Tinder card with approve/decline/retry.
   - `queued | processing | retrying` → read-only card with company/title/url, badge for current state (mapped from `workflow_step` for fine-grained labels: "Scraping", "Filtering", "Composing CV", "Generating PDF"), no action buttons.
3. Front-end polls `GET /api/jobs/{job_id}/status` every ~3 s while card is visible and not in a terminal state.

### Data Model

#### `JobRecord` additions (`src/models/unified.py`)

```python
class JobRecord(BaseModel):
    # ... existing fields ...

    # NEW: bounded recovery counter, increments on each startup requeue.
    recovery_attempts: int = 0

    # NEW: last time we attempted a startup recovery (for diagnostics).
    last_recovery_attempt_at: datetime | None = None
```

#### `BusinessState` (`src/models/state_machine.py`)

No new states. `QUEUED` is reused for the initial insert; `PROCESSING` covers the entire workflow run after dequeue.

`ALLOWED_TRANSITIONS` already permits `PROCESSING → QUEUED` and `SCRAPE_FAILED → QUEUED` for retry re-entry (added during the scrape-failure-recovery work). **No transition-map changes are required for this feature.** Verify the test in `tests/test_state_machine.py` still asserts these transitions after any refactor.

#### Repository (abstract: `src/services/db/repository.py`, impls: `src/services/db/job_repository.py`)

New method on the abstract interface and both implementations:

```python
async def list_by_states(self, states: list[BusinessState], *, user_id: str | None = None) -> list[JobRecord]:
    """Return all jobs whose status is in `states`, optionally scoped to a user."""
```

The Piccolo-backed SQLite implementation uses `JobTable.status.is_in([s.value for s in states])` plus optional user filter. The in-memory implementation iterates the dict.

`UPDATABLE_FIELDS` in `src/services/db/repository.py` must be extended to include `recovery_attempts` and `last_recovery_attempt_at` so the recovery scan can write them via `repo.update()`.

A new pair of entries in the `MIGRATIONS` tuple (`src/services/db/migrations.py`) adds:

```python
Migration(
    "add_job_recovery_attempts",
    _add_column("job", "recovery_attempts", "recovery_attempts INTEGER NOT NULL DEFAULT 0"),
),
Migration(
    "add_job_last_recovery_attempt_at",
    _add_column("job", "last_recovery_attempt_at", "last_recovery_attempt_at TIMESTAMP NULL"),
),
```

These follow the same in-process apply-on-startup pattern used by `add_job_scrape_attempts` et al. Piccolo's own migration files are **not** the pattern in this repo.

### Integration Points

| Touchpoint | Change |
|------------|--------|
| `Scheduler.run_search` (`src/services/jobs/scheduler.py`, currently calls `self.queue.put_batch(jobs, user_id=...)`) | Replace `put_batch` with a per-job loop: `get` → dedup via `_should_retry_scrape` → `repo.create(JobRecord(QUEUED, ...))` → `self.queue.put(job, user_id=...)`. Inject `ctx.repository` (Scheduler already has it through constructor wiring — verify). |
| `process_queue` (`src/services/jobs/job_queue.py`) | Dequeue → `repo.update(status=PROCESSING)` → `ctx.workflow_dispatcher.dispatch_preparation(..., create_failure_record=False)`. Remove the inline `repo.create(JobRecord(...))` fallback inside the exception handler — the dispatcher already handles `_mark_preparation_failed` via update. Keep the `_should_retry_scrape` dedup check as a belt-and-suspenders guard for overlapping scrape ticks. |
| `JobOrchestrator.submit_job` (`src/services/jobs/job_orchestrator.py`) | Insert `JobRecord(QUEUED)` synchronously before `ctx.create_background_task(dispatcher.dispatch_preparation(...))`. Set `create_failure_record=False` since the row is now guaranteed to exist. Keep the `register_workflow` call. |
| `WorkflowDispatcher` (`src/agents/dispatcher.py`) | No structural change required, but `_mark_preparation_failed`'s `create_record_if_missing` branch becomes dead code once both callers stop passing `create_failure_record=True`. Mark it for removal in a follow-up cleanup; leave the branch in place for one release as a safety net. |
| `preparation_workflow.save_to_db_node` | Becomes `repo.update`-only. The `if existing is None: create else: update` branch is deleted. |
| `preparation_workflow.save_filtered_out_node` | Same: `repo.update`-only. |
| `preparation_workflow.save_scrape_failed_node` | Same: `repo.update`-only. The scrape-attempts increment logic stays. |
| `preparation_workflow.extract_job_node` | First step: `repo.update(status=PROCESSING)` if the row is still `QUEUED`. (Self-transition `PROCESSING → PROCESSING` is allowed, so re-entry from recovery is safe.) |
| `AppContext` lifespan startup (`src/api/main.py` `lifespan`) | Call `await recover_in_flight_jobs(ctx)` **after** the consumer manager and scheduler are started, so any re-enqueued LinkedIn rows have a consumer waiting. Wrap in try/except — recovery failure must not block startup. |
| `GET /api/hitl/pending` (`src/api/routes/hitl.py`) | Accept optional `states: str` query param (comma-separated). Default unchanged (`pending` only). When `?states=queued,processing,pending` is passed, validate each token against `BusinessState`, 400 on unknown. Delegates to a new `HITLProcessor.get_in_flight(user_id, states)` method. |
| `GET /api/health` (`src/api/routes/system.py`) | Add `queued_count`, `processing_count` fields (cheap repo count query — reuse `repo.get_status_counts(user_id=None)` if we extend it to support global counts, or add a focused `count_by_state` helper). Current code unpacks `consumer_health` at the top level; add the new fields alongside. |
| UI dashboard (`ui/src/routes/+page.svelte`) | Request the broader state list; render read-only "in-flight" cards for non-terminal items. Map `workflow_step` (already on the Pydantic `JobRecord`) to a friendly badge. |
| UI client (`ui/src/lib/api/hitl.ts`) | `fetchPendingApprovals(states?: BusinessState[])` accepts an optional state list and forwards as `?states=...`. |

## Technical Design

### Architecture

The chosen insertion point is **at the scraper/submitter boundary, just before the in-memory queue or workflow dispatch**. Rationale:

- It is the earliest moment we have a `job_id` and enough raw data to render a useful row.
- A crash between scrape and enqueue is then also covered: the row exists; the recovery pass will requeue it.
- Both LinkedIn-pipeline and manual-submit flows converge on the same primitive: "create the row, then schedule the work."
- The `JobOrchestrator.get_status` workflow-thread fallback (against `ctx._workflow_threads`) remains useful for surfacing live `workflow_step` between PROCESSING transitions, but the repo becomes authoritative for the row's existence and overall status.

Workflow nodes become pure mutators: they assume the row exists and call `repo.update(...)`. This kills the `if existing is None: create else: update` branching that pervades the three save_* nodes today, and lets `WorkflowDispatcher._mark_preparation_failed` rely on a simple update-with-transition-guard path (its `create_record_if_missing` branch becomes vestigial).

### Technology Stack

- **Frameworks**: existing LangGraph + FastAPI + Piccolo ORM stack.
- **Libraries**: nothing new.
- **Tools**: piccolo migrations for the two new columns.

### Data Persistence

- Continues to use `SQLiteJobRepository` (piccolo) in production and `InMemoryJobRepository` in tests.
- All new write paths go through `JobRepository.create` / `JobRepository.update`, which already validate transitions.
- The in-memory `JobQueue` remains the work-coordination primitive between scraper and consumer; the DB is the durability layer.

### API / Interface Design

#### Modified: `GET /api/hitl/pending`

```
GET /api/hitl/pending?states=queued,processing,pending
```

- New optional query param `states` (CSV of `BusinessState` values).
- Default behavior unchanged: returns `pending` only.
- Validates each token against `BusinessState`; rejects unknown tokens with 400.
- Response items (`PendingApproval` in `src/models/unified.py`) extended with a `workflow_step` field (already on `JobRecord`) so the UI can render fine-grained badges ("Composing CV", etc.) for `PROCESSING` rows.

#### New: `recover_in_flight_jobs(ctx: AppContext) -> RecoveryReport`

```python
@dataclass
class RecoveryReport:
    recovered: int
    exhausted: int
    skipped: int

async def recover_in_flight_jobs(ctx: AppContext) -> RecoveryReport: ...
```

Called once during FastAPI lifespan startup. Idempotent w.r.t. multiple invocations (the recovery_attempts counter caps loops).

#### Modified: `JobOrchestrator.submit_job`

```python
async def submit_job(self, request, user_id, master_cv, model_preferences=None) -> JobSubmitResponse:
    # ... existing validation ...
    job_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())

    # NEW: persist QUEUED row before dispatching workflow
    preview = self._build_job_posting_preview(request)
    now = datetime.now(tz=timezone.utc)
    await self._ctx.repository.create(JobRecord(
        job_id=job_id,
        user_id=user_id,
        source=request.source,
        mode=request.mode,
        status=BusinessState.QUEUED,
        job_posting=preview,
        raw_input=raw_input,
        created_at=now,
        updated_at=now,
    ))

    # Existing: register in-memory tracker (kept — useful for live workflow_step polling)
    await self._ctx.register_workflow(job_id, thread_id, "preparation", user_id=user_id)

    # Existing: dispatch background — but flip create_failure_record off now that row exists
    self._ctx.create_background_task(
        self._ctx.workflow_dispatcher.dispatch_preparation(
            job_id=job_id,
            thread_id=thread_id,
            initial_state=initial_state,
            user_id=user_id,
            create_failure_record=False,
        )
    )
    return JobSubmitResponse(job_id=job_id, status=BusinessState.QUEUED, ...)
```

#### Modified: scheduler enqueue site

Today `Scheduler.run_search` calls `await self.queue.put_batch(jobs, user_id=plan_user_id)` (`src/services/jobs/scheduler.py`). Replace with:

```python
from src.services.jobs.job_queue import _should_retry_scrape  # pull this up into a shared module

now = datetime.now(tz=timezone.utc)
enqueued = 0
for scraped_job in jobs:
    scoped_id = self.queue._scoped_id(scraped_job.job_id, plan_user_id)  # or refactor to a shared helper
    existing = await self.repository.get(scoped_id)
    if existing is not None and not _should_retry_scrape(existing):
        continue  # dedup: terminal or recently-attempted — skip
    if existing is None:
        await self.repository.create(JobRecord(
            job_id=scoped_id,
            user_id=plan_user_id,
            source="linkedin",
            mode="full",
            status=BusinessState.QUEUED,
            job_posting={
                "title": scraped_job.title,
                "company": scraped_job.company,
                "url": scraped_job.url,
                "location": scraped_job.location,
            },
            raw_input=scraped_job.model_dump(),
            created_at=now,
            updated_at=now,
        ))
    await self.queue.put(scraped_job, user_id=plan_user_id)
    enqueued += 1
logger.info("Enqueued %d jobs for user=%s (deduped %d)", enqueued, plan_user_id, len(jobs) - enqueued)
```

The existing dedup branch in `process_queue` becomes a belt-and-suspenders no-op for newly-discovered rows but stays in place to handle the race between overlapping scrape ticks. (See Design Trade-offs.)

**Note on the "3 jobs found but nothing in DB" symptom (2026-05-31 prod report):** Today the scheduler returns the "3 jobs" count *before* `process_queue` does dedup, so silent dedups look like silent drops in the scheduler log. After this change, the scheduler log will distinguish enqueued vs. deduped, and the deduped rows will already be in the DB from previous runs — making the discrepancy visible and explicable.

## Non-Functional Requirements

- **Performance**: One extra DB INSERT per scraped job (currently ~10–50 per scheduler tick per user). Negligible vs. the LLM call. The recovery scan on startup is a single `SELECT WHERE status IN (...)` — bounded by the number of in-flight jobs.
- **Security**: All new queries remain user-scoped via the existing `user_id` filter in `list_by_states`. The global `user_id=None` form is admin-only (the existing `/api/admin/*` routes already gate via `get_admin_user`).
- **Observability**:
  - Log line `jobs_discovered total=%d enqueued=%d deduped=%d source=%s user=%s` per scheduler tick (replaces today's "Enqueued N jobs" which conflates scraper output and actual enqueue count).
  - Single startup log line: `Recovered N jobs on startup (M exhausted, K skipped)`.
  - `/api/health` extended with `queued_count`, `processing_count` (alongside the existing unpacked `consumer_health` fields).
  - The diagnostic the user hit ("3 jobs found · in 42 min" with no DB rows) becomes self-explanatory once dedup vs. enqueue counts are logged separately.
- **Error Handling**:
  - If the pre-queue `repo.create()` fails (e.g., DB locked), log + skip the enqueue. Next scrape tick will retry naturally.
  - If `repo.update(PROCESSING)` fails in the consumer, log and proceed — the workflow may still succeed and the final `save_to_db_node` update will reconcile state.
  - If `recover_in_flight_jobs` itself raises, log loudly but **do not** fail startup — the system can run, scrapes still create new rows, and a future restart will re-attempt recovery.

## Implementation Considerations

### Design Trade-offs

| Decision | Considered | Chosen | Rationale |
|----------|-----------|--------|-----------|
| Insertion point | (a) pre-queue at scraper, (b) inside consumer, (c) first workflow node | **(a) pre-queue at scraper + manual-submit** | Earliest possible point; uniformly covers both flows; eliminates the special-case in the orchestrator's exception handler. |
| State enum | New `DISCOVERED` state vs. reuse `QUEUED` | **Reuse `QUEUED`** | Existing `QUEUED → PROCESSING → ...` transition graph already fits the semantics. Avoids migration + transition map changes. |
| Workflow save_* nodes | Defensive create-or-update vs. update-only | **Update-only** | The row is now guaranteed to exist; defensive checks accumulate as cruft. Test coverage will catch the contract violation if it ever breaks. |
| Recovery cap | No cap vs. counter vs. time-window | **Per-row `recovery_attempts` counter (max 3)** | Bounded protection against a poison row; survives multiple restarts within the same day (time-window approach would not). |
| API shape | New `/api/jobs/in-flight` vs. extended `/api/hitl/pending` | **Extend with `?states=...`** | One endpoint, opt-in for callers that want the broader view; HITL default behavior unchanged. |
| UI for in-flight | Disabled review card vs. dedicated read-only card vs. hide | **Read-only progress card** | Surfaces transparency value without confusing users with disabled approve/decline buttons. |
| Dedup location | Move into scraper only vs. keep also in consumer | **Move into scraper; keep light defensive check in consumer** | Belt-and-suspenders: scraper is authoritative, consumer's check protects against race if two scrape ticks overlap. |

### Dependencies

- Two new entries in `MIGRATIONS` (`src/services/db/migrations.py`) adding `recovery_attempts INTEGER NOT NULL DEFAULT 0` and `last_recovery_attempt_at TIMESTAMP NULL` to the `job` table. (In-process migrations apply on `UserRepository.initialize()` / `SQLiteJobRepository.initialize()`; **not** Piccolo's own migration file mechanism.)
- Corresponding `Timestamptz`/`Integer` columns added to `Job` in `src/services/db/tables.py` so the ORM mapping stays in sync.
- Two new fields on `JobRecord` (`src/models/unified.py`) and two new entries in `UPDATABLE_FIELDS` (`src/services/db/repository.py`).
- No new third-party packages.
- VPS deploy already applies in-process migrations on container start.

### Testing Strategy

- **Unit**
  - `test_job_repository.list_by_states` — returns rows in given states; respects user_id scope.
  - `JobOrchestrator.submit_job` — verifies a `JobRecord` exists with `status=QUEUED` immediately after the call returns, before the background task runs.
  - Scheduler enqueue site — given an existing record in a terminal state, no duplicate insert and no enqueue.
  - `recover_in_flight_jobs` — happy path requeues, exhausted path marks FAILED, idempotency.
- **Integration**
  - Workflow run end-to-end with a pre-created `QUEUED` row: ensure save_* nodes update (not create) and final state is correct.
  - Simulated restart: insert N rows in `QUEUED`/`PROCESSING` state, call recovery, run consumer, assert all reach terminal.
- **API**
  - `/api/hitl/pending?states=queued,processing,pending` returns all three classes for the auth'd user only.
  - `/api/health` reports correct counts when queue is non-empty.
- **E2E**
  - Submit a manual job, immediately fetch `/api/hitl/pending?states=queued,processing,pending` — the job appears with `queued`/`processing` status.
  - Kill the API container mid-workflow (force-restart in staging); after restart, the in-flight job completes.

## Out of Scope

- Cross-process queue (Redis / RabbitMQ). The `asyncio.Queue` stays in-process; durability is provided purely by persisting at discovery + recovery scan on startup.
- Background recovery during normal operation (e.g. periodic sweep of stale `PROCESSING` rows). Only startup recovery is in scope; if the consumer dies mid-job without a restart, that row is handled by existing `ConsumerManager` restart logic.
- Per-step granular DB writes inside the workflow (currently `current_step` lives in workflow state, not the DB). The UI maps `current_step` from the running thread for `PROCESSING` rows; we don't persist every node transition.
- Resurfacing previously-declined / filtered-out jobs after a TTL — out of scope; today's "skip if any record exists" semantics are preserved.

## Open Questions

- Should `workflow_step` be persisted on the `job` table (it's already in the Pydantic `JobRecord` and `UPDATABLE_FIELDS`, but the underlying `Job` Piccolo table has no column for it) so the UI can read fine-grained progress for `PROCESSING` rows even after a restart? Leaning yes — add a third migration in the same batch to add `workflow_step VARCHAR(40) NULL`. Light-weight UPDATE per node.
- For LinkedIn jobs that get persisted at discovery but then permanently fail extraction (LinkedIn page gone), do we want a separate `EXPIRED` terminal state, or is `SCRAPE_FAILED → FAILED` sufficient? Probably sufficient — `_should_retry_scrape` already drives the transition to `FAILED` when `scrape_attempts` exceeds `settings.scraper_max_attempts`.
- The `recovery_attempts` cap of 3 is a guess; real value should be tuned after we see how often deploys catch jobs mid-flight in production. Consider reusing `settings.scraper_max_attempts` for symmetry.
- Should `recover_in_flight_jobs` distinguish between rows that were `PROCESSING` (likely interrupted mid-workflow) vs. `QUEUED` (never got dequeued)? A `PROCESSING` row may have side effects already (CV composed, PDF generated) — re-running could double-bill the LLM. Cheapest mitigation: if `current_pdf_path` is set on a `PROCESSING` row, dispatch the retry workflow instead of preparation. Worth a separate ticket if it shows up in production.

## References

- `src/services/jobs/job_queue.py` — `JobQueue`, `process_queue`, `ConsumerManager`, `_should_retry_scrape`.
- `src/services/jobs/job_orchestrator.py` — `JobOrchestrator.submit_job` (now dispatches via `ctx.workflow_dispatcher`).
- `src/services/jobs/scheduler.py` — `Scheduler.run_search` (currently uses `queue.put_batch`; needs the per-job create-then-put refactor).
- `src/agents/dispatcher.py` — `WorkflowDispatcher.dispatch_preparation` / `dispatch_retry`, `_mark_preparation_failed`.
- `src/agents/preparation_workflow.py` — `extract_job_node`, `save_to_db_node`, `save_filtered_out_node`, `save_scrape_failed_node`.
- `src/models/state_machine.py` — `BusinessState`, `WorkflowStep`, `ALLOWED_TRANSITIONS` (recovery transitions already in place).
- `src/models/unified.py` — `JobRecord`, `PendingApproval`.
- `src/services/db/repository.py` — abstract `JobRepository`, `UPDATABLE_FIELDS`.
- `src/services/db/job_repository.py` — `InMemoryJobRepository`, `SQLiteJobRepository`.
- `src/services/db/tables.py` — Piccolo ORM `Job` table.
- `src/services/db/migrations.py` — in-process `MIGRATIONS` tuple (the migration pattern this plan uses).
- `src/api/routes/hitl.py`, `src/api/routes/system.py` — endpoints that need the new `states` filter and health counts.
- `docs/plans/scrape-failure-recovery.md` — precedent for adding retry/recovery columns + the same migration pattern this plan reuses.
- `docs/plans/multi-user-support.md` — precedent for per-user scoping in repository methods.
