# Agents & Workflow Nodes

The LangGraph nodes that make up the pipeline, as implemented. Requirements and the wider
architecture live in `implementation-plan.md`; LLM-layer internals in `src/llm/CLAUDE.md`.

Two graphs exist: **preparation** (`src/agents/preparation_workflow.py`) and **retry**
(`src/agents/retry_workflow.py`). Both are compiled with a `MemorySaver` checkpointer and invoked
through `WorkflowDispatcher` (`src/agents/dispatcher.py`), never directly.

There is **no application/apply graph.** Discovery (LinkedIn search + scrape) is also not a graph
node — it runs in `src/services/jobs/scheduler.py` and feeds an `asyncio` queue.

---

## Conventions that apply to every node

- **All nodes are `async def`** and take `(state, config=None)`. No `asyncio.run()` anywhere inside a
  graph.
- **Repositories arrive through `config["configurable"]`** (`repository`, `user_repository`), not via
  imports or globals. `_shared.get_repository_from_config` / `get_user_repository_from_config` read
  them.
- **`current_step` and `target_status` are different fields with different enums.** `current_step` is
  a transient `WorkflowStep`; business outcomes go into `target_status` as a `BusinessState`, and the
  routing functions read *that*. Writing a `BusinessState` into `current_step` mixed the two enums
  and was the historical source of routing bugs — don't reintroduce it.
- **Nodes don't raise to signal business outcomes.** They set `target_status` / `error_message` and
  let a routing function decide. Genuine exceptions propagate to the dispatcher, which persists
  `failed` if — and only if — `ALLOWED_TRANSITIONS` allows it from the current state.
- **Step progress is persisted as it happens** (`_persist_workflow_step`), so the UI can show
  `composing_cv` on an in-flight job rather than a bare `processing`.

---

## Preparation graph

```
extract_job ──┬─(scrape_failed)──► save_scrape_failed ──► END
              ├─(error)─────────────────────────────────► END
              ├─(filter)────────► filter_job ──┬─(filtered_out)─► save_filtered_out ──► END
              │                                └─(compose)──┐
              └─(compose)─────────────────────────────────► compose_cv ──► generate_pdf ──► save_to_db ──► END
```

State: `PreparationWorkflowState` (TypedDict) — inputs `job_id`, `user_id`, `source`, `mode`,
`raw_input`; working fields `job_posting`, `master_cv`, `tailored_cv_json`, `tailored_cv_pdf_path`,
`filter_result`, `skip_filter`, `user_feedback`, `retry_count`; status fields `current_step`,
`target_status`, `error_message`.

### `extract_job`

Normalizes raw input into a `job_posting` dict via the source adapter
(`JobSourceFactory.get_adapter`): fetch-and-parse for `url`, pass-through for `manual`, field mapping
for an already-scraped `linkedin` job.

Non-obvious:
- **Its first action is a status write**, not extraction: `queued → processing` (plus
  `workflow_step`), so the UI shows work has started. Self-transitions are legal, which makes
  re-entry after startup recovery a no-op instead of an error. The write is best-effort — a failure
  logs and continues, because the terminal state still comes out right.
- It enforces the **scrape quality gate** for every non-`manual` source: a description shorter than
  `SCRAPER_MIN_DESCRIPTION_CHARS` sets `target_status = SCRAPE_FAILED` rather than composing a CV
  from nothing. Manual input is user-supplied and exempt.
- On a `skip_filter` run ("Proceed Anyway"), it returns immediately with the *stored* `job_posting`
  — no re-scrape, and the quality gate is skipped because it already passed on the original run.
- A `NotImplementedError` from an adapter is caught and turned into a `failed` record with a message
  telling the user to use `source="manual"`, rather than surfacing a stack trace.

### `route_after_extract` (conditional edge)

In order: `SCRAPE_FAILED` → `save_scrape_failed`; any `error_message` → `END`; `skip_filter` →
`compose_cv`; `source == "linkedin"` → `filter_job`; otherwise → `compose_cv`.

**Filtering only applies to LinkedIn jobs.** A manually submitted job description is never filtered —
the user asked for it explicitly.

### `filter_job`

LLM evaluation producing a `FilterResult`: `score` (0–100), `red_flags`, `disqualified`,
`disqualifier_reason`, `reasoning`. Model choice honours the user's `job_filtering` preference.

Non-obvious:
- Loads the user's `UserFilterPreferences` and respects the **per-user kill switch** (`enabled`),
  passing the job straight through when off.
- Thresholds come from the user's preferences, falling back to `JOB_FILTER_REJECT_THRESHOLD` /
  `JOB_FILTER_WARNING_THRESHOLD`.
- Two thresholds, one node: below reject (or `disqualified`) sets `target_status = FILTERED_OUT`;
  below warning still passes but the score and red flags ride along in `filter_result` so the review
  UI can badge it.
- **The filter fails open.** If evaluation raises — bad API key, provider outage, malformed output
  past Instructor's retries — the exception is logged and the job *passes through* to CV composition.
  A broken filter degrades quality, it never blocks the pipeline.
