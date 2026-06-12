# Feature Specification: Application Status Page

## Overview
- **Feature**: A dedicated, user-scoped "Applications" page (`/applications`) that surfaces the full job history and live status for the signed-in user — mirroring the admin `/admin/jobs` page but scoped to the current user. The Tinder-like Review page is stripped back to pure CV review.
- **Status**: Draft
- **Created**: 2026-06-01
- **Author**: User + Claude Code

## Problem Statement

The `persist-jobs-at-discovery` work (already merged, see `docs/plans/persist-jobs-at-discovery.md`) made every scraped/submitted job visible in the UI with a live status. The natural place it landed was the home page (`ui/src/routes/+page.svelte`), which is the **Tinder-like HITL review** surface. As a result that page now carries a lot of status-related chrome that distracts from its single job — reviewing one AI-generated CV at a time:

- A read-only **in-flight list** (`InFlightList`) of `queued`/`processing`/`retrying` cards polled every 3s.
- A row of **status-count badges** in the header (`queued`, `processing`, `retrying`, `scrape_failed`, `applied`, `approved`, `declined`, `filtered_out`, `failed`).

These belong in a separate "where are all my jobs?" view, not on the focused review card. The user wants a clean Review page and a new **Application Status** page where they can see their entire job history and current pipeline state — equivalent to `/admin/jobs` but accessible to all users and implicitly filtered to themselves.

## Goals & Success Criteria

- **Focused Review page**: the Review surface shows only the pending-review queue and the active Tinder card + decision controls. No in-flight list, no per-status count badges. The simple "N pending" counter stays (it describes the review queue itself, not job statuses).
- **Complete history in one place**: a new `/applications` page lists every job the current user has, across all states, with filtering, full-text search, pagination, and status-count summary cards.
- **No new persistence work**: reuse the existing user-scopeable repository query methods (`list_all_jobs`/`count_all_jobs` already accept a `user_ids` filter) and the existing user-scoped delete + PDF endpoints. The only backend additions are a thin user-scoped list endpoint and a search-coverage tweak.
- **Success Metrics**:
  - Review page no longer renders `InFlightList` or the status-count badge row.
  - `/applications` shows the same job rows for a user that `/admin/jobs?user_id=<them>` would, and a non-admin can reach it.
  - A user can filter by status/source/date, full-text search by title/company/description, page through results, open a generated CV PDF, and delete their own job.

## User Stories

1. As a job-seeker, I want a clean Review page that only asks me to approve/decline/retry CVs, so I'm not distracted by pipeline status while reviewing.
2. As a job-seeker, I want a dedicated "Applications" page listing every job the system found for me with its current status, so I can see my whole history at a glance.
3. As a job-seeker, I want to filter and full-text search that list (by status, source, date, and free text matching title/company/description), so I can find a specific job quickly.
4. As a job-seeker, I want to open the generated CV PDF and delete jobs I don't care about, directly from the Applications page.

## Functional Requirements

### Core Capabilities

- **Strip status chrome from Review** (`ui/src/routes/+page.svelte`):
  - Remove the `<InFlightList>` block and its `inFlightStore` wiring (`loadInitial`, `startPolling`, `stopPolling`, imports).
  - Remove the `STAT_BADGES` / `visibleStatBadges` block and the `{#each visibleStatBadges}` header markup.
  - **Keep** the `{reviewQueue.totalCount} pending` badge, the Tinder `JobCard`, `DecisionButtons`, `NavigationControls`, the keyboard-shortcut hint, and `FeedbackModal`.
- **New `/applications` route** (`ui/src/routes/applications/+page.svelte`), available to any authenticated user (not behind the admin layout guard):
  - **Status-count summary cards** across the top (the counts moved off Review), driven by `GET /api/jobs/stats` (already user-scoped). Cards are **clickable**: clicking a card sets the status filter to that single status (and toggles it off when clicked again), resetting `offset=0` and refetching.
  - A **filter bar**: status (multi), source (multi), created-from/created-to date range, and a free-text search box. No user filter (implicitly the current user). Clicking a summary card keeps the status filter bar in sync (single status selected).
  - A **jobs table** with pagination (prev/next, page size 50), polled on the shared poll interval for live status updates.
  - **Row actions**: open/download the CV PDF (when one exists) and delete the job (with confirmation).
  - **Open in Review**: rows with `status == "pending"` get a "Review" affordance (clicking the row, or a dedicated button) that navigates to `/?job=<job_id>` and focuses that job's card in the Tinder review queue. Non-pending rows have no review affordance (they're done or in-flight).
