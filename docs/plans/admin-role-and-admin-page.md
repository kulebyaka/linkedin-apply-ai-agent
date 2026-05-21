# Admin Role & Admin Page

## Overview

Introduce a `role` column on users (enum: `trial`, `premium`, `admin`, extensible later), a CLI promotion script, and a new `/admin` UI section with sub-pages for jobs (filterable across all users), queue & scheduler dashboard, error log, and user management. All admin endpoints are gated by a new `get_admin_user` FastAPI dependency. Admins can view everything, retry failed jobs, and delete jobs (single + bulk). Pages poll periodically for fresh data.

## Context

- Files involved:
  - `src/models/user.py` (existing — `User` Pydantic model; needs `role` field + `UserRole` enum)
  - `src/services/db/tables.py` (existing — `UserTable`; needs `role` column + migration)
  - `src/services/auth/user_repository.py` (existing — user CRUD; needs `list_all_users`, `set_role`, plus migration logic mirroring `filter_preferences`/`model_preferences` pattern at lines 56–70)
  - `src/services/auth/auth.py` (existing — magic-link + JWT)
  - `src/services/jobs/job_repository.py` (existing — currently user-scoped only; needs admin-scope methods on the abstract class + both implementations: `list_all_jobs(...)`, `count_by_status_global(...)`, `delete(...)`, `re_enqueue(...)`)
  - `src/api/main.py` (existing — endpoints + DI; needs `get_admin_user` dependency + new `/api/admin/*` routes; existing dev-bypass at `/api/auth/dev-login` should respect role too)
  - `src/context.py` (existing — `AppContext`; no change expected, but verify scheduler handle is accessible for queue/scheduler endpoints)
  - `src/services/jobs/scheduler.py` (existing — `LinkedInSearchScheduler`; expose `get_jobs_state()` for next-fire times per user)
  - `src/services/jobs/job_queue.py` (existing — `ConsumerManager`; expose `is_running`/`task_count`/queue depth)
  - `scripts/promote_user.py` (to be created — CLI to set role for an email)
  - `ui/src/lib/api/admin.ts` (to be created — admin API client)
  - `ui/src/lib/stores/auth.svelte.ts` (existing — auth store; needs `role` field exposed)
  - `ui/src/routes/admin/+layout.svelte` (to be created — role guard + nav)
  - `ui/src/routes/admin/+page.svelte` (to be created — index/redirect)
  - `ui/src/routes/admin/jobs/+page.svelte` (to be created — filterable jobs table)
  - `ui/src/routes/admin/queue/+page.svelte` (to be created — queue + scheduler dashboard)
  - `ui/src/routes/admin/errors/+page.svelte` (to be created — error log viewer)
  - `ui/src/routes/admin/users/+page.svelte` (to be created — user management)
  - `ui/src/lib/components/admin/` (to be created — table, filter bar, stat card components)
  - `tests/unit/test_admin_authz.py` (to be created)
  - `tests/unit/test_admin_endpoints.py` (to be created)
  - `tests/unit/test_user_role_migration.py` (to be created)
  - `tests/unit/test_job_repository_admin.py` (to be created — admin-scope repo methods)
  - `tests/e2e/test_admin_ui.py` (to be created — happy-path admin flow)
  - `CLAUDE.md` (existing — needs updates to document role model + admin endpoints)
- Related patterns:
  - **DI via `AppContext`**: pull dependencies from `request.app.state.ctx` — see `_get_ctx`, `_get_orchestrator`, `_get_hitl` in `src/api/main.py`.
  - **FastAPI auth dependency**: follow `get_current_user` (raises 401) and `get_optional_user` (returns None) patterns at `src/api/main.py:82–141`. `get_admin_user` should layer on top of `get_current_user`, raising 403 when `role != "admin"`.
  - **Piccolo schema migration**: add the column in `tables.py`, then mirror the runtime migration block in `UserRepository.initialize()` (lines 56–70) — `PRAGMA table_info(user)` check, `ALTER TABLE` if missing, default existing rows to `"trial"`.
  - **Repository abstract pattern**: extend the ABC in `src/services/jobs/job_repository.py:66`, then implement in both `InMemoryJobRepository` and `SQLiteJobRepository`.
  - **Svelte 5 routing**: existing routes live under `ui/src/routes/<name>/+page.svelte`. Layout guards via `+layout.svelte` redirect when auth/role missing — see `ui/src/routes/+layout.svelte` for the existing auth guard.
  - **API client style**: see `ui/src/lib/api/{hitl,settings,auth}.ts` for the fetch-wrapper convention.