- `JobFilter.evaluate_job` is synchronous and is offloaded with `asyncio.to_thread`, so it doesn't
  stall the event loop while the queue consumer is running.
- LLM responses can be cached/replayed via `job_fixtures` for deterministic runs.

### `route_after_filter` (conditional edge)

`target_status == FILTERED_OUT` → `save_filtered_out`; otherwise → `compose_cv`.

### `save_filtered_out`

Persists a minimal `JobRecord` with status `filtered_out`, keeping `filter_result` so the user can
see *why*. Terminal — the graph ends here, and no CV is generated. `filtered_out` is not a dead end
at the domain level: "Proceed Anyway" transitions it back to `processing`.

### `save_scrape_failed`

Persists status `scrape_failed`, bumps `scrape_attempts`, and records `last_scrape_error` /
`last_scrape_attempt_at`. Terminal for this run; re-attempt eligibility is decided later by
`_should_retry_scrape` (attempt cap + backoff window).

### `compose_cv`

`CVComposer` tailors `master_cv` to `job_posting` and produces `tailored_cv_json`. Model choice
honours the user's `cv_generation` preference. Any `user_feedback` in state is folded into the
prompt (this is how the retry graph reuses this node's logic). Hallucination checking is delegated to
`CVValidator` under the configured `HallucinationPolicy` (`strict` raises, `warn` logs, `disabled`
skips). Each composition is recorded as a `CVCompositionAttempt` for retry history.

### `generate_pdf`

`PDFGenerator` renders `tailored_cv_json` through a Jinja2 template with WeasyPrint, writing to
`data/generated_cvs/{user_id}/{job_id}.pdf`. Templates: `modern`, `compact`, `profile-card`.

### `save_to_db`

Persists the record and picks the terminal status from `mode`: `mvp` → `completed` (PDF ready to
download, no review), `full` → `pending` (enters the HITL queue).

---

## Retry graph

```
load_from_db ──► compose_cv ──► generate_pdf ──► update_db ──► END
```

State: `RetryWorkflowState` — inputs `job_id`, `user_id`, `user_feedback`; loaded `job_posting`,
`master_cv`, `retry_count`; outputs `tailored_cv_json`, `tailored_cv_pdf_path`; optional per-user
`llm_provider` / `llm_model` overrides.

| Node | Behaviour |
|---|---|
| `load_from_db` | Loads the existing record — the job is **not** re-extracted or re-filtered |
| `compose_cv` | Re-composes with `user_feedback` folded into the prompt |
| `generate_pdf` | Regenerates the PDF |
| `update_db` | Increments `retry_count` and returns the record to `pending` — back into the review queue |

The graph is linear and has no filter step: a retry means the user already accepted the job, just not
the CV.

---

## The HITL step (not a graph node)

Human review is **not** a LangGraph interrupt. The preparation graph ends at `pending`, the user
reviews a batch in the UI, and `HITLProcessor` (`src/services/jobs/hitl_processor.py`) turns each
decision into either a state write or a new graph invocation:

| Decision | Effect |
|---|---|
| `approved` | Writes `approved` and stops. **Nothing is submitted** — the user applies manually via the LinkedIn link. The API response says so explicitly |
| `declined` | Writes `declined`; the optional note is stored as `decline_reason` and becomes a refinement signal |
| `retry` | Dispatches the retry graph with the note as `user_feedback` |

Decisions are serialized per job by an in-process lock, so a double-click can't race two workflows
onto the same record.

---

## Services that behave like agents but aren't nodes

| Service | File | Role |
|---|---|---|
| Discovery / scheduler | `src/services/jobs/scheduler.py` | Per-user LinkedIn search on an interval; enqueues new jobs, persists them as `queued` at discovery |
| Queue consumer | `src/services/jobs/job_queue.py` | Single `ConsumerManager` task draining the queue into the preparation graph; decides scrape-retry eligibility |
| Startup recovery | `src/services/jobs/recovery.py` | Re-enqueues or re-dispatches jobs left mid-flight by a previous process, bounded by `recovery_attempts` |
| Filter refiner | `src/services/jobs/refinement.py` | One LLM call per opted-in user turning decline/override signals into a *proposed* filter-prompt change plus a notification. Never self-applies |
| Model catalog refresh | `src/services/jobs/model_catalog_scheduler.py` | Daily refresh of the model list and pricing |
| Admin alerts | `src/services/alerts.py` | Emails the admin when a scrape batch looks unauthenticated |

## Notification, apply, and fetch "agents"

Earlier drafts of this document described `fetch_jobs`, `human_review`, `apply_linkedin`, and
`send_notification` nodes. None of them exist as graph nodes:

- fetching is the scheduler service (above),
- human review is an out-of-band UI + `HITLProcessor`,
- applying is **unbuilt**,
- notification is the persistent notification centre (`src/services/notifications/`) plus
  `AdminAlertService` — there is no per-job failure webhook.