- **Configurable poll intervals**: introduce a single shared UI constant for poll cadence and route all polling through it (Applications page, admin pages, and any remaining pollers). Default **5000ms**, overridable via a Vite env var.
- **Nav**: add an "Applications" link to the main nav (`ui/src/routes/+layout.svelte`) **after** "Generate".

### User Flows

#### Flow A — Reviewing CVs (unchanged behavior, decluttered UI)
1. User opens `/` (Review). Page loads the pending queue only.
2. The Tinder card, decision buttons, navigation, and keyboard shortcuts work exactly as before.
3. No in-flight cards or status badges render. The "N pending" badge still shows when the queue is non-empty.

#### Flow B — Viewing application status / history
1. User clicks "Applications" in the nav → `/applications`.
2. On mount the page fetches:
   - `GET /api/jobs/stats` → renders summary cards (one per non-zero status).
   - `GET /api/jobs?limit=50&offset=0` → renders the first page of the table (newest first).
3. User adjusts filters (status chips, source chips, date range, search text) — or clicks a summary card to filter by that status. Each change resets `offset=0` and refetches.
4. A poll timer (shared interval, default 5s) silently refetches the current page so in-flight rows advance their status without a manual refresh.
5. Prev/Next page through results; footer shows `start–end of total`.

#### Flow C — Row actions
1. **Open CV**: for rows with a generated CV (`pdf_path` set / status in `pending`/`approved`/`applied`/`completed`), a button opens `GET /api/jobs/{job_id}/pdf` in a new tab (reuse `getPdfUrl`/`downloadPdf` from `ui/src/lib/api/hitl.ts`).
2. **Delete**: a delete button calls `DELETE /api/jobs/{job_id}` (existing, user-scoped via `repository.delete_for_user`). Confirm via `window.confirm`, then silently refetch.

#### Flow D — Open a pending job in Review (deep-link)
1. On the Applications page the user clicks a row whose `status == "pending"` (or its "Review" button).
2. The app navigates to `/?job=<job_id>` (`goto('/?job=' + jobId)`).
3. The Review page (`+page.svelte`) reads the `job` query param on mount, calls `reviewQueue.loadPending()`, then `reviewQueue.selectJob(jobId)` to set `currentIndex` to that job's position in the queue.
4. The matching Tinder card is shown with decision controls. If the job is no longer in the pending queue (already decided elsewhere), fall back to index 0 and show an info toast ("That job is no longer pending").
5. The `?job=` param is consumed (optionally cleared from the URL after selection) so a manual refresh doesn't re-trigger the jump unexpectedly.

### Data Model

No new tables, columns, or Pydantic models. The page consumes existing shapes:

- `JobRecord` rows (via the new list endpoint) — same JSON the admin jobs endpoint returns. The frontend can reuse the existing `AdminJobRecord` TypeScript interface (`ui/src/lib/api/admin.ts`) or a thin alias of it.
- `GET /api/jobs/stats` → `dict[str, int]` (status → count), already user-scoped.

### Integration Points

