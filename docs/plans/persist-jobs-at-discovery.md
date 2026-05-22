# Feature Specification: Persist Jobs at Discovery

## Overview
- **Feature**: Save scraped/submitted jobs to the database the moment they are discovered, not at the end of CV generation.
- **Status**: Draft
- **Created**: 2026-05-19
- **Author**: User + Claude Code

## Problem Statement

Today a `JobRecord` is only written to the repository at the very end of `preparation_workflow.save_to_db_node`, after extraction → filtering → CV composition → PDF generation have all succeeded. Two consequences:

1. **No transparency during processing.** The UI cannot show that a job has been picked up by the scraper or that CV composition is in flight — the job is invisible until the entire pipeline finishes (typically tens of seconds of LLM + PDF work per job).
2. **Queue loss on restart.** The pipeline relies on an in-process `asyncio.Queue` (`src/services/jobs/job_queue.py`). When the VPS deploy restarts the container, every job that the scraper had enqueued but the consumer had not yet finished is silently dropped. There is no on-disk record of those discoveries, so they are gone until the next scheduled scrape happens to find them again — and may not, since LinkedIn results rotate.

The goal is to persist a `JobRecord` at the earliest possible moment, advance its status as the workflow progresses, and recover any in-flight rows on startup.

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
- The record's `status` advances through `QUEUED → PROCESSING → (CV_READY | PENDING_REVIEW | FILTERED_OUT | SCRAPE_FAILED | FAILED)` as the workflow runs, reflecting the actual lifecycle.
- On application startup, jobs found in non-terminal states are re-enqueued so the consumer can pick them up. A bounded `recovery_attempts` counter prevents poison rows from looping forever.
- The HITL/list API surfaces in-flight rows (queued, processing, etc.) when the caller opts in via a `states` filter.
- The UI shows in-flight jobs as read-only cards with a status badge + spinner; review actions only appear once `status=PENDING_REVIEW`.

### User Flows

#### Flow A — LinkedIn scrape (scheduler tick)

1. `LinkedInScraper.scrape()` returns a `list[ScrapedJob]` for a user.
2. For each `ScrapedJob`, the scheduler computes `scoped_job_id = f"{job_id}:{user_id}"` and calls `job_repository.get(scoped_job_id)`.
3. If a record exists and is not retry-eligible → skip.
4. Otherwise insert a new `JobRecord(status=QUEUED, source="linkedin", mode="full", job_posting={preview}, raw_input={full_scraped})` and **then** enqueue a `QueueItem` so the consumer can pick it up.
5. Consumer dequeues, calls `repo.update(status=PROCESSING)`, runs `preparation_workflow.ainvoke(...)`.
6. Each workflow node updates `current_step` (already does today). Terminal nodes call `repo.update(status=TERMINAL_STATE)` instead of `repo.create(...)`.

#### Flow B — Manual submission (POST /api/jobs/submit)

1. `JobOrchestrator.submit_job` validates the request, generates `job_id`.
2. Before dispatching the workflow, it `repo.create(JobRecord(status=QUEUED, job_posting={preview from raw_input}, raw_input={...}))`.
3. Returns `JobSubmitResponse(job_id, status=QUEUED)` to the caller.
4. Background task runs the workflow, which `repo.update(status=PROCESSING)` first and then updates as nodes complete.

#### Flow C — Startup recovery

1. App lifespan startup, after `AppContext` is created and `prep_workflow` is compiled, run `recover_in_flight_jobs(ctx)`.
2. Query `repo.list_by_states([QUEUED, PROCESSING, RETRYING])`.
3. For each row: increment `recovery_attempts`. If `recovery_attempts > MAX_RECOVERY_ATTEMPTS` (e.g. 3) → `repo.update(status=FAILED, error_message="restart loop guard")`. Else → reconstruct a `QueueItem`/initial state from `raw_input` and re-enqueue (LinkedIn rows go to `JobQueue`; manual rows are dispatched directly via `_run_preparation_workflow`).
4. Emit `logger.info("Recovered %d jobs on startup (%d gave up)", recovered_count, exhausted_count)`.