- Dependencies: all already present — `piccolo`, `fastapi`, `pydantic`, Svelte 5. No new packages.

## Development Approach

- **Testing approach**: Regular (code first, then tests within each task).
- Complete each task fully (code + tests + suite green) before moving to the next.
- Use `uv` for any Python package/run commands; project memory `feedback_uv_not_pip.md` requires it.
- Async API consistently — every new endpoint and repo method is `async def`.
- Preserve existing user-scoped methods on `JobRepository`; admin-scope methods are additive, never replace them.
- Frontend uses Svelte 5 runes (`$state`, `$derived`) as in `ui/src/lib/stores/*.svelte.ts`.
- Polling intervals: jobs page on filter change + 10s; queue page 5s; errors page 10s; users page on mount. Make interval a constant per page, not a setting.
- **CRITICAL: every task MUST include new/updated tests**.
- **CRITICAL: all tests must pass before starting next task**.

## Implementation Steps

### Task 1: Add `role` enum + column + migration

**Files:**
- Modify: `src/models/user.py`
- Modify: `src/services/db/tables.py`
- Modify: `src/services/auth/user_repository.py`
- Create: `tests/unit/test_user_role_migration.py`

- [x] Add `UserRole` Python enum in `src/models/user.py` with values `TRIAL = "trial"`, `PREMIUM = "premium"`, `ADMIN = "admin"`.
- [x] Add `role: UserRole = UserRole.TRIAL` field to `User` Pydantic model.
- [x] Add `role = Varchar(length=20, default="trial", index=True)` column to `UserTable` in `src/services/db/tables.py`.
- [x] In `UserRepository.initialize()`, extend the existing `PRAGMA table_info(user)` block: if `role` column missing, `ALTER TABLE user ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'trial'`, then commit.
- [x] Update `UserRepository.create_user()`, `get_user_by_email()`, `get_user_by_id()`, `update_user()` to read/write the `role` field (default `"trial"` when creating).
- [x] Add `UserRepository.set_role(user_id: str, role: UserRole) -> User` method.
- [x] Add `UserRepository.list_all_users(limit: int = 200, offset: int = 0) -> list[User]` method ordered by `created_at` desc.
- [x] Write unit test: fresh DB creates user with `role="trial"`.
- [x] Write unit test: old DB without `role` column gets migrated, existing rows default to `"trial"`.
- [x] Write unit test: `set_role` persists and round-trips.
- [x] Run project test suite: `uv run pytest tests/unit/test_user_role_migration.py -v` then `uv run pytest -q` — must pass before task 2.

### Task 2: Admin-scope repository methods + `get_admin_user` dependency

**Files:**
- Modify: `src/services/jobs/job_repository.py`
- Modify: `src/api/main.py`
- Create: `tests/unit/test_admin_authz.py`
- Create: `tests/unit/test_job_repository_admin.py`

