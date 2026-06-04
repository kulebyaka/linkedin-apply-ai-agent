# Feature Specification: "Proceed Anyway" for Filtered-Out Jobs

## Overview
- **Feature**: Proceed Anyway button on the applications page
- **Status**: Draft
- **Created**: 2026-06-04
- **Author**: User + Claude Code

## Problem Statement

The LLM job filter automatically rejects LinkedIn jobs that score below the reject
threshold (or hit a hard disqualifier), saving them with `status = filtered_out` and
**skipping CV composition and PDF generation entirely**. This is a terminal state — the
user has no way to override the filter's decision.

In practice the filter is not infallible: it can misjudge a job, flag a "hidden
disqualifier" that the user is willing to accept, or score a borderline-good role just
under the threshold. Today the only signal the user gets is a muted gray `filtered_out`
badge with a tooltip explaining the rejection. They cannot act on a disagreement.

This feature adds a **"Proceed Anyway"** action that lets the user override the filter
for a single job, pushing it through the CV-generation pipeline it originally skipped so
it lands in the normal HITL review queue.

## Goals & Success Criteria

- Let a user override the filter on any individual `filtered_out` job from the
  applications page.
- Re-run only the part of the preparation pipeline that was skipped (CV composition →
  PDF generation), without re-scraping or re-filtering the job.
- Land the job in `pending_review` so it appears in the HITL review queue like any other
  generated CV.
- Preserve the original `filter_result` so the reviewer sees why it was filtered and that
  it was proceeded anyway.
- **Success Metrics**: A user can click "Proceed Anyway" on a filtered-out job, confirm,
  and within the normal CV-generation time see that job move to `pending_review` with a
  generated CV/PDF available for review.

## User Stories

1. As a job seeker, when the filter rejects a job I actually want to apply to, I want to
   override it with one click so a tailored CV is generated and I can review it.
2. As a job seeker, I want to see *why* the job was filtered before I override it, so I
   make an informed decision rather than a misclick.
3. As a reviewer in the HITL queue, I want to still see the original filter verdict on a
   proceeded job, so I know it was a deliberate override.

## Functional Requirements

### Core Capabilities

- A **"Proceed Anyway"** button appears in the action cell of each `filtered_out` row on
  the applications table.
- Clicking it opens a **confirmation dialog** summarizing the filter verdict (score,
  disqualifier reason, red flags) before any work happens.
- On confirm, the backend transitions the job `filtered_out → processing` and dispatches
  the preparation workflow in a mode that **skips extraction and filtering** and resumes
  at CV composition using the already-stored `job_posting`.
- The job's `filter_result` is **preserved** throughout (not cleared).
- On success the job lands in **`pending_review`** (HITL queue) — `full` mode is forced
  regardless of the job's originally stored `mode`.
- On any failure during composition/PDF, the job lands in **`failed`** with
  `error_message` set (same as a normal preparation-workflow failure).

### User Flows

**Happy path**
1. User is on `/applications`. A LinkedIn job shows the `filtered_out` badge with its
   reason tooltip.
2. The row's action cell shows **Proceed Anyway** (alongside the existing Open on
   LinkedIn / Delete actions).
3. User clicks it → a confirmation dialog appears showing: job title/company, filter
   score, disqualifier reason (if any), red flags, and reasoning.
4. User confirms. The frontend `POST`s to the proceed endpoint. The button enters a
   loading/disabled state.
5. Backend validates ownership + `status == filtered_out`, transitions to `processing`,
   and dispatches the preparation workflow (skip-filter mode).
6. The row's status updates to `processing` (via the existing polling/refresh on the
   applications page).
7. Workflow composes the CV, generates the PDF, and saves the record as `pending_review`.
8. The job now appears in the HITL review queue; `filter_result` is still attached.

**Failure path**
- If the user is not the owner, or the job is not in `filtered_out`, the endpoint returns
  a 4xx and the UI shows an error (no state change).
- If CV composition or PDF generation throws, the workflow marks the job `failed` with
  `error_message`; the row reflects the failed state.

**Cancel path**
- User dismisses the confirmation dialog → no request is sent, no state change.

### Data Model

No schema changes to `JobRecord`. A `filtered_out` record already carries everything the
pipeline needs:

| Field | filtered_out value | Used by Proceed Anyway |
|-------|--------------------|------------------------|
| `job_posting` | populated (title, company, description, …) | input to `compose_cv` |
| `raw_input` | populated | retained |
| `filter_result` | populated (score, disqualified, reason, red_flags, reasoning) | **preserved**, shown in confirm dialog + HITL |
| `current_cv_json` | `None` | generated by this flow |
| `current_pdf_path` | `None` | generated by this flow |
| `source` / `mode` / `user_id` | populated | ownership + dispatch |

**State machine change** (`src/models/state_machine.py`):
```python
# Before:  BusinessState.FILTERED_OUT: set()          # terminal
# After:   BusinessState.FILTERED_OUT: {BusinessState.PROCESSING}
```
This removes `filtered_out` from `TERMINAL_STATES` (it is derived from empty transition
sets). Confirm nothing else relies on `filtered_out` being terminal (search usages).

