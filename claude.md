# Claude.md - LinkedIn Job Application Agent

This document provides context for Claude Code (or any AI assistant) to effectively work with this codebase.

**IMPORTANT**: The `implementation-plan.md` file is the **source of truth** for all functional and non-functional requirements, architecture decisions, and design specifications. Always refer to it when making architectural decisions or implementing features.

Derivable detail (directory layout, dependency list, API routes, model fields, command lines) is
deliberately **not** duplicated here — read the code. What follows is the part the code can't tell
you: design rationale, non-obvious contracts, and gotchas. Node-level workflow detail is in
`agents.md`; LLM-layer internals in `src/llm/CLAUDE.md`.

## Project Overview

**LinkedIn Job Application Agent** is a self-hosted, multi-user system that:
- Authenticates users with magic-link email (Resend.com) + JWT cookie
- Scrapes LinkedIn job postings hourly per user (opt-in scheduler, per-user search preferences)
- Uses an LLM to filter jobs and detect hidden disqualifiers
- Tailors each user's master CV per job and renders a PDF
- Puts every result through a Tinder-like batch Human-in-the-Loop review
- Supports four LLM providers (OpenAI, Anthropic, DeepSeek, Grok), selectable per user *and* per
  operation

**It does not apply to jobs.** There is no application workflow and no Easy Apply automation — the
old stubs were deleted rather than left to rot. "Approve" in the review UI records
`BusinessState.APPROVED` and opens the LinkedIn tab; the user applies by hand. Any doc or comment
suggesting otherwise is stale — fix it.

## Pipeline Architecture

The pipeline is **split at the HITL boundary** so generated CVs can be reviewed in a batch rather
than one at a time:

- **Discovery** (`src/services/jobs/scheduler.py`) — not a graph. Per-user LinkedIn search on an
  APScheduler interval; every discovered job is persisted as `queued` *at discovery* and pushed onto
  an `asyncio` queue drained by a single `ConsumerManager` task.
- **Preparation** (`src/agents/preparation_workflow.py`) — extract → filter → compose CV →
  generate PDF → save.
- **HITL boundary** — batch review UI: decline / retry-with-feedback / mark-reviewed.
- **Retry** (`src/agents/retry_workflow.py`) — re-composes the CV with user feedback and loops the
  record back to `pending`.
- **Dispatcher** (`src/agents/dispatcher.py`) — the *only* way workflows are invoked. Owns the
  `config["configurable"]` dict, workflow-thread registration, and failure persistence. Four call
  sites previously duplicated this; don't add a fifth by calling `ainvoke` directly.
- **Shared** (`src/agents/_shared.py`) — LLM client construction, CV compose, PDF gen, master CV
  loading, config accessors.

### Workflow Modes

- **MVP Mode** (`mode="mvp"`): Generate PDF only, skip HITL, status = `completed`
- **Full Mode** (`mode="full"`): Generate PDF, save to DB with status = `pending` for HITL review

## Key Design Patterns

### 1. Dependency Injection via AppContext
- `src/context.py` defines a single `AppContext` dataclass holding all shared dependencies
- Created once at startup via `create_app_context()` and stored in `app.state.ctx`
- No module-level globals — all dependencies are explicit and injected
- Workflow nodes receive repositories via LangGraph's `config["configurable"]` dict
- Domain services (`JobOrchestrator`, `HITLProcessor`, `WorkflowDispatcher`) receive the full `AppContext`
- Most `AppContext` fields are `| None`: optional subsystems (scheduler, browser, notification repo,
  schedulers) are only wired when their config enables them. Guard before use.

### 2. LangGraph Workflows (Async-Native)
- All workflow node functions are `async def` — use `await` directly, no `asyncio.run()` hacks
- Invoked via `workflow.ainvoke()` (through the dispatcher), compiled with a `MemorySaver` checkpointer
- Shared logic extracted to `src/agents/_shared.py` to eliminate duplication
- State management with TypedDict classes
- **`current_step` (a `WorkflowStep`) and `target_status` (a `BusinessState`) are separate fields.**
  Routing functions read `target_status`. Writing a `BusinessState` into `current_step` mixed the two
  enums and was the historical source of routing bugs.