- [ ] On `JobRepository` ABC, add abstract methods: `list_all_jobs(*, user_ids: list[str] | None, statuses: list[str] | None, sources: list[str] | None, created_from: datetime | None, created_to: datetime | None, search: str | None, limit: int, offset: int) -> list[JobRecord]`, `count_all_jobs(...same filter args...) -> int`, `count_by_status_global(window_hours: int | None = None) -> dict[str, int]`, `list_jobs_with_errors(limit: int, offset: int) -> list[JobRecord]`, `delete(job_id: str) -> bool`.
- [ ] Implement all five in `InMemoryJobRepository` (filter in Python over `self._jobs.values()`).
- [ ] Implement all five in `SQLiteJobRepository` using Piccolo `Job.select().where(...)` and `Job.delete().where(...)`. For free-text search, `LIKE` against `job_posting->>'$.title'`, `job_posting->>'$.company'`, and `error_message` (use Piccolo raw `Job.raw` if JSON-path predicates are awkward).
- [ ] Add `get_admin_user` dependency in `src/api/main.py` after `get_optional_user`: depends on `get_current_user`, raises `HTTPException(403, "Admin role required")` if `user.role != "admin"`.
- [ ] Add type alias `AdminUser = Annotated[User, Depends(get_admin_user)]`.
- [ ] Write unit test: non-admin user hitting any future admin endpoint gets 403; admin user passes.
- [ ] Write unit tests for each new repository method against `InMemoryJobRepository` (filter combinations, paging, status counts including the `window_hours` filter, delete idempotency).
- [ ] Write unit tests for the same surface against `SQLiteJobRepository` using a temp DB (mirror existing repo test conventions).
- [ ] Run project test suite: `uv run pytest -q` — must pass before task 3.

### Task 3: Admin API endpoints

**Files:**
- Modify: `src/api/main.py`
- Modify: `src/services/jobs/scheduler.py` (expose `get_jobs_state()` returning `[{user_id, last_run_at, next_run_at, last_status}]`)
- Modify: `src/services/jobs/job_queue.py` (expose `ConsumerManager.snapshot() -> {is_running, task_count, queue_depth}` — read `queue.qsize()` from the queue it manages)
- Create: `tests/unit/test_admin_endpoints.py`

- [ ] `GET /api/admin/jobs` — query params: `user_id` (repeatable), `status` (repeatable), `source` (repeatable), `created_from`, `created_to`, `search`, `limit` (default 50, max 200), `offset`. Returns `{items: list[JobRecord], total: int}`. Uses `list_all_jobs` + `count_all_jobs`.
- [ ] `GET /api/admin/jobs/{job_id}` — full job detail (any user). 404 if missing.
- [ ] `POST /api/admin/jobs/{job_id}/retry` — only valid if status in {`failed`}; transitions to `queued`, re-enqueues onto `ctx.job_queue`, returns updated record. 409 if not retriable.
- [ ] `DELETE /api/admin/jobs/{job_id}` — deletes the record and any associated PDF file under `data/generated_cvs/{user_id}/{job_id}.pdf` (best-effort, log on failure). 404 if missing.
- [ ] `POST /api/admin/jobs/bulk-delete` — body: `{job_ids: list[str]}` (max 100). Returns `{deleted: int, failed: list[str]}`.
- [ ] `GET /api/admin/queue` — returns `ConsumerManager.snapshot()` + scheduler `get_jobs_state()` + `count_by_status_global(window_hours=24)` and same for `window_hours=168`.
- [ ] `POST /api/admin/scheduler/run/{user_id}` — manually fires the LinkedIn search for the named user. Reuses scheduler trigger logic from existing `/api/jobs/linkedin-search` endpoint, but admin can specify any `user_id`.
- [ ] `GET /api/admin/errors` — paged list of jobs where `error_message` or `last_scrape_error` is non-null, ordered by `updated_at` desc. Query params: `limit`, `offset`, `since` (datetime).
- [ ] `GET /api/admin/users` — returns paged user list with derived counts: `{user, job_counts: dict[status, int], last_job_at: datetime | None}`. Uses `count_by_status_global` per-user variant or N+1 (acceptable at expected scale; <500 users).
- [ ] `PUT /api/admin/users/{user_id}/role` — body `{role: "trial"|"premium"|"admin"}`. Calls `UserRepository.set_role`. 400 on invalid role. **Disallow demoting the last admin**: count admins first; if `current_user_id == user_id and current_role == admin and target_role != admin and admin_count == 1`, raise 409.
- [ ] All admin endpoints depend on `AdminUser`.
- [ ] `GET /api/auth/me` response: extend so the frontend receives `role`.
- [ ] Write endpoint unit tests with `TestClient`: at minimum one success + one 403 (non-admin) for each endpoint, plus the last-admin demotion guard.
- [ ] Run project test suite: `uv run pytest -q` — must pass before task 4.

