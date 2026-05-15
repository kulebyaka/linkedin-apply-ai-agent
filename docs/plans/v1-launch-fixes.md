# V1 Launch Fixes — Audit + Ralphex Plan

## Context

The LinkedIn Job Application Agent is functionally complete per `CLAUDE.md`, but the UX and operational surface has gaps that will block 3–5 friends from using it daily. Scope per user decisions: ship in 1–2 weeks, shared LLM key (operator pays), keep the `Approve` button visible with a clear "Coming soon" toast.

### Audit Findings (verified against the codebase)

**Verified facts during the pass:**
- `SQLiteJobRepository.initialize()` already creates all tables via `create_table(if_not_exists=True)` at `src/services/db/job_repository.py:643-646` and is called from FastAPI lifespan (`src/api/main.py:136-140`). **DB init is not a launch blocker.**
- Master CV is loaded from the user's DB record into workflow state; missing CV raises `ValueError("Master CV not provided in workflow state")` deep inside the workflow (`src/agents/_shared.py:174`) — not at submit time.
- `_handle_approve` (`src/services/jobs/hitl_processor.py:191-199`) flips state to `APPROVED` and returns "Application workflow not yet implemented" in the message — there is no downstream side effect.
- `JobQueue.process_queue` invokes `prep_workflow.ainvoke(...)` without `asyncio.wait_for`, so a stalled LLM call hangs the job in `processing` indefinitely.
- `cors_origins` is a hardcoded list of localhost entries in `src/config/settings.py:181-186`.

**Blockers (must fix before sharing the URL):**

| # | Issue | Evidence |
|---|---|---|
| B1 | No onboarding path for the master CV; first submission fails deep in the workflow. | `src/agents/_shared.py:174`; settings page expects raw JSON paste; welcome page is informational only. |
| B2 | Jobs can hang in `queued`/`processing` forever; no per-item timeout, no surfaced error. | `src/services/jobs/job_queue.py` lacks `asyncio.wait_for`; UI polls forever (`ui/src/routes/generate/+page.svelte`). |
| B3 | `Approve` looks broken — state flips but nothing happens; UI doesn't render the "not implemented" message clearly. | `src/services/jobs/hitl_processor.py:191-199`; HITL components in `ui/src/lib/components/review/`. |

**Serious (would mar daily use within a week):**

| # | Issue | Evidence |
|---|---|---|
| S1 | CORS hardcoded to localhost — first real-domain deploy looks broken. | `src/config/settings.py:181-186` |
| S2 | No fail-fast on missing LLM API key; surfaces as a generic 500 mid-workflow. | `src/agents/_shared.py:97-98` |
| S3 | LinkedIn cookies expire (24–72h) with no recovery or visible signal. | `src/services/linkedin/browser_automation.py`; `src/services/jobs/scheduler.py` |
| S4 | WeasyPrint system deps missing on bare metal cause cryptic PDF failures. | `src/services/cv/pdf_generator.py` |

**Polish (post-launch, listed at bottom — not in this plan):** mobile HITL layout, per-user rate limits, filtered-jobs visibility, magic-link expiry UX, scheduler status widget, CV JSON schema hints, the actual Application workflow.

---

## Overview

Close the seven blocker/serious gaps that would prevent 3–5 friends from using this daily: add a CV onboarding gate, add workflow timeouts with surfaced errors, make the stubbed Approve action honest, fix CORS for real-domain deploys, fail fast on missing LLM keys, recover gracefully from LinkedIn auth expiry, and pre-flight WeasyPrint at startup.

## Context