- Nodes signal business outcomes by setting `target_status` / `error_message`, not by raising.

### 3. Job Lifecycle State Machine
- `src/models/state_machine.py` defines `BusinessState` and `WorkflowStep` enums
- `BusinessState`: `queued` → `processing` → `completed` (MVP) or `pending` (HITL) →
  `approved`/`declined`/`retrying`; plus `failed`, `filtered_out`, `scrape_failed`, and the
  reserved-but-unused `applying`/`applied`
- Two deliberate name/value mismatches, preserved for data + frontend compatibility:
  `COMPLETED = "completed"` means "CV ready", `PENDING = "pending"` means "pending HITL review"
- `ALLOWED_TRANSITIONS` enforces valid changes; violations raise `InvalidStateTransitionError`.
  Both `InMemoryJobRepository` and `SQLiteJobRepository` validate on `update()`
- `filtered_out` is how LLM-rejected jobs are recorded without generating a CV. It is **not**
  terminal: "Proceed Anyway" transitions it to `processing`
- `scrape_failed` self-transitions so idempotent re-attempts can bump the counter
- `failed` → `queued` exists only for admin-initiated retry
- `approved` is terminal in practice, since nothing consumes it

### 4. Domain Services (Thin API Handlers)
- `JobOrchestrator`: job submission, status queries, workflow dispatch, "Proceed Anyway"
- `HITLProcessor`: approve/decline/retry decisions, pending retrieval, history. Serializes decisions
  per job with an in-process lock so a double-click can't race two workflows onto one record
- API endpoints are thin adapters — extract context, call service, return result

### 5. Multi-LLM Support (Instructor + LiteLLM)
- A single `InstructorClient(BaseLLMClient)` (`src/llm/providers/instructor_client.py`) backs **all**
  providers; provider routing is delegated to LiteLLM via prefixed model strings
  (`anthropic/…`, `openai/…`, `xai/…`, `deepseek/…`). There is **no** `LLMClientFactory`.
- Settings and the model catalog store **bare** model ids; the route prefix is reattached in
  `create_llm_client` (note `grok → xai/`).
- Model choice is **per operation**: `UserModelPreferences.{cv_generation, job_filtering,
  filter_prompt_generation}` is resolved by the caller and passed to `create_llm_client` as an
  override, falling back to `PRIMARY_LLM_PROVIDER` + that provider's `*_MODEL`.
- The model *list* is fetched, not hardcoded — see the catalog notes in `src/llm/CLAUDE.md`.
- Structured output, prompt caching, retry behaviour, and the steps for adding a provider are all
  documented in **`src/llm/CLAUDE.md`**.

### 6. Repository Pattern
- `JobRepository` abstract interface for data persistence (`src/services/db/`)
- `InMemoryJobRepository` (with `asyncio.Lock` for thread safety) for development
- `SQLiteJobRepository` via Piccolo ORM for production; `UserRepository` and
  `NotificationRepository` ride the same engine
- `REPO_TYPE` defaults to `memory`, so **persistence is opt-in** — a restart otherwise loses every job
- Supports `CVCompositionAttempt` tracking for retry history
- Schema changes go through `src/services/db/migrations.py`: each migration inspects the live schema,
  no-ops when already applied, and records itself in `schema_migrations`. That is what lets legacy
  DBs upgrade in place. Add columns there, not with ad-hoc ALTERs at startup.

### 7. CV Validation (Extracted from Composer)
- `CVValidator` in `src/services/cv/cv_validator.py` handles hallucination checks
- Configurable `HallucinationPolicy`: STRICT (raises), WARN (logs), DISABLED (skips)
- `CVComposer` delegates validation to `CVValidator` after composition

### 8. Job Source Adapters
- Abstract interface in `src/services/jobs/job_source.py`
- Adapters for URL extraction, manual input, LinkedIn
- Factory pattern: `JobSourceFactory.get_adapter(source)`
- **Filtering applies to `linkedin` jobs only.** A manually submitted description is never filtered —
  the user asked for it explicitly.

### 9. Multi-User Authentication
- Magic link flow: user enters email → `AuthService` generates token → sends email via Resend.com → user clicks link → JWT cookie set
- `AuthService` in `src/services/auth/auth.py`; `UserRepository` / `MagicLinkRepository` /
  `UserService` alongside it