| Touchpoint | Change |
|------------|--------|
| `ui/src/routes/+page.svelte` | Remove `InFlightList` + `inFlightStore` usage and the `STAT_BADGES`/`visibleStatBadges` status-count block. Keep pending badge, Tinder card, decision/nav/keyboard, feedback modal. **Add deep-link handling**: on mount read `?job=` from `$page.url.searchParams`; after `loadPending()`, call `reviewQueue.selectJob(jobId)`. |
| `ui/src/lib/stores/reviewQueue.svelte.ts` | **New action** `selectJob(jobId: string): boolean` — sets `currentIndex` to the index of `jobId` in `pendingJobs`; returns false (and leaves index at 0) if not found. Exported on the store object. |
| `ui/src/routes/+layout.svelte` | Add `{ href: '/applications', label: 'Applications' }` to `navLinks` **after** the Generate entry (renders in both desktop and mobile nav automatically). |
| `ui/src/lib/config.ts` (new) | Export a shared `POLL_INTERVAL_MS` constant: `export const POLL_INTERVAL_MS = Number(import.meta.env.VITE_POLL_INTERVAL_MS ?? 5000);`. All pollers import this instead of hardcoding. |
| Existing pollers | Replace hardcoded intervals with `POLL_INTERVAL_MS`: `ui/src/routes/admin/jobs/+page.svelte` (`POLL_MS = 10_000`), and any other admin page / store that polls. The removed `inFlightStore` (3s) goes away entirely. |
| `ui/src/routes/applications/+page.svelte` | **New.** Page modeled on `ui/src/routes/admin/jobs/+page.svelte` but: no `user_id` filter, no bulk-delete/retry, no cross-user user-email lookup; calls the new user-scoped list endpoint + `GET /api/jobs/stats`. |
| `ui/src/lib/api/jobs.ts` (or extend `hitl.ts`) | **New client fn** `listMyJobs(filters)` → `GET /api/jobs` (paginated/filterable). Reuse `getPdfUrl`/`downloadPdf`/`deleteJob` already in `hitl.ts`. |
| UI components | Reuse `ui/src/lib/components/admin/StatCard.svelte` for summary cards. The admin `JobsTable`/`FilterBar` are admin-shaped (user column, bulk select, retry). Prefer a **new lightweight `ApplicationsTable` + `ApplicationsFilterBar`** under `ui/src/lib/components/applications/` rather than overloading the admin components with conditional props. |
| `src/api/routes/jobs.py` | **New endpoint** `GET /api/jobs` (user-scoped list, see API design) returning `{items, total, limit, offset}`. |
| `src/services/jobs/job_orchestrator.py` (or a thin service method) | Add a user-scoped `list_jobs(user_id, filters)` that delegates to `repository.list_all_jobs(user_ids=[user_id], ...)` + `count_all_jobs(user_ids=[user_id], ...)`. Keeps the route a thin adapter. |
| `src/services/db/sqlite_admin_queries.py` `_build_admin_filter_sql` | **Extend search** to also match the job description: add `OR COALESCE(json_extract(job_posting, '$.description'), '') LIKE ?` (and the matching param). Update the in-memory equivalent in `src/services/db/in_memory_repository.py` (search currently lowercases title/company/error — add description). This satisfies the "full-text search would be great" requirement and benefits admin search too. |

## Technical Design

### Architecture

The cleanest path is to **reuse the existing admin query plumbing, scoped to the caller**. `repository.list_all_jobs` / `count_all_jobs` already accept `user_ids`, `statuses`, `sources`, `created_from`, `created_to`, `search`, `limit`, `offset`. A user-scoped endpoint simply forces `user_ids=[current_user.id]`, so a regular user can never widen the scope.

- Backend: one new route `GET /api/jobs` (thin adapter) → orchestrator/service method → repository (already implemented). No abstract-interface changes, no migrations.
- Frontend: a new route that is structurally a slimmed-down copy of `/admin/jobs`, plus the removal of status chrome from the Review page. New, dedicated table/filter components keep the admin components clean.

This mirrors the established patterns in CLAUDE.md: thin API adapters delegating to domain services; user-scoped methods as the default path; admin-scope methods as additive variants.

### Technology Stack
- **Frameworks**: existing FastAPI (backend route) + SvelteKit / Svelte 5 runes (frontend), Tailwind (neobrutalist styles already in use).
- **Libraries**: none new.
- **Tools**: none new (no migrations).

### Data Persistence
- No schema changes. Reads go through existing repository methods (`list_all_jobs`, `count_all_jobs`, `get_status_counts`, `delete_for_user`) and existing PDF/HTML routes.

### API / Interface Design

#### New: `GET /api/jobs` (user-scoped list)

```
GET /api/jobs?status=pending,processing&source=linkedin&created_from=...&created_to=...&search=acme&limit=50&offset=0
```

