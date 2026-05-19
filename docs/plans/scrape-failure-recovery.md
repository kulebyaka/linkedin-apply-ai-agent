# Scrape Failure Recovery

## Problem

The cross-cycle dedup in `src/services/jobs/job_queue.py:179` skips any LinkedIn posting whose user-scoped `JobRecord` already exists, regardless of state. When the scraper misfires (e.g. LinkedIn changes the detail-page HTML), broken records get persisted with empty/short descriptions and then become permanently locked out of re-processing — even after the scraper is fixed. The fix shipped in `e35ad2a` corrected the selectors, but the corpus of empty-description rows captured before the fix will never re-enter the pipeline.

The same failure mode will recur the next time LinkedIn changes its layout. We need a self-healing pipeline plus a manual escape hatch.

## Goals

1. Future schema breaks **auto-heal** once the scraper fix ships: broken records get re-scraped on the next scheduler tick without manual intervention.
2. Bounded retry: a permanently-broken posting must not loop forever.
3. **Manual delete button** on every Preview-page card as a release valve, with cascade cleanup (DB row + CV attempts + PDF file).
4. No regression on the four existing dedup layers for successfully-scraped jobs.

## Non-Goals

- Re-scraping arbitrarily on demand (manual delete + scheduler re-encounter is sufficient).
- UI to bulk-recover broken rows (deletion via the per-card button is enough for the current corpus; a one-shot migration handles the existing backlog).
- Changing the semantics of `failed` (workflow exceptions stay sticky).

## Design

### 1. New business state: `SCRAPE_FAILED`

Add to `src/models/state_machine.py`:

```python
class BusinessState(StrEnum):
    ...
    SCRAPE_FAILED = "scrape_failed"  # description missing/empty — re-eligible
```

Allowed transitions:
- `QUEUED → SCRAPE_FAILED`
- `PROCESSING → SCRAPE_FAILED`
- `SCRAPE_FAILED → QUEUED` (when re-scraping kicks off — re-entry into the pipeline)
- `SCRAPE_FAILED → PROCESSING` (next-cycle pickup may skip QUEUED depending on dispatch)
- `SCRAPE_FAILED → FAILED` (when `scrape_attempts` exceeds cap)

Semantically distinct from `FAILED`:
- `FAILED` = workflow exception or unrecoverable error. Sticky.
- `SCRAPE_FAILED` = description didn't extract; retry-eligible up to a cap.

### 2. Description quality gate in extract_job_node

`src/agents/preparation_workflow.py:148` — after `adapter.extract(raw_input)`:

```python
MIN_DESCRIPTION_CHARS = 200  # tunable in settings

description = (job_posting.get("description") or "").strip()
if len(description) < MIN_DESCRIPTION_CHARS:
    logger.warning(
        "Scrape produced short/empty description (%d chars) for job %s — marking SCRAPE_FAILED",
        len(description), job_id,
    )
    state["scrape_failed"] = True
    state["error_message"] = f"Description too short ({len(description)} chars)"
    state["current_step"] = BusinessState.SCRAPE_FAILED
    return state
```

Route from `route_after_extract`: if `scrape_failed` is set, go to a new `save_scrape_failed_node` rather than the filter/compose chain. The node writes a minimal `JobRecord` with:
- `status = SCRAPE_FAILED`
- `scrape_attempts = (existing.scrape_attempts or 0) + 1`
- `raw_input` preserved (LinkedIn ID + URL)
- `last_scrape_error = state["error_message"]`
- `last_scrape_attempt_at = utcnow()`

Threshold rationale: 200 chars is conservative — every legitimate LinkedIn posting we've seen has been >500. Empty + a few stub elements like "Apply now" can reach ~50; 200 leaves comfortable headroom. Make it a setting (`SCRAPER_MIN_DESCRIPTION_CHARS=200`).

### 3. State-aware dedup at queue consumption

`src/services/jobs/job_queue.py:177-184` becomes:

```python
RETRY_ELIGIBLE_STATES = {BusinessState.SCRAPE_FAILED}
MAX_SCRAPE_ATTEMPTS = 3  # settings.SCRAPER_MAX_ATTEMPTS

if existing is not None:
    if existing.status in RETRY_ELIGIBLE_STATES and (existing.scrape_attempts or 0) < MAX_SCRAPE_ATTEMPTS:
        logger.info(
            "Re-attempting previously failed scrape for %s (attempt %d/%d)",
            scoped_job_id, (existing.scrape_attempts or 0) + 1, MAX_SCRAPE_ATTEMPTS,
        )
        # fall through — workflow runs; save_scrape_failed_node will increment counter
    else:
        logger.info("Skipping already-processed job %s (status: %s)", scoped_job_id, existing.status)
        continue
```

When `scrape_attempts` reaches the cap, `save_scrape_failed_node` transitions the row to `FAILED` instead of staying in `SCRAPE_FAILED`, so it gets locked out cleanly.

Optional backoff: skip retry if `last_scrape_attempt_at` is within `SCRAPER_RETRY_BACKOFF_MINUTES` (default 60). Prevents the same broken job from re-failing on every scheduler tick. Pulls the LinkedIn ID into the in-memory `_seen_job_ids` of the scraper for the current cycle anyway, so this is mostly a guard against the manual scheduler trigger being hammered.

### 4. Schema migration

`src/services/db/tables.py` — add to `Job`:

```python
scrape_attempts = Integer(default=0)
last_scrape_error = Text(null=True, default=None)
last_scrape_attempt_at = Timestamp(null=True, default=None)
```

`JobRecord` (`src/models/unified.py`) — add matching optional fields. Piccolo migration via `piccolo migrations new piccolo_app --auto` then `piccolo migrations forwards piccolo_app`.

For SQLite, the existing pattern in `_migrate_legacy_schema()` (`src/services/db/job_repository.py:670+`) handles ALTER TABLE adds; mirror it for the three new columns. Existing rows get `scrape_attempts = 0` and NULLs for the others.

### 5. Cascade delete

Replace the bare `delete(job_id)` on both repositories with `delete_for_user(job_id, user_id)` that:

1. Verifies ownership (`SELECT WHERE job_id AND user_id`).
2. Loads the `current_pdf_path` and every `CVAttemptTable.pdf_path` for the job.
3. Deletes all CV attempts (`DELETE FROM cv_attempt WHERE job_id = ?`).
4. Deletes the job row.
5. After DB commit succeeds, unlinks PDF files (best-effort; log + swallow `FileNotFoundError`, raise on other `OSError`).