- Files involved:
  - `src/config/settings.py` (existing — pydantic-settings; cors_origins, JWT/Resend, LLM config, repo settings)
  - `src/api/main.py` (existing — FastAPI app, lifespan, all endpoints; submit/status/HITL/health)
  - `src/agents/_shared.py` (existing — LLM init, `load_master_cv`, CV compose, PDF gen)
  - `src/agents/preparation_workflow.py` (existing — LangGraph pipeline)
  - `src/services/jobs/job_queue.py` (existing — `JobQueue`, `ConsumerManager`, `process_queue`)
  - `src/services/jobs/hitl_processor.py` (existing — `_handle_approve` returns "not implemented")
  - `src/services/jobs/scheduler.py` (existing — APScheduler-based per-user search)
  - `src/services/linkedin/browser_automation.py` (existing — stealth Playwright + cookie auth)
  - `src/services/cv/pdf_generator.py` (existing — WeasyPrint + Jinja2)
  - `src/services/db/tables.py` (existing — Piccolo tables; `Job`, `UserTable`, etc.)
  - `src/models/unified.py` (existing — `JobRecord`, `JobStatusResponse`)
  - `src/context.py` (existing — `AppContext` DI container)
  - `ui/src/routes/welcome/+page.svelte` (existing — informational only)
  - `ui/src/routes/settings/+page.svelte` (existing — JSON paste editor for CV)
  - `ui/src/routes/generate/+page.svelte` (existing — submit + poll page)
  - `ui/src/lib/components/settings/CVUploadSection.svelte` (existing — CV upload widget)
  - `ui/src/lib/components/review/` (existing — HITL review cards/actions)
  - `ui/src/lib/api/jobs.ts` (existing — frontend API client for jobs)
  - `ui/src/lib/api/settings.ts` (existing — frontend API client for user/settings)
  - `ui/src/lib/data/cv_template.json` (to be created — minimal valid MasterCV)
  - `ui/src/lib/guards/onboarding.ts` (to be created — redirect when no master CV)
  - `docs/DEPLOY.md` (to be created — fresh-VPS deployment checklist)
  - `.env.example` (existing — add CORS_ORIGINS, document shared LLM key)
  - `README.md` (existing — link to DEPLOY.md, system deps note)
  - `tests/unit/test_cv_template.py` (to be created)
  - `tests/unit/test_workflow_timeout.py` (to be created)
  - `tests/unit/test_scheduler_auth.py` (to be created)
- Related patterns:
  - **AppContext DI** (`src/context.py`) — add new startup flags (`llm_ok`, `pdf_ok`) here.
  - **Async-native workflows** — every node is `async def`; preserve this in new code.
  - **Pydantic v2 models** for all request/response shapes.
  - **State machine** (`src/models/state_machine.py`) — only transition to `failed` via the existing allowed transitions; do not invent new states.
  - **Repository pattern** — never call Piccolo tables directly from API/services; go through the repository.
- Dependencies: no new packages required. All work uses what's already installed (FastAPI, Pydantic v2, Piccolo, WeasyPrint, Playwright, Svelte 5).

## Development Approach

- **Testing approach**: Regular (code first, then tests). The codebase already has substantial test coverage and the time budget is tight; TDD for UX changes would over-budget the plan.
- **Package manager**: `uv` — run all commands as `uv run pytest`, `uv run black src/`, etc. **Never** call `pip` directly (project preference).
- Complete each task fully before moving to the next.
- Preserve existing patterns: `async def` everywhere in workflows, AppContext DI for cross-cutting state, Pydantic v2, state-machine-validated transitions.
- **CRITICAL: every task MUST include new/updated tests.**
- **CRITICAL: all tests must pass before starting the next task.**

## Implementation Steps

### Task 1: Onboarding gate + CV template helper (B1)

**Files:**
- Create: `ui/src/lib/data/cv_template.json` — minimal valid `MasterCV` skeleton (matches `src/models/cv.py`).
- Create: `ui/src/lib/guards/onboarding.ts` — utility returning `needsOnboarding(user)` and a redirect helper.
- Modify: `ui/src/routes/+layout.ts` — when authed user has no `master_cv_json`, redirect to `/settings?onboarding=1`.
- Modify: `ui/src/routes/welcome/+page.svelte` — add a "Set up your CV" CTA visible after auth.
- Modify: `ui/src/lib/components/settings/CVUploadSection.svelte` — add "Load template" button that injects `cv_template.json`; expand inline help with required-field list; render an `?onboarding=1` banner.
- Modify: `src/api/main.py` — `POST /api/jobs/submit` returns `409 Conflict` with `{"error": "master_cv_missing"}` when the user has no master CV.
- Modify: `ui/src/routes/generate/+page.svelte` — on 409 `master_cv_missing`, redirect to `/settings?onboarding=1`.
- Create: `tests/unit/test_cv_template.py` — assert `cv_template.json` validates against the `MasterCV` Pydantic model.