- Auth: `CurrentUser` (any authenticated user).
- Query params mirror the admin list filters **except** `user_id` (forced to the caller):
  - `status` (CSV or repeated), `source` (CSV or repeated), `created_from`/`created_to` (ISO), `search` (free text), `limit` (default 50, cap 100), `offset` (default 0).
- Response:
  ```json
  { "items": [ JobRecord, ... ], "total": <int>, "limit": 50, "offset": 0 }
  ```
- Implementation:
  ```python
  @router.get("/api/jobs")
  async def list_my_jobs(request, user: CurrentUser, status=..., source=..., created_from=..., created_to=..., search=..., limit=50, offset=0):
      ctx = get_ctx(request)
      items = await ctx.repository.list_all_jobs(
          user_ids=[user.id], statuses=..., sources=..., created_from=..., created_to=..., search=search,
          limit=min(limit, 100), offset=offset,
      )
      total = await ctx.repository.count_all_jobs(user_ids=[user.id], statuses=..., ...)
      return {"items": items, "total": total, "limit": limit, "offset": offset}
  ```
  Route is registered **before** `GET /api/jobs/{job_id}/...` patterns so the static path is not shadowed (FastAPI matches in declaration order; place `GET /api/jobs` near the top of `jobs.py` or ensure it doesn't collide — `/api/jobs` vs `/api/jobs/{job_id}/status` don't conflict, but verify ordering with existing `/api/jobs/stats`, `/api/jobs/cleanup`).

#### Reused (unchanged)
- `GET /api/jobs/stats` → status-count summary cards.
- `GET /api/jobs/{job_id}/pdf` and `/html` → open CV.
- `DELETE /api/jobs/{job_id}` → delete own job.

#### Frontend client (new)
```ts
// ui/src/lib/api/jobs.ts
export interface MyJobsFilters { status?: string[]; source?: string[]; created_from?: string; created_to?: string; search?: string; limit?: number; offset?: number; }
export interface MyJobsResponse { items: AdminJobRecord[]; total: number; limit: number; offset: number; }
export async function listMyJobs(filters: MyJobsFilters): Promise<MyJobsResponse> { /* GET /api/jobs?... credentials:'include' */ }
```

## Non-Functional Requirements

- **Performance**: list + count are two indexed `SELECT`s scoped by `user_id`; page size capped at 100. The 10s poll refetches only the current page. Negligible load.
- **Security**: the endpoint **always** forces `user_ids=[current_user.id]` — a user can never list another user's jobs. Delete and PDF routes already enforce ownership (`delete_for_user`, user-scoped status). No admin gate needed.
- **Observability**: no new metrics required. Errors surface via the existing toast pattern on the frontend and `logger.error` on the route.
- **Error Handling**:
  - Unknown status/source tokens in the query: accept and let the `IN (...)` simply match nothing, or 400 on invalid `BusinessState` tokens to match the `/api/hitl/pending` precedent — **choose 400 for status validation** for consistency with `hitl.py`.
  - Delete of a non-owned/missing job returns 404 (existing behavior); frontend shows an error toast.
  - List failure returns 500 with a logged exception; frontend shows a toast and keeps the last good page.

## Implementation Considerations

### Design Trade-offs

| Decision | Considered | Chosen | Rationale |
|----------|-----------|--------|-----------|
| Backend list source | New user-scoped repo method vs. reuse `list_all_jobs(user_ids=[me])` | **Reuse `list_all_jobs`/`count_all_jobs`** | Already user-scopeable; zero new repository/interface/migration work. |
| Endpoint shape | Extend `/api/hitl/pending` vs. new `GET /api/jobs` | **New `GET /api/jobs`** | HITL pending is review-queue-shaped (`PendingApproval`); the history view wants raw `JobRecord` + pagination + filters. Distinct concern, distinct endpoint. |
| Frontend components | Reuse admin `JobsTable`/`FilterBar` with conditional props vs. new components | **New `applications/` components** | Admin table has user column, bulk-select, retry — overloading it with `isAdmin?` flags muddies it. A focused copy is simpler and keeps admin untouched. |
| Route guard | Under admin layout vs. top-level authed route | **Top-level `/applications`** | Must be reachable by all users; the admin `+layout.svelte` redirects non-admins. |
| Search coverage | Keep title/company/error vs. add description | **Add description** | User explicitly wants full-text; one extra `LIKE` clause, also improves admin search. |
| Row actions | Read-only vs. PDF + delete vs. + retry | **PDF + delete** (no retry) | PDF/delete reuse existing user-scoped endpoints. User-scoped retry has no endpoint yet — out of scope to avoid new surface. |