Return `True` on success, `False` if not found / not owned. Wrap (1)–(4) in a transaction where the engine supports it (Piccolo's `Transaction` ctx manager).

Keep the old `delete(job_id)` as a thin wrapper that calls the new method without ownership check, OR drop it entirely after auditing callers (existing `cleanup()` method uses raw `DELETE` queries, not the per-row `delete()`, so the audit should be quick).

### 6. API endpoint

`src/api/main.py` — new:

```python
@app.delete("/api/jobs/{job_id}")
async def delete_job(
    job_id: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> dict:
    ctx = request.app.state.ctx
    deleted = await ctx.repository.delete_for_user(job_id, user.id)
    if not deleted:
        raise HTTPException(404, "Job not found")
    return {"deleted": True, "job_id": job_id}
```

404 on missing **or** not-owned (don't leak existence of other users' jobs).

### 7. UI: delete button on Preview cards

Locate the card component (likely `ui/src/lib/components/` — needs `grep` for `pending` / `hitl`). For each card, add a small icon button (trash icon, top-right or in the action row), behind a `confirm()` dialog:

> "Delete this job? It may reappear on the next LinkedIn search if it's still in your results."

On confirm:
1. `DELETE /api/jobs/{job_id}`.
2. Optimistically remove the card from the local store; refetch on error.
3. Show a toast on success.

Tooltip text on hover of the button itself (separate from the confirm body): the same one-liner.

Always available regardless of card state — explicitly chosen, per discussion.

### 8. One-shot cleanup for the current backlog

A script at `scripts/cleanup_empty_description_jobs.py` that:

1. Queries all `JobRecord` rows where `LENGTH(description) < 200` (or description IS NULL) and `status IN ('pending', 'completed', 'failed')`.
2. For each, calls `delete_for_user(job_id, user_id)` to get cascade cleanup.
3. Reports counts per user.

Dry-run by default; `--apply` to commit. Idempotent.

Alternative: instead of deleting, transition them to `SCRAPE_FAILED` with `scrape_attempts = 0` so the next scheduler tick re-processes them naturally. This preserves the row's `created_at` and any user-visible history. Probably the better default — call this `--mode rescrape` and make it the default; `--mode delete` for outright removal.

## File-by-file changes

| File | Change |
|------|--------|
| `src/models/state_machine.py` | Add `SCRAPE_FAILED` state + transitions |
| `src/models/unified.py` | Add `scrape_attempts`, `last_scrape_error`, `last_scrape_attempt_at` to `JobRecord` |
| `src/services/db/tables.py` | Add three columns to `Job` table |
| `src/services/db/job_repository.py` | Add `delete_for_user(job_id, user_id)` with cascade on both impls; legacy migration for new columns |
| `src/services/jobs/job_queue.py` | State-aware dedup: allow `SCRAPE_FAILED` retry up to cap |
| `src/agents/preparation_workflow.py` | Quality gate after extract; new `save_scrape_failed_node`; routing |
| `src/config/settings.py` | `SCRAPER_MIN_DESCRIPTION_CHARS=200`, `SCRAPER_MAX_ATTEMPTS=3`, `SCRAPER_RETRY_BACKOFF_MINUTES=60` |
| `src/api/main.py` | `DELETE /api/jobs/{job_id}` endpoint |
| `ui/src/lib/components/...` | Delete button + confirm + API call (find exact path during impl) |
| `scripts/cleanup_empty_description_jobs.py` | One-shot backlog cleanup (rescrape or delete modes) |
| `tests/unit/test_state_machine.py` | Transition tests for `SCRAPE_FAILED` |
| `tests/unit/test_job_repository.py` | Cascade delete tests (DB rows + PDF unlink) |
| `tests/unit/test_job_queue.py` | Dedup with retry-eligible state + attempt cap |
| `tests/unit/test_preparation_workflow.py` | Quality gate triggers `SCRAPE_FAILED` path |

## Rollout order

1. **State + schema** (state_machine, unified models, tables, migration) — no behavior change yet.
2. **Repository cascade delete** + unit tests.
3. **Workflow quality gate + save_scrape_failed_node** — write-side only; rows start landing in new state.
4. **State-aware dedup in queue consumer** — read-side; broken rows now re-attempt.
5. **API endpoint** + unit/integration tests.
6. **UI delete button**.
7. **One-shot backlog cleanup script** — run with `--mode rescrape` against prod after deploying steps 1–4.

Steps 1–6 ship together (user confirmed). Step 7 runs as a post-deploy operation.

## Risks & mitigations

- **Retry storms.** A LinkedIn outage could mark many rows `SCRAPE_FAILED`. The `scrape_attempts` cap (3) + 60-min backoff bounds the blast radius. Worst case: each broken job retries 3× over ~3 hours then becomes `FAILED`.
- **Quality gate false positives.** Some legitimate jobs may have short descriptions (rare, but possible for early-stage startup postings). Mitigation: 200-char threshold is conservative; tune via setting if false positives appear in logs.
- **PDF unlink races.** If a workflow is writing a PDF while a delete fires, the file may be unlinked mid-write. Acceptable — delete is user-initiated, and a re-scrape would regenerate. Wrap unlink in try/except.
- **Foreign key cascades.** Piccolo `CVAttemptTable` references `Job.job_id`. Manual deletion of attempts in the correct order avoids FK violations; alternative is `ON DELETE CASCADE` at the schema level (cleaner but requires a separate migration to add).
- **`InMemoryJobRepository` parity.** The dev/test repo must implement the same cascade behavior; tests should pin both impls to the same contract.

## Open questions

- Should `SCRAPE_FAILED` rows appear on the Preview page? Default: **no** — they're not actionable to the user. They live in the DB only as dedup state. The user only sees them disappear and reappear on the next scrape, which matches mental model.
- Should the manual delete button confirm on `applied` rows differently (extra warning)? Out of scope for v1; revisit if users complain about accidental deletes of application history.