**Workflow state flag** (`PreparationWorkflowState` in
`src/agents/preparation_workflow.py`): add an optional `skip_filter: bool` key (default
falsy). When set, the workflow bypasses extraction-rescrape and filtering.

### Integration Points

- **Applications table** (`ui/src/lib/components/applications/ApplicationsTable.svelte`)
  — new action button + confirmation dialog; reuses the existing `filterResult(j)` helper
  that already powers the tooltip.
- **Applications page** (`ui/src/routes/applications/+page.svelte`) — wires the new API
  call and triggers a refresh after success.
- **Frontend jobs API** (`ui/src/lib/api/jobs.ts`) — new `proceedAnyway(jobId)` function.
- **Preparation workflow** (`src/agents/preparation_workflow.py`) — `route_after_extract`
  and `extract_job_node` honor `skip_filter`; reuses existing `compose_cv_node` →
  `generate_pdf_node` → `save_to_db_node` chain.
- **JobOrchestrator** (`src/services/jobs/job_orchestrator.py`) — new `proceed_filtered_out`
  method following the `_handle_retry` dispatch pattern.
- **API** (`src/api/main.py` or `src/api/routes/`) — new `POST /api/jobs/{job_id}/proceed`
  endpoint.

## Technical Design

### Architecture

A **dedicated endpoint + workflow re-dispatch**, mirroring the existing retry pattern in
`HITLProcessor._handle_retry`. The retry flow is the closest precedent: it loads the
master CV from the user record, builds a workflow `initial_state` dict, transitions the
job status, and dispatches a workflow on a background task with rollback on dispatch
failure.

The key difference from retry: the preparation workflow always begins at `extract_job`.
To avoid re-scraping and re-filtering, we pass `skip_filter=True` plus the already-stored
`job_posting` in the initial state, and:

- `extract_job_node` short-circuits when `skip_filter` is set and `job_posting` is already
  present — it passes the stored posting through instead of re-scraping. It already flips
  the persisted row to `PROCESSING` for the `(QUEUED, PROCESSING, SCRAPE_FAILED)` status
  set; the endpoint will have set `PROCESSING` first, so the existing self-transition
  guard is satisfied (no need to add `FILTERED_OUT` to that set).
- `route_after_extract` returns `"compose"` when `skip_filter` is set (bypassing
  `filter_job` even for LinkedIn-sourced jobs).
- From there the normal `compose_cv → generate_pdf → save_to_db` chain runs. `save_to_db`
  must persist `pending_review` (force `full` mode) and **must not** overwrite
  `filter_result`.

### Endpoint

```
POST /api/jobs/{job_id}/proceed
  auth: CurrentUser (user-scoped, ownership enforced)
  preconditions: job exists, owned by user, status == filtered_out
  effect:
    - status: filtered_out → processing
    - dispatch preparation workflow with {skip_filter: True, job_posting, master_cv, mode: "full"}
  response: { job_id, status: "processing", message }
  errors:
    - 404 if job not found / not owned
    - 409 if status != filtered_out
```

### Orchestrator method (sketch)

```python
# src/services/jobs/job_orchestrator.py
async def proceed_filtered_out(self, job_id: str, user_id: str) -> JobSubmitResponse:
    job = await self._ctx.repository.get_for_user(job_id, user_id)   # 404 if None
    if job.status != BusinessState.FILTERED_OUT:
        raise <conflict 409>
    await self._ctx.repository.update(job_id, {"status": BusinessState.PROCESSING})
    # load master_cv + cv model prefs from user record (same as _handle_retry)
    initial_state = {
        "job_id": job_id, "user_id": user_id,
        "source": job.source, "mode": "full",
        "job_posting": job.job_posting,
        "raw_input": job.raw_input or {},
        "filter_result": job.filter_result,   # carried through; not re-evaluated
        "master_cv": master_cv,
        "skip_filter": True,
        "current_step": BusinessState.QUEUED, "error_message": None,
    }
    # dispatch in background with rollback-to-filtered_out on dispatch failure
    self._ctx.create_background_task(
        dispatcher.dispatch_preparation(job_id=job_id, thread_id=str(uuid4()),
                                        initial_state=initial_state, user_id=user_id))
    return JobSubmitResponse(job_id=job_id, status=BusinessState.PROCESSING)
```

> Note on dispatch-failure rollback: `_handle_retry` rolls back to `pending_review`.
> Here, if the *dispatch* itself fails, roll back to `filtered_out` (its prior state) so
> the button reappears. If the *workflow* runs but composition/PDF fails, the workflow
> itself sets `failed` (per the failure-handling decision).

### Frontend

```ts
// ui/src/lib/api/jobs.ts
export async function proceedAnyway(jobId: string): Promise<{ job_id: string; status: string }> {
  const res = await fetch(`${API_BASE}/api/jobs/${jobId}/proceed`, {
    method: 'POST', credentials: 'include',
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
```

`ApplicationsTable.svelte`: in the action cell, when `j.status === 'filtered_out'`, render
a **Proceed Anyway** button. On click, open a confirmation dialog populated from
`filterResult(j)` (score / disqualifier_reason / red_flags / reasoning). On confirm, call
`proceedAnyway(j.job_id)`, disable the button, and emit an event so the page refreshes the
list.