### Dependencies
- No new packages, tables, columns, or migrations.
- Relies on already-merged `persist-jobs-at-discovery` (provides the in-flight statuses and `list_by_states`/stats plumbing) — confirmed present on this branch.

### Testing Strategy

- **Unit (backend)**
  - `GET /api/jobs` returns only the caller's jobs; a second user's rows never appear even with no filters.
  - Status/source/date/search filters narrow results correctly; `total` reflects the filtered count, not the page size.
  - Search matches description (new) in addition to title/company (both SQLite and in-memory repos).
  - `limit` is capped at 100.
- **API**
  - 400 on an invalid `status` token (parity with `/api/hitl/pending`).
  - Delete via the page hits the existing user-scoped delete and 404s for non-owned ids.
- **Frontend / E2E** (extend `tests/e2e` patterns)
  - Review page no longer renders the in-flight list or status badges; pending badge + Tinder card still render.
  - `/applications` loads, shows summary cards, filters/searches, pages, opens a PDF, and deletes a row (with confirmation).
  - Clicking a summary card filters the table to that status; clicking again clears it.
  - Clicking a `pending` row navigates to `/?job=<id>` and the Review page shows that exact job's card (assert the rendered title/company matches). A stale/decided `job` id falls back to the first card with an info toast.
  - Nav shows "Applications" and routes to the page for a non-admin user.

## Out of Scope

- User-scoped **retry** of failed jobs (no endpoint exists; admin retry stays admin-only).
- **Bulk delete** for users (admin-only feature retained on `/admin/jobs`).
- A per-job **detail page** (admin `handleView` is itself still a placeholder). Row "view" here means open the CV PDF/HTML, not a dedicated detail route.
- Changes to the underlying persistence or recovery behavior from `persist-jobs-at-discovery`.
- Real-time push (WebSocket/SSE); polling is sufficient.

## Resolved Decisions

- **Clickable summary cards**: yes — clicking a status card filters the table to that status (toggles off on re-click) and syncs the status filter bar.
- **Poll interval**: 5000ms, **configurable** via a single shared `POLL_INTERVAL_MS` constant (`ui/src/lib/config.ts`, overridable with `VITE_POLL_INTERVAL_MS`). All UI pollers route through this constant and use 5s for now (replacing admin/jobs' 10s and the removed in-flight 3s).
- **Nav order**: "Applications" sits **after** "Generate".

## Open Questions

- None outstanding.

## References
- `docs/plans/persist-jobs-at-discovery.md` — predecessor; added the in-flight statuses + stats this page consumes.
- `ui/src/routes/+page.svelte` — Review page to declutter.
- `ui/src/routes/+layout.svelte` — nav links.
- `ui/src/routes/admin/jobs/+page.svelte` — structural template for the new page.
- `ui/src/lib/api/admin.ts` — `AdminJobRecord`, `ListJobsFilters` shapes to reuse/alias.
- `ui/src/lib/api/hitl.ts` — `getPdfUrl`/`downloadPdf`/`deleteJob` to reuse.
- `ui/src/lib/components/admin/StatCard.svelte` — summary card component to reuse.
- `ui/src/lib/stores/inFlightJobs.svelte.ts` — store to remove from Review.
- `src/api/routes/jobs.py` — where `GET /api/jobs` is added; existing PDF/HTML/delete/stats routes.
- `src/api/routes/hitl.py` — precedent for `BusinessState` token validation (400 on unknown).
- `src/services/db/repository.py` — `list_all_jobs`/`count_all_jobs`/`get_status_counts` (user-scopeable via `user_ids`).
- `src/services/db/sqlite_admin_queries.py` — `_build_admin_filter_sql` (extend search to description).
- `src/services/db/in_memory_repository.py` — in-memory search to keep in sync.
</content>
</invoke>