Steps:
- [x] Create `cv_template.json` and validate it against `MasterCV` locally before committing.
- [x] Implement `needsOnboarding` guard + apply in `+layout.ts`.
- [x] Wire "Load template" button + onboarding banner in `CVUploadSection.svelte`.
- [x] Add 409 short-circuit to `/api/jobs/submit` (check `master_cv_json` is non-empty before queueing).
- [x] Handle 409 in `ui/src/lib/api/jobs.ts` submit caller; redirect from `generate/+page.svelte`.
- [x] Add "Set up your CV" CTA on welcome page (visible when authed + no CV).
- [x] Write `tests/unit/test_cv_template.py` validating the template parses as `MasterCV`.
- [x] Manual: new user → login → welcome → "Set up your CV" → settings opens in onboarding mode → "Load template" populates → save succeeds → submit job works end-to-end.
- [x] Run project test suite: `uv run pytest` — must pass before Task 2.

### Task 2: Workflow timeout + surfaced error (B2)

**Files:**
- Modify: `src/config/settings.py` — add `workflow_timeout_seconds: int = 300`.
- Modify: `src/models/unified.py` — ensure `JobRecord.error_message: Optional[str]` exists (add if missing); add same field to `JobStatusResponse`.
- Modify: `src/services/db/tables.py` — add `error_message` column to the `Job` table (nullable text).
- Modify: `src/services/db/job_repository.py` `SQLiteJobRepository.initialize()` — after `Job.create_table(if_not_exists=True)`, execute `ALTER TABLE job ADD COLUMN error_message TEXT` inside a try/except that swallows the "duplicate column" error (idempotent for existing DBs).
- Modify: `src/services/jobs/job_queue.py` `process_queue` — wrap the `prep_workflow.ainvoke(...)` call in `asyncio.wait_for(..., timeout=settings.workflow_timeout_seconds)`; on `asyncio.TimeoutError`, update the job to `failed` with `error_message="Workflow timed out after Ns"`; on any other exception, populate `error_message` with `str(exc)` (truncate to ~500 chars).
- Modify: `src/api/main.py` `/api/jobs/{job_id}/status` — include `error_message` in the response.
- Modify: `ui/src/lib/api/jobs.ts` — add `error_message` to the status type.
- Modify: `ui/src/routes/generate/+page.svelte` — cap polling at `MAX_POLL_ATTEMPTS = 150` (5 min @ 2s); on cap or terminal status, render `error_message` if present.
- Create: `tests/unit/test_workflow_timeout.py` — feed `process_queue` a fake workflow that `await asyncio.sleep(10)` with `workflow_timeout_seconds=0.1`; assert job ends as `failed` with `error_message` set.

Steps:
- [x] Add `workflow_timeout_seconds` setting.
- [x] Add `error_message` to `JobRecord` model + `Job` table + idempotent ALTER in `initialize()`.
- [x] Wrap workflow invocation in `asyncio.wait_for`; populate `error_message` on timeout and on caught exceptions.
- [x] Expose `error_message` in `/api/jobs/{job_id}/status` payload.
- [x] Frontend: cap poll attempts; render `error_message` when present.
- [x] Write `tests/unit/test_workflow_timeout.py`.
- [x] Manual: submit a job with an invalid LLM key — UI shows the error within timeout, not "Loading…" forever.
- [x] Run project test suite: `uv run pytest` — must pass before Task 3.

### Task 3: Honest "Approve" action — "Coming soon" toast (B3)

**Files:**
- Modify: `src/services/jobs/hitl_processor.py` `_handle_approve` — keep the state transition; refine `message` to: `"Approved. Automatic application is not yet implemented — please apply via LinkedIn manually for now."`
- Modify: HITL action component in `ui/src/lib/components/review/` (whichever wraps the approve/decline/retry buttons) — on a successful approve response, surface a toast/snackbar (use the existing toast system if one exists; otherwise add a minimal inline banner component at `ui/src/lib/components/Toast.svelte`).
- Modify: `ui/src/routes/+page.svelte` (or the main dashboard route) — add a small "v1 beta" badge with a tooltip listing what's not yet automated (currently: auto-apply).
- Update/add: a small Svelte component test or Playwright E2E in `tests/e2e/test_hitl_review.py` confirming the toast copy appears after approve.

Steps:
- [x] Refine `_handle_approve` message copy.
- [x] Implement/locate toast UI and wire approve-success → toast with the new copy.
- [x] Add the "v1 beta" badge with tooltip.
- [x] Update or add a test asserting the approve flow shows the toast (extend existing `tests/e2e/test_hitl_review.py` if practical; otherwise add a unit-level assertion that the component renders the toast on the success state).
- [x] Manual: approve a job → toast appears with the new copy → job appears in History as `approved`.
- [x] Run project test suite: `uv run pytest` — must pass before Task 4.