### Task 4: Promotion CLI script

**Files:**
- Create: `scripts/promote_user.py`
- Modify: `tests/unit/test_user_role_migration.py` (add CLI tests OR create `tests/unit/test_promote_user_cli.py`)

- [ ] `scripts/promote_user.py` accepts `--email <email>` and `--role <trial|premium|admin>` (default `admin`). Uses `uv run` shebang-compatible entry: `if __name__ == "__main__"` block, parses args with `argparse`, initialises `UserRepository`, calls `set_role`.
- [ ] Prints `Promoted <email> to <role>` on success, exits 0. Exits 1 with clear error if user missing.
- [ ] Add `--list-admins` flag that prints all current admins.
- [ ] Write unit tests invoking the script via `subprocess` or by importing the `main()` function directly against a temp DB.
- [ ] Run project test suite: `uv run pytest -q` — must pass before task 5.

### Task 5: Frontend — auth store, admin layout, API client

**Files:**
- Modify: `ui/src/lib/stores/auth.svelte.ts` (expose `role`, add `isAdmin` derived)
- Modify: `ui/src/lib/api/auth.ts` (if it parses the `/api/auth/me` response, include `role`)
- Create: `ui/src/lib/api/admin.ts`
- Create: `ui/src/routes/admin/+layout.svelte`
- Create: `ui/src/routes/admin/+page.svelte`

- [ ] In `auth.svelte.ts`, add `role: UserRole | null` to the store state; populate from `/api/auth/me`. Add `isAdmin = $derived(state.user?.role === "admin")`.
- [ ] `ui/src/lib/api/admin.ts` exports typed fetchers: `listJobs(filters)`, `getJob(id)`, `retryJob(id)`, `deleteJob(id)`, `bulkDeleteJobs(ids)`, `getQueueState()`, `runScheduler(userId)`, `listErrors(params)`, `listUsers()`, `setUserRole(id, role)`. Use the existing `apiFetch` from `ui/src/lib/api/client.ts`.
- [ ] `admin/+layout.svelte`: on mount, if `!authStore.isAdmin`, `goto('/')`. Render a sidebar nav linking to `/admin/jobs`, `/admin/queue`, `/admin/errors`, `/admin/users` plus `<slot />`.
- [ ] `admin/+page.svelte`: redirect to `/admin/jobs` on mount.
- [ ] Manual smoke: log in as admin → `/admin` redirects to `/admin/jobs`; log in as non-admin → guard kicks back to `/`.
- [ ] Add a frontend unit test or component test verifying the layout guard (Vitest if configured; otherwise a Playwright assertion deferred to Task 9).
- [ ] Run project test suite: `uv run pytest -q` and `cd ui && npm run check` — must pass before task 6.

### Task 6: Admin jobs page (filterable table)

**Files:**
- Create: `ui/src/routes/admin/jobs/+page.svelte`
- Create: `ui/src/lib/components/admin/FilterBar.svelte`
- Create: `ui/src/lib/components/admin/JobsTable.svelte`

- [ ] `FilterBar.svelte`: bindable props for `userIds: string[]`, `statuses: string[]`, `sources: string[]`, `createdFrom: string`, `createdTo: string`, `search: string`. Multi-select chips for users (loaded from `/api/admin/users`), statuses (from `BusinessState` enum mirrored as constants), sources (`linkedin|url|manual`). Date pickers via native `<input type="date">`. Free-text search input with 300ms debounce.
- [ ] `JobsTable.svelte`: columns: created_at, user email, status badge, source, title, company, has_error indicator, actions (View, Retry if `failed`, Delete).
- [ ] Page composes filter bar + table, calls `listJobs(filters)` on filter change and on a 10s interval. Pagination controls (Prev/Next + total count display).
- [ ] Bulk-select with header checkbox; "Delete selected" calls `bulkDeleteJobs`. Confirm dialog before bulk delete.
- [ ] "Retry" and "Delete" buttons call the corresponding endpoint and refresh the table. Toast on success/failure (reuse `ToastNotification.svelte`).
- [ ] Add Vitest tests for FilterBar binding + JobsTable rendering with mock data, OR defer to e2e in Task 9 if Vitest isn't set up — verify which by checking `ui/package.json`.
- [ ] Run project test suite: `uv run pytest -q` and `cd ui && npm run check && npm run build` — must pass before task 7.