#### Flow D — UI viewing an in-flight job

1. User opens dashboard. Front-end calls `GET /api/hitl/pending?states=queued,processing,pending_review`.
2. Cards rendered:
   - `pending_review` → existing Tinder card with approve/decline/retry.
   - `queued | processing | retrying` → read-only card with company/title/url, badge for current state (mapped from `current_step` for fine-grained labels: "Scraping", "Filtering", "Composing CV", "Generating PDF"), no action buttons.
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

`ALLOWED_TRANSITIONS` is unchanged except a small clarification: rows can re-enter `QUEUED` from `PROCESSING` during recovery. Add `BusinessState.QUEUED` to `ALLOWED_TRANSITIONS[BusinessState.PROCESSING]` so the recovery path can flip a stuck `PROCESSING` row back to `QUEUED` for requeue.

#### Repository (`src/services/db/job_repository.py`)

New method on the abstract interface and both implementations:

```python
async def list_by_states(self, states: list[BusinessState], *, user_id: str | None = None) -> list[JobRecord]:
    """Return all jobs whose status is in `states`, optionally scoped to a user."""
```

The piccolo SQLite implementation uses `where(JobTable.status.is_in(...))` plus optional user filter. The in-memory implementation iterates the dict.

A new piccolo migration adds the `recovery_attempts` (int, default 0) and `last_recovery_attempt_at` (timestamptz, nullable) columns to `JobTable`.

### Integration Points

| Touchpoint | Change |
|------------|--------|
| `LinkedInScraper`/scheduler enqueue site (where it currently calls `JobQueue.put_batch`) | Persist `JobRecord(QUEUED)` first, then enqueue. Dedup check moves here from the consumer (or is duplicated — see Design Trade-offs). |
| `process_queue` (`src/services/jobs/job_queue.py`) | Dequeue → `repo.update(status=PROCESSING)` → `workflow.ainvoke`. Remove the after-the-fact `repo.create(FAILED)` path; row already exists. Keep the `_should_retry_scrape` logic. |
| `JobOrchestrator.submit_job` | Insert `JobRecord(QUEUED)` synchronously before scheduling the background workflow task. Update `_run_preparation_workflow`'s exception handler to only call `repo.update`, not `repo.create`. |
| `preparation_workflow.save_to_db_node` | Becomes `repo.update`-only. The `if existing is None: create else: update` branch is deleted. |
| `preparation_workflow.save_filtered_out_node` | Same: `repo.update`-only. |
| `preparation_workflow.save_scrape_failed_node` | Same: `repo.update`-only. The scrape-attempts increment logic stays. |
| `extract_job_node` | First step: `repo.update(status=PROCESSING)` if the row is still `QUEUED`. (Idempotent thanks to self-transition rule.) |
| `AppContext` lifespan startup | Call new `recover_in_flight_jobs(ctx)` after queue and workflows are wired. |
| `/api/hitl/pending` | Accept optional `states: str` query param (comma-separated). Default unchanged (`pending`). When `?states=queued,processing,pending` is passed, return all matching rows. |
| `/api/health` | Add `queued_count`, `processing_count` fields (cheap repo count query). |
| UI `/+page.svelte` | Request the broader state list; render read-only "in-flight" cards for non-terminal items. |
| UI `lib/api/hitl.ts` | `fetchPending(states?: BusinessState[])` accepts an optional state list. |

## Technical Design

### Architecture

The chosen insertion point is **at the scraper/submitter boundary, just before the in-memory queue or workflow dispatch**. Rationale:

- It is the earliest moment we have a `job_id` and enough raw data to render a useful row.
- A crash between scrape and enqueue is then also covered: the row exists; the recovery pass will requeue it.
- Both LinkedIn-pipeline and manual-submit flows converge on the same primitive: "create the row, then schedule the work." This simplifies status queries (`JobOrchestrator.get_status` no longer needs the workflow-thread fallback for early states — the repo is always authoritative).