- Open registration: first login auto-creates the account with role `trial`
- JWT stored in httpOnly cookie (`auth_token`), 30-day expiry
- FastAPI dependencies live in `src/api/deps.py` (re-exported from `src/api/main.py` for
  back-compat with older tests): `get_current_user` (401 on missing auth), `get_optional_user`
  (returns None), `get_admin_user` (403 unless admin)
- All job data is user-scoped: `user_id` on `Job` and `CVAttemptTable`
- `jwt_secret` is validated twice: at import (placeholder tolerated so tests can import Settings)
  and again in `AuthService` before signing anything

### 10. Per-User Data Ownership
- `JobRecord` includes `user_id` — all list queries filter by user
- Master CV stored as JSON in `UserTable.master_cv_json`. **There is still a filesystem fallback**:
  when a user record has no CV, call sites fall back to `MASTER_CV_PATH`
  (`data/cv/master_cv.json`), which is why a missing CV surfaces as `FileNotFoundError`
- Search / filter / model preferences stored as JSON on the user row
- PDF output stored in per-user directories: `data/generated_cvs/{user_id}/{job_id}.pdf`
- `JobRepository.get_for_user(job_id, user_id)` enforces ownership verification
- Scheduler state is **per user** (`UserLastRun`), not global: one user's slow or failing search
  never blocks another's, and an on-demand manual run is never dropped because a scheduled run is
  in flight

### 11. Admin & Roles
- `UserRole` enum (`src/models/user.py`): `trial`, `premium`, `admin`. Default is `trial`; the enum is the extension point for future tiers.
- Persisted as a `role` `Varchar(20)` column on `UserTable` with an index; runtime-migrated onto older DBs by `migrations.py`.
- Two layered dependencies: `get_current_user` (401 when unauthenticated) and `get_admin_user`
  (403 unless `role == "admin"`). Type alias: `AdminUser = Annotated[User, Depends(get_admin_user)]`.
- Admin-scope repository methods are **additive** on top of the user-scoped ones (`list_all_jobs`, `count_all_jobs`, …). User-scoped methods (`list_for_user`, `get_for_user`, …) remain the default path for non-admin callers.
- Bootstrapping the first admin: `uv run python scripts/promote_user.py --email you@example.com --role admin`. The same script supports `--role trial|premium|admin` and `--list-admins`.
- Last-admin guard: `PUT /api/admin/users/{user_id}/role` refuses (409) to demote yourself when you are the only remaining admin. The UI mirrors this guard, but the server-side check is authoritative.
- Frontend: `ui/src/routes/admin/+layout.svelte` redirects to `/` when `auth.isAdmin` is false, and
  `/admin` redirects to `/admin/jobs`. The guard is convenience only — the API is the real boundary.

### 12. Failure Handling & Recovery
- **Persist at discovery.** Jobs are written as `queued` when scraped, not when finished, so a crash
  leaves a visible row instead of a silent gap.
- **Scrape quality gate.** A description under `SCRAPER_MIN_DESCRIPTION_CHARS` (200) becomes
  `scrape_failed` rather than feeding an empty description to the CV composer. Re-attempts are gated
  by `SCRAPER_MAX_ATTEMPTS` *and* `SCRAPER_RETRY_BACKOFF_MINUTES` (`_should_retry_scrape`), so a
  permanently broken posting can't churn every tick.
- **Startup recovery** (`src/services/jobs/recovery.py`) re-enqueues or re-dispatches rows left in
  `queued`/`processing`/`retrying`, bounded by `recovery_attempts` so poison rows can't loop.
- **The dispatcher never clobbers a terminal state.** On exception it writes `failed` only if
  `ALLOWED_TRANSITIONS` permits it from the current status; it can synthesize a `failed` record when
  the workflow died before `save_to_db`.
- **The filter fails open.** If LLM evaluation raises, the job passes through to CV composition. A
  broken filter degrades quality; it never blocks the pipeline.