### Task 4: Production deploy hardening — CORS + LLM key fail-fast (S1, S2)

**Files:**
- Modify: `src/config/settings.py` — change `cors_origins` to accept a comma-separated env override (`CORS_ORIGINS`); keep the localhost list as the fallback. Add a startup-time warning when `cors_origins` is localhost-only but `app_url` points at a non-local host.
- Modify: `src/context.py` `AppContext` — add `llm_ok: bool` and `llm_error: Optional[str]` fields.
- Modify: `src/api/main.py` lifespan — for the configured `primary_llm_provider`, check the matching API key is set; on missing key, set `ctx.llm_ok = False` and log an `ERROR` (do not crash — operator may fix without restart). Same applies for `fallback_llm_provider` (warn but don't block).
- Modify: `src/api/main.py` `POST /api/jobs/submit` — if `ctx.llm_ok` is False, return `503` with `{"error": "llm_not_configured", "detail": ctx.llm_error}`.
- Modify: `ui/src/lib/api/jobs.ts` + `ui/src/routes/generate/+page.svelte` — on 503 `llm_not_configured`, show "Service not configured — contact admin" banner.
- Modify: `.env.example` — add `CORS_ORIGINS=https://apply.example.com` with a comment explaining the comma-separated format.
- Modify: `README.md` — add a "Deploying to a real domain" section covering `CORS_ORIGINS`, `APP_URL`, `JWT_SECRET`, `RESEND_API_KEY`, and the shared LLM key envs.
- Add: a unit test under `tests/unit/` that constructs `Settings` with `CORS_ORIGINS="https://a.com,https://b.com"` (via monkeypatched env) and asserts the parsed list.

Steps:
- [x] Implement env-driven CORS parsing + startup warning.
- [x] Add `llm_ok`/`llm_error` to `AppContext`; populate in lifespan.
- [x] Short-circuit `/api/jobs/submit` with 503 when LLM not configured.
- [x] Frontend handles 503 with a clear admin-facing banner.
- [x] Update `.env.example` and `README.md`.
- [x] Write the CORS parsing unit test.
- [x] Manual: start API with no LLM keys → submit → expect 503 with the structured payload; start with `CORS_ORIGINS=https://example.com` and confirm middleware reflects it via curl with `-H "Origin: https://example.com"`.
- [x] Run project test suite: `uv run pytest` — must pass before Task 5.

### Task 5: LinkedIn auth resilience (S3)

**Files:**
- Modify: `src/services/linkedin/browser_automation.py` — define `class LinkedInAuthExpiredError(Exception)`; in the scrape/search code path, detect the sign-in redirect (URL contains `/login` or `/uas/login`) and the "session expired" response markers, and raise `LinkedInAuthExpiredError`.
- Modify: `src/services/jobs/scheduler.py` — catch `LinkedInAuthExpiredError` per scheduled run, set scheduler state to `paused_auth_required` (add this enum/state in the same file), record `last_auth_error_at`, and skip subsequent scheduled runs until the operator clears it via API.
- Modify: `src/api/main.py` `/api/jobs/linkedin-search/status` — include `state` (active / paused_auth_required) and `last_auth_error_at` fields.
- Add: `src/api/main.py` `POST /api/jobs/linkedin-search/clear-auth-error` (auth-required) — resets the scheduler state to active after the operator has refreshed cookies.
- Modify: `ui/src/routes/settings/+page.svelte` — fetch the status and show a prominent "LinkedIn session expired — refresh cookies" banner with a "Clear after refresh" button calling the new endpoint.
- Create: `tests/unit/test_scheduler_auth.py` — simulate a scrape raising `LinkedInAuthExpiredError`, assert scheduler transitions to `paused_auth_required` and skips the next tick.

Steps:
- [ ] Define `LinkedInAuthExpiredError` and detection logic.
- [ ] Add `paused_auth_required` state + transition in scheduler.
- [ ] Expose state via status endpoint; add clear-auth-error endpoint.
- [ ] Frontend banner + clear button.
- [ ] Write `tests/unit/test_scheduler_auth.py`.
- [ ] Manual: delete cookies → trigger search → status reflects paused state → UI shows banner → click clear button after refreshing cookies → next search succeeds.
- [ ] Run project test suite: `uv run pytest` — must pass before Task 6.

### Task 6: WeasyPrint pre-flight check (S4)

**Files:**
- Modify: `src/services/cv/pdf_generator.py` — add `def verify_pdf_stack() -> tuple[bool, Optional[str]]` that renders a one-line HTML to PDF in-memory; on failure, parse the exception text for `pango`/`gobject`/`cairo`/`gdk` keywords to produce a useful hint.
- Modify: `src/context.py` `AppContext` — add `pdf_ok: bool` and `pdf_error: Optional[str]` fields.
- Modify: `src/api/main.py` lifespan — call `verify_pdf_stack()`, store result on `AppContext`; do not block startup (operator can fix without restart, and tests may run on machines without WeasyPrint deps installed).
- Modify: `src/api/main.py` `/api/health` — include `pdf_ok` and `pdf_error` in the response.
- Modify: `README.md` — add "System dependencies" with the apt/brew commands for libpango/libgdk/libcairo.
- Add: a unit test under `tests/unit/` that calls `verify_pdf_stack()` and asserts it returns `(True, None)` in CI (skip-if-WeasyPrint-broken to avoid failing on stripped environments).

Steps:
- [ ] Implement `verify_pdf_stack()` and exception-text parsing.
- [ ] Add `pdf_ok`/`pdf_error` to `AppContext`; call during lifespan.
- [ ] Expose via `/api/health`.
- [ ] Update `README.md` system-deps section.
- [ ] Write the unit test (with appropriate skip marker).
- [ ] Manual: `/api/health` returns `pdf_ok: true` on the dev machine.
- [ ] Run project test suite: `uv run pytest` — must pass before Task 7.

### Task 7: Verify acceptance criteria

- [ ] Manual smoke test (fresh user): register a new email → magic link → welcome → "Set up your CV" → load template → save → submit a real LinkedIn job URL → status progresses without hanging → CV PDF downloads → approve in HITL → "Coming soon" toast appears.
- [ ] Failure injection: temporarily set an invalid OpenAI key in `.env` and restart → `/api/jobs/submit` returns 503 with `llm_not_configured` → UI shows admin banner.
- [ ] Failure injection: delete `data/linkedin_cookies.json` and trigger a scheduled search → scheduler enters `paused_auth_required` → UI banner appears.
- [ ] Run full test suite: `uv run pytest`.
- [ ] Run linter/format check: `uv run black src/ --check` and `uv run mypy src/` (best effort — do not block on pre-existing mypy debt).
- [ ] Verify test coverage for new code is reasonable (spot-check the new test modules).

### Task 8: Update documentation

**Files:**
- Create: `docs/DEPLOY.md` — fresh-VPS deployment checklist: required env vars (`JWT_SECRET`, `RESEND_API_KEY`, `APP_URL`, `CORS_ORIGINS`, primary LLM provider + key, `REPO_TYPE=sqlite`, `DB_PATH`), system deps (libpango/libgdk/libcairo + Playwright `playwright install chromium`), `uv` bootstrap, smoke-test recipe (create user → upload CV template → submit a job URL → confirm PDF downloads).
- Modify: `README.md` — link to `docs/DEPLOY.md` from Quick Start; add the "Deploying to a real domain" + "System dependencies" sections referenced earlier.
- Modify: `CLAUDE.md` — note the new `workflow_timeout_seconds`, `error_message`, and `paused_auth_required` patterns under the relevant Implementation Status rows.

Steps:
- [ ] Write `docs/DEPLOY.md`.
- [ ] Update `README.md` (Quick Start + new sections).
- [ ] Update `CLAUDE.md` to reflect the new patterns.
- [ ] Move this plan to `docs/plans/completed/v1-launch-fixes.md` once Task 7 passes.

---

## Post-Launch Polish (NOT in this plan — track separately)

- **P1** — Responsive HITL review UI (mobile-first stacked layout or tab switcher).
- **P2** — Per-user submit rate limiting (e.g. 10 jobs/min/user) in `JobOrchestrator`.
- **P3** — `/api/jobs/filtered` endpoint + count badge in HITL UI so users see why jobs disappeared.
- **P4** — Friendlier magic-link expiry page with "Resend link" button; consider bumping TTL to 30 min.
- **P5** — Scheduler status widget in Settings (last run, jobs found, next run).
- **P6** — Client-side CV JSON schema validation with field-level error messages.
- **P7** — Real Application workflow (Playwright Easy-Apply MVP) — the actual fix for B3.