### Task 7: Admin queue & scheduler dashboard

**Files:**
- Create: `ui/src/routes/admin/queue/+page.svelte`
- Create: `ui/src/lib/components/admin/StatCard.svelte`

- [ ] `StatCard.svelte`: title, value, optional sub-label.
- [ ] Page polls `/api/admin/queue` every 5s. Shows: queue depth, consumer running/stopped indicator, consumer task count, status counts for last 24h and 7d (as a small bar/list).
- [ ] Scheduler table: rows per user with email, last_run_at (relative time), next_run_at, last_status (ok/error). Per-row "Run now" button calls `runScheduler(userId)`; toast result.
- [ ] Add a "Refresh now" button alongside the polling indicator.
- [ ] Add a manual test note in task description (no automated UI test required here unless Vitest exists).
- [ ] Run project test suite: `uv run pytest -q` and `cd ui && npm run check` — must pass before task 8.

### Task 8: Admin errors viewer + users page

**Files:**
- Create: `ui/src/routes/admin/errors/+page.svelte`
- Create: `ui/src/routes/admin/users/+page.svelte`

- [ ] Errors page: polls `/api/admin/errors` every 10s. Columns: updated_at, user email, job title, error type (`error_message` vs `last_scrape_error`), truncated error text, link to job detail. Expand row to view full error. "Since" filter (last 1h, 24h, 7d, all).
- [ ] Users page: lists users from `/api/admin/users` with email, role, created_at, job counts (queued/processing/completed/failed), last_job_at. Role dropdown per row calls `setUserRole(id, role)` with optimistic update + rollback on error. Disallow changing own role to non-admin (UI guard, server-side guard already in place from Task 3).
- [ ] Confirm dialog before any role change.
- [ ] Run project test suite: `uv run pytest -q` and `cd ui && npm run check && npm run build` — must pass before task 9.

### Task 9: Verify acceptance criteria

**Files:**
- Create: `tests/e2e/test_admin_ui.py`

- [ ] Write e2e test: login as admin (use existing dev-bypass + promote via Task 4 CLI in fixture), visit `/admin/jobs`, apply a status filter, verify table updates; visit `/admin/queue` and assert queue card renders; visit `/admin/users` and toggle a user's role.
- [ ] Manual test: log in as non-admin user, navigate to `/admin` directly — verify redirect to `/`.
- [ ] Manual test: as admin, retry a failed job — verify it returns to `queued` and is processed.
- [ ] Manual test: as admin, bulk-delete 3 test jobs — verify they vanish from DB and PDFs removed from `data/generated_cvs/`.
- [ ] Manual test: as admin, demote yourself when you are the only admin — verify 409 error toast.
- [ ] Run full test suite: `uv run pytest -q`.
- [ ] Run linter: `uv run ruff check src/ tests/ scripts/` and `cd ui && npm run check`.
- [ ] Verify test coverage for new backend code is ≥ 80%: `uv run pytest --cov=src --cov-report=term-missing tests/unit/test_admin_endpoints.py tests/unit/test_admin_authz.py tests/unit/test_job_repository_admin.py tests/unit/test_user_role_migration.py`.

### Task 10: Update documentation

- [ ] Update `CLAUDE.md`:
  - Add `UserRole` enum + `role` field to "User & Auth Models" section.
  - Add `Admin API` table listing all `/api/admin/*` endpoints.
  - Add an "Admin & Roles" section explaining the role model, promotion via `scripts/promote_user.py`, and the `get_admin_user` dependency pattern.
  - Mark "Admin Role & Admin Page" as ✅ Complete in the Implementation Status table.
- [ ] Update `README.md` (if it covers user roles or first-time setup) to mention `scripts/promote_user.py --email you@example.com --role admin` as the first-admin bootstrap step.
- [ ] Move this plan to `docs/plans/completed/admin-role-and-admin-page.md`.