- **Session-death detection** (`src/services/alerts.py`): a batch of ≥5 detail pages coming back ≥50%
  empty is treated as a stale `li_at` cookie and emails `ADMIN_ALERT_EMAIL`, with cooldown state
  persisted to disk so restarts don't re-alert. `JobRecord.session_authenticated` records the
  per-job signal. **Cookie re-capture is manual, and a replayed `li_at` is often rejected outright by
  LinkedIn** — this is the most fragile part of the system.

### 13. Auto-Refining Filter Prompt
- Declines and "Proceed Anyway" overrides become signals on the job row (`decline_reason`,
  `override_reason`, `refine_signal_state`: pending → proposed → consumed). Each signal feeds the
  refiner exactly once.
- A weekly `RefinementScheduler` makes one LLM call per opted-in user and produces a
  `RefinementProposal` for the *auto-learned block* of their `custom_prompt`, plus a persistent
  notification linking to `/settings#filter`.
- **The proposal is never applied automatically.** Accept splices the block between the auto-learned
  markers (`apply_learned_block`); reject discards. Both consume the signals.
- `AUTO_REFINE_ENABLED` is a global kill switch; the per-user opt-in defaults to **off**.

### 14. Notifications
- `src/services/notifications/` + `/api/notifications/*`: persistent, user-scoped, with an unread
  count driving the nav bell. Used by the refiner today.
- Distinct from `AdminAlertService`, which emails the *operator* about infrastructure problems.
- There is **no** per-job failure webhook or Telegram delivery yet (see
  `docs/plans/telegram-job-notifications.md`).

### 15. Search Query Translation
- LinkedIn treats a comma as literal text, not "OR". `linkedin_search.py` rewrites a comma list
  (`Junior Accountant, Finance Assistant`) into `"Junior Accountant" OR "Finance Assistant"`.
- A query already using standalone uppercase `OR`/`AND`/`NOT`, quotes, or parentheses is returned
  **unchanged**, so a power user's query is never mangled.

## Master CV Format

- Stored as JSON in each user's DB record (`UserTable.master_cv_json`), schema in `src/models/cv.py`
- Uploaded via Settings UI or API (`PUT /api/users/me`), or extracted from an uploaded PDF resume by
  an LLM: `POST /api/users/me/master-cv/extract` returns 202 with an `extraction_id` to poll at
  `GET /api/users/me/master-cv/extract/{extraction_id}`. Non-obvious constraints:
  - It reuses the user's **`cv_generation`** model choice, and 400s unless that provider is
    PDF-capable (`provider_supports_pdf` → OpenAI and Anthropic only; DeepSeek and Grok are not)
  - One extraction in flight per user; a second returns 409
  - MIME and provider capability are checked *before* the body is read; size
    (`PDF_CV_UPLOAD_MAX_BYTES`) and page count (`PDF_CV_UPLOAD_MAX_PAGES`) after
  - `src.agents._shared` is imported lazily here because it pulls in WeasyPrint's native libs
- Contains comprehensive work history, skills, projects — the LLM recomposes relevant portions per job
- Loaded from the user record and passed via workflow state under the `master_cv` key

## Development Guidelines

- **Use `uv`, not `pip`.** `uv sync` to install, `uv run <cmd>` to execute. There is no
  `requirements.txt` / `requirements-test.txt`.
- Line length 100 (black + ruff). `uv run ruff check src/ tests/`, `uv run mypy src/`.
- **macOS `DYLD_LIBRARY_PATH` conflict.** WeasyPrint needs `/opt/homebrew/lib` on
  `DYLD_LIBRARY_PATH` to find gobject; Chromium **crashes** when it is set. So
  `browser_automation.py` (and the `scripts/linkedin_*` probes) strip `DYLD_*` from the browser
  subprocess env — a no-op on Linux. If PDF generation fails with a library error, export it; if the
  browser dies instantly, that stripping is what you're relying on.
- There is **no CI test job** — `release.yml` only builds and deploys. Run the suite locally.

### Testing

- Mock LLM responses for determinism; `job_fixtures` can record/replay real scraped jobs and LLM
  responses.
- Markers are strict (`--strict-markers`): `unit`, `integration`, `eval`, `e2e`, `llm`, `slow`,
  `expensive`. `unit` is declared but not actually applied — select that tier by path.