Workflow nodes become pure mutators: they assume the row exists and call `repo.update(...)`. This kills the `if existing is None: create else: update` branching that pervades the three save_* nodes today.

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
- Default behavior unchanged: returns `pending_review` only.
- Validates each token against `BusinessState`; rejects unknown tokens with 400.
- Response items extended with a `current_step` field (already on `JobRecord`) so the UI can render fine-grained badges ("Composing CV", etc.) for `PROCESSING` rows.

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

    # NEW: persist QUEUED row before dispatching workflow
    preview = self._build_job_posting_preview(request)
    await self._ctx.repository.create(JobRecord(
        job_id=job_id,
        user_id=user_id,
        source=request.source,
        mode=request.mode,
        status=BusinessState.QUEUED,
        job_posting=preview,
        raw_input=raw_input,
        created_at=now(),
        updated_at=now(),
    ))

    # ... existing dispatch ...
```

#### Modified: scheduler enqueue site

```python
for scraped_job in scraped_jobs:
    scoped_id = _scoped_job_id(scraped_job.job_id, user_id)
    existing = await ctx.repository.get(scoped_id)
    if existing is not None and not _should_retry_scrape(existing):
        continue
    if existing is None:
        await ctx.repository.create(JobRecord(
            job_id=scoped_id,
            user_id=user_id,
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
            created_at=now(),
            updated_at=now(),
        ))
    await ctx.job_queue.put(scraped_job, user_id=user_id)
```

The existing dedup branch in `process_queue` can be simplified/removed because the create-before-enqueue contract makes dedup at insert time the source of truth. (Keeping a defensive re-check in `process_queue` is acceptable — see Design Trade-offs.)

## Non-Functional Requirements

- **Performance**: One extra DB INSERT per scraped job (currently ~10–50 per scheduler tick per user). Negligible vs. the LLM call. The recovery scan on startup is a single `SELECT WHERE status IN (...)` — bounded by the number of in-flight jobs.
- **Security**: All new queries remain user-scoped via the existing `user_id` filter in `list_by_states`.
- **Observability**:
  - Counter `jobs_discovered_total{source=linkedin|manual}` — incremented on each new `create()` from scraper/submitter.
  - Histogram `time_in_state_seconds{state=...}` — emitted when a row transitions out of each state; uses `updated_at - prev_updated_at` or a per-state timer in the workflow.
  - Single startup log line: `Recovered N jobs on startup (M exhausted, K skipped)`.
  - `/api/health` extended with `queued_count`, `processing_count`.
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

- Piccolo migration to add `recovery_attempts INT NOT NULL DEFAULT 0` and `last_recovery_attempt_at TIMESTAMPTZ NULL` to the `JobTable`.
- No new third-party packages.
- VPS deploy must run migrations on container start (already configured per `scrape-failure-recovery.md` pattern).

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

- Should `current_step` be persisted on `JobRecord` (so the UI can read fine-grained progress for `PROCESSING` rows even after a restart) or stay workflow-only? Leaning toward persisting it on each node transition; light-weight UPDATE.
- For LinkedIn jobs that get persisted at discovery but then permanently fail extraction (LinkedIn page gone), do we want a separate `EXPIRED` terminal state, or is `SCRAPE_FAILED → FAILED` sufficient? Probably sufficient.
- The `recovery_attempts` cap of 3 is a guess; real value should be tuned after we see how often deploys catch jobs mid-flight in production.

## References

- `src/services/jobs/job_queue.py` — `JobQueue`, `process_queue`, `ConsumerManager`.
- `src/services/jobs/job_orchestrator.py` — `JobOrchestrator.submit_job`, `_run_preparation_workflow`.
- `src/agents/preparation_workflow.py` — `save_to_db_node`, `save_filtered_out_node`, `save_scrape_failed_node`.
- `src/models/state_machine.py` — `BusinessState`, `ALLOWED_TRANSITIONS`.
- `src/models/unified.py` — `JobRecord`.
- `docs/plans/scrape-failure-recovery.md` — precedent for adding retry/recovery columns + migration pattern.
- `docs/plans/multi-user-support.md` — precedent for per-user scoping in repository methods.