### Technology Stack
- **Frameworks**: FastAPI (endpoint), LangGraph (preparation workflow), Svelte 5 (UI)
- **Libraries**: Pydantic v2 (models), existing WeasyPrint/Jinja2 PDF pipeline
- **Tools**: existing repository (in-memory / SQLite via Piccolo)

### Data Persistence
No new tables or columns. Reuses the existing `JobRecord` persistence; the only change is
allowing the `filtered_out → processing` transition and ensuring `save_to_db` preserves
`filter_result`.

## Non-Functional Requirements

- **Performance**: Skipping re-scrape and re-filter means the proceed path costs roughly
  one CV composition + one PDF render — comparable to a retry, cheaper than a fresh submit.
- **Security**: Endpoint is user-scoped via `get_for_user`; a user can only proceed their
  own jobs. No admin path required.
- **Observability**: Log the override at INFO (`job %s proceeded past filter (score=%s)`),
  and the existing workflow timing logs cover the rest.
- **Error Handling**:
  - Invalid state → 409, no mutation.
  - Not owned / missing → 404.
  - Dispatch failure → rollback to `filtered_out`, surface error.
  - Composition/PDF failure → `failed` with `error_message` (handled by the workflow).

## Implementation Considerations

### Design Trade-offs

- **Dedicated endpoint vs. extending HITL `decide`** → chose a dedicated
  `POST /api/jobs/{job_id}/proceed`. The HITL `decide` endpoint validates `PENDING` status
  and centers on approve/decline/retry of an already-generated CV; folding a
  `filtered_out`-only action into it muddies that contract.
- **Reusing the preparation workflow with `skip_filter` vs. a new workflow** → reuse. A new
  workflow would duplicate `compose_cv/generate_pdf/save_to_db`. A single boolean flag on
  the existing state + two small routing/guard tweaks is far less surface area.
- **End state `pending_review` vs. respecting original `mode`** → force `pending_review`.
  An override of the filter is exactly the case where human review is most warranted, so
  it always enters the HITL queue regardless of the job's stored mode.
- **Preserve `filter_result` vs. clear it** → preserve, so the reviewer sees the original
  rejection context and that it was a deliberate override.

### Dependencies
None external. All touch points are existing modules.

### Testing Strategy

- **Unit (state machine)**: `filtered_out → processing` is now allowed; `processing →
  filtered_out` (dispatch rollback) is allowed or handled; illegal transitions still raise.
- **Unit (orchestrator)**: `proceed_filtered_out` 404s on non-owned/missing, 409s on
  non-`filtered_out`, dispatches with `skip_filter=True` and `mode="full"` on success,
  rolls back to `filtered_out` on dispatch failure.
- **Unit (workflow routing)**: `route_after_extract` returns `"compose"` when
  `skip_filter` is set even for `source == "linkedin"`; `extract_job_node` passes through
  the stored `job_posting` without re-scraping; `save_to_db` preserves `filter_result` and
  writes `pending_review`.
- **API (TestClient)**: `POST /api/jobs/{job_id}/proceed` returns 200/`processing` on a
  filtered_out job, 409 on a non-filtered job, 401 unauth, 404 for another user's job.
- **E2E (optional, Playwright)**: filtered_out row → Proceed Anyway → confirm dialog →
  job moves out of filtered_out. Aligns with `tests/e2e/test_hitl_review.py` patterns.

## Out of Scope

- Bulk "proceed anyway" for multiple filtered-out jobs at once.
- Re-running the filter or adjusting filter thresholds (this is a per-job override only).
- Surfacing the action anywhere other than the applications-table row (e.g. not inside the
  tooltip, admin page, or a job detail view).
- Auto-applying or any change to the (stubbed) application workflow.
- Feeding the override back into the filter as a learning signal.

## Open Questions

- Should there be a guard against proceeding a job that hit a *hard disqualifier*
  (`disqualified == true`) vs. merely a low score, or is the confirmation dialog
  sufficient for both? (Current spec: confirmation covers both.)
- Does the applications page poll often enough to reflect the `processing → pending_review`
  transition promptly, or should the proceed action trigger an immediate optimistic
  refresh? (Current spec: reuse existing refresh.)

## References
- `src/models/state_machine.py` — `ALLOWED_TRANSITIONS`, `TERMINAL_STATES`
- `src/agents/preparation_workflow.py` — `create_preparation_workflow`, `route_after_extract`,
  `route_after_filter`, `extract_job_node`, `save_filtered_out_node`
- `src/services/jobs/hitl_processor.py:227` — `_handle_retry` (dispatch pattern precedent)
- `src/services/jobs/job_orchestrator.py` — `submit_job` dispatch
- `src/models/unified.py` — `JobRecord`
- `ui/src/lib/components/applications/ApplicationsTable.svelte` — status badge + filter tooltip
- `ui/src/routes/applications/+page.svelte`, `ui/src/lib/api/jobs.ts`
- Commit `246e968` — "show filter-out reason tooltip and Download CV on applications page"