- `uv run pytest -m "not e2e"` is the everyday loop (~820 tests, ~9s). A bare `uv run pytest` also
  runs E2E, which auto-starts its own API + Vite servers and needs a Chromium binary.
- The eval tier is skipped unless `deepeval` is installed on purpose (it isn't a declared
  dependency, and those tests spend real money).
- Details in `docs/testing_guide.md` and `tests/README.md`.

### Configuration (`.env`)

Full list in `src/config/settings.py`. The non-obvious ones:

- `DEV_AUTH_BYPASS=true` + `DEV_AUTH_EMAIL=dev@local.test` — local-only auth bypass for browser/UX
  testing. Exposes `POST /api/auth/dev-login`, which mints a JWT cookie for the dev user with no
  email round-trip. **The server refuses to start if this is true and `APP_URL` is non-localhost**;
  the route 404s when false. See `.claude/skills/web-browser/SKILL.md` for usage.
- `REPO_TYPE=memory` (default) or `sqlite` — memory is the default, so persistence is **opt-in**.
- `JOB_FILTER_REJECT_THRESHOLD=30` / `JOB_FILTER_WARNING_THRESHOLD=70` — two-threshold routing:
  below reject → saved as `filtered_out` and CV generation is skipped; below warning → warning
  badge + red flags shown in HITL review. Per-user values override both.
- `LINKEDIN_SEARCH_KEYWORDS` / `LINKEDIN_SEARCH_LOCATION` — **fallback only**, used when no users
  have configured search preferences.
- `SEED_JOBS_FROM_FILE=true` **disables LinkedIn scraping entirely** and replays
  `SCRAPED_JOBS_PATH` instead. Easy to forget when debugging "why isn't it scraping".
- `LINKEDIN_SEARCH_SCHEDULE_ENABLED=false` by default — without it, no browser is launched and no
  queue consumer starts (except one spun up by startup recovery).
- `ADMIN_ALERT_EMAIL` empty disables all operator alerts.
- `CV_TEMPLATE_NAME` must name a directory under `src/templates/cv/`: `modern`, `compact`,
  `profile-card`. Anything else raises at `PDFGenerator` construction.

**Never commit `.env` or real CV data to git!**

## Common Tasks

Step-by-step procedures (adding an LLM provider, changing CV tailoring, adding a workflow step,
debugging a workflow run) live in the `project-howtos` skill — invoke it rather than duplicating
them here. LLM-layer internals: `src/llm/CLAUDE.md`.

## Next Steps

1. **Durable LinkedIn session auth** — persistent browser profile instead of cookie capture/replay
   (`docs/plans/persistent-browser-profile-auth.md`). Blocks everything downstream.
2. **LinkedIn Easy Apply** — LinkedIn moved it to a React SDUI modal, so prior work is stale
   (`docs/plans/sdui-easy-apply-rework.md`, `docs/plans/ARCHITECTURE-browser-agent.md`).
3. **Job notifications** — Telegram delivery (`docs/plans/telegram-job-notifications.md`).

## Reference Implementations

`Obsolete/` was a symlink to two third-party LinkedIn-automation projects (GodsScion's
`Auto_job_applier_linkedIn`, AIHawk's `Jobs_Applier_AI_Agent_AIHawk`) kept purely as reference.
**The symlink points at a Windows path and is dead on this machine** — don't send anyone to it. Older
plan documents that cite `Obsolete/**/ARCHITECTURE.md` are citing unavailable files.

## Quick Start

```bash
uv run uvicorn src.api.main:app --reload    # API
cd ui && npm run dev                        # UI
```

`.\scripts\dev.ps1` starts both and kills previous instances, but it is **Windows-only** — it uses
`Get-NetTCPConnection` and `Win32_Process`, so it fails under `pwsh` on macOS/Linux.

Then open:
- **UI**: http://localhost:5173 (Vite dev server with HMR)
- **API**: http://localhost:8000 (FastAPI with auto-reload on .py changes)

## Security Notes

- **Never commit** `.env` or actual CV data
- **Secure storage** for LinkedIn credentials
- **Rate limiting** for API calls
- **User data** stays on self-hosted VPS
- `DEV_AUTH_BYPASS` must never be true in production — the startup guard is the backstop, not the plan
