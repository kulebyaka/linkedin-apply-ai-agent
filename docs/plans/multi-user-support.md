# Multi-User Support

## Overview

Add a User entity with magic-link authentication (via Resend.com) so the app supports multiple users. Each user gets their own master CV, LinkedIn search preferences, and job pipeline. JWT stored in httpOnly cookie for session management. No sensitive-data protection — friends-only deployment for now.

## Context

- Files involved:
  - `src/services/tables.py` (existing — Piccolo ORM table definitions: Job, CVAttemptTable)
  - `src/models/unified.py` (existing — Pydantic models for JobRecord, API request/response)
  - `src/services/job_repository.py` (existing — JobRepository ABC + InMemory + SQLite implementations)
  - `src/services/job_orchestrator.py` (existing — job submission and status queries)
  - `src/services/hitl_processor.py` (existing — HITL decision processing)
  - `src/agents/_shared.py` (existing — shared workflow utils, loads master CV from filesystem)
  - `src/agents/preparation_workflow.py` (existing — main pipeline workflow)
  - `src/agents/retry_workflow.py` (existing — retry workflow)
  - `src/api/main.py` (existing — FastAPI endpoints)
  - `src/context.py` (existing — AppContext DI container)
  - `src/config/settings.py` (existing — Pydantic settings with env vars)
  - `src/services/scheduler.py` (existing — APScheduler LinkedIn search scheduler)
  - `src/services/linkedin_search.py` (existing — LinkedInSearchParams model + URL builder)
  - `ui/src/lib/api/client.ts` (existing — frontend API client)
  - `ui/src/lib/api/hitl.ts` (existing — frontend HITL API calls)
  - `ui/src/lib/types/index.ts` (existing — TypeScript interfaces)
  - `ui/src/lib/stores/reviewQueue.svelte.ts` (existing — review queue state)
  - `ui/src/routes/+layout.svelte` (existing — root layout with nav)
  - `ui/src/routes/+layout.ts` (existing — `ssr = false`, `prerender = true`)
  - `src/models/user.py` (to be created — User + auth Pydantic models)
  - `src/services/auth.py` (to be created — AuthService: magic link, JWT, Resend)
  - `src/services/user_repository.py` (to be created — UserRepository: CRUD + search prefs)
  - `tests/unit/test_auth.py` (to be created)
  - `tests/unit/test_user_repository.py` (to be created)
  - `ui/src/routes/login/+page.svelte` (to be created)
  - `ui/src/routes/auth/verify/+page.svelte` (to be created)
  - `ui/src/routes/settings/+page.svelte` (to be created)
  - `ui/src/lib/stores/auth.svelte.ts` (to be created — auth state)
  - `ui/src/lib/api/auth.ts` (to be created — auth API calls)
  - `ui/src/lib/api/settings.ts` (to be created — settings API calls)
- Related patterns: Repository ABC (`JobRepository`), AppContext DI, Piccolo ORM tables, LangGraph `config["configurable"]`, Pydantic BaseModel, Svelte 5 runes (`$state`, `$derived`), SvelteKit file-based routing
- Dependencies:
  - `resend` (NEW — magic link email sending via resend.com)
  - `pyjwt` (NEW — JWT token creation and validation)
  - All others already present

## Design Decisions

1. **Authentication**: Magic link via Resend.com. User enters email → receives link with token → clicks link → JWT cookie set. Open registration: first login auto-creates user.
2. **JWT**: httpOnly cookie, 30-day expiry. Magic link token valid 15 minutes. Stored in separate `magic_link` DB table.
3. **User entity**: `id` (UUID), `email` (unique), `display_name`, `master_cv_json` (JSON), `search_preferences` (JSON — full LinkedInSearchParams), `created_at`, `updated_at`.
4. **Data ownership**: `user_id` FK added to `Job` and `CVAttemptTable`. All repository queries filter by user_id.
5. **Master CV**: Stored in User table as JSON column. `load_master_cv()` in `_shared.py` reads from DB via user_id in workflow config instead of filesystem.
6. **Per-user search**: Scheduler iterates all users with configured search preferences, runs a LinkedIn search per user. Jobs tagged with owner's user_id.
7. **LinkedIn session**: Shared system-wide (single browser/cookie). Per-user sessions deferred to application workflow implementation.
8. **PDF storage**: Per-user directories: `data/generated_cvs/{user_id}/{job_id}.pdf`.
9. **Migration**: Wipe existing data. No backwards compatibility. Fresh schema.
10. **Frontend**: SPA (ssr=false). Login page at `/login`, magic link callback at `/auth/verify`, settings at `/settings`. Auth state in Svelte store. API client sends cookies via `credentials: 'include'`.

## Development Approach

- **Testing approach**: Code first, then tests
- Complete each task fully before moving to the next
- Use `uv` for package management (not pip)
- All new backend code is async-native
- Follow existing patterns: Repository ABC, AppContext DI, Piccolo ORM tables
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**
- **Validation after each task**: `uv run pytest tests/ -x -q && uv run ruff check src/`

## Implementation Steps

### Task 1: User & Auth DB tables + Pydantic models

Create the database schema for users and magic link tokens, and the Pydantic models for the auth/user API surface. Add `user_id` foreign key to existing Job and CVAttempt tables. Install new dependencies.

**Files:**
- Modify: `src/services/tables.py`
- Create: `src/models/user.py`
- Modify: `pyproject.toml`

- [x] Add `resend` and `pyjwt` to `pyproject.toml` dependencies, run `uv sync`
- [x] Add `UserTable` Piccolo table to `src/services/tables.py` with columns: `id` (UUID VARCHAR 36, PK), `email` (Varchar 255, unique, indexed), `display_name` (Varchar 100), `master_cv_json` (JSON, null=True), `search_preferences` (JSON, null=True — stores serialized `LinkedInSearchParams`), `created_at` (Timestamptz, indexed), `updated_at` (Timestamptz)
- [x] Add `MagicLinkTable` Piccolo table to `src/services/tables.py` with columns: `token` (Varchar 64, PK), `email` (Varchar 255, indexed), `expires_at` (Timestamptz), `used` (Boolean, default=False)
- [x] Add `user_id` column (Varchar 36, indexed, null=False) to `Job` table and `CVAttemptTable` in `src/services/tables.py`
- [x] Create `src/models/user.py` with Pydantic models: `User` (id, email, display_name, master_cv_json, search_preferences, created_at, updated_at), `LoginRequest` (email), `LoginResponse` (message), `VerifyRequest` (token), `AuthResponse` (user: User, message), `UserUpdateRequest` (display_name, master_cv_json, search_preferences — all optional), `UserSearchPreferences` (mirrors `LinkedInSearchParams` fields: keywords, location, remote_filter, date_posted, experience_level, job_type, easy_apply_only, max_jobs)
- [x] Write unit tests for Pydantic models in `tests/unit/test_user_models.py`: validate serialization, optional fields, search preferences defaults
- [x] Run project test suite — must pass before task 2

### Task 2: Auth service + User repository + API endpoints

Build the authentication flow (magic link via Resend, JWT cookie) and user CRUD. Wire into FastAPI as middleware.

**Files:**
- Create: `src/services/auth.py`
- Create: `src/services/user_repository.py`
- Modify: `src/api/main.py`
- Modify: `src/context.py`
- Modify: `src/config/settings.py`

- [x] Add auth-related settings to `src/config/settings.py`: `RESEND_API_KEY` (str), `JWT_SECRET` (str), `MAGIC_LINK_TTL_MINUTES` (int, default=15), `JWT_EXPIRY_DAYS` (int, default=30), `APP_URL` (str — base URL for magic link callback, e.g. `http://localhost:5173`)
- [x] Create `src/services/user_repository.py` with `UserRepository` class (uses Piccolo ORM): `create_user(email, display_name) -> User`, `get_by_id(user_id) -> User | None`, `get_by_email(email) -> User | None`, `update(user_id, updates) -> User`, `get_all_with_search_prefs() -> list[User]` (returns users who have non-null search_preferences), `create_magic_link(email, token, expires_at)`, `verify_magic_link(token) -> email | None` (checks expiry and used flag, marks as used), `cleanup_expired_magic_links()`
- [x] Create `src/services/auth.py` with `AuthService` class: `send_magic_link(email) -> None` (generates token via `secrets.token_urlsafe(32)`, stores in DB, sends email via Resend with link to `{APP_URL}/auth/verify?token={token}`), `verify_token(token) -> User` (validates magic link, creates user if first login, returns user), `create_jwt(user_id, email) -> str` (encodes JWT with `user_id`, `email`, `exp` claims using `pyjwt`), `decode_jwt(token) -> dict` (validates and decodes JWT, raises on expired/invalid)
- [x] Add `user_repository: UserRepository` and `auth_service: AuthService` fields to `AppContext` in `src/context.py`. Initialize in `create_app_context()`
- [x] Add `get_current_user` FastAPI dependency to `src/api/main.py`: reads JWT from `auth_token` cookie, decodes via `AuthService.decode_jwt()`, looks up user via `UserRepository.get_by_id()`, returns `User` or raises 401. Create a second dependency `get_optional_user` that returns `None` instead of 401 for public endpoints
- [x] Add auth API endpoints to `src/api/main.py`: `POST /api/auth/login` (accepts `LoginRequest`, calls `auth_service.send_magic_link()`, returns success message), `GET /api/auth/verify?token=...` (calls `auth_service.verify_token()`, sets httpOnly `auth_token` cookie with JWT, returns `AuthResponse`), `GET /api/auth/me` (requires auth, returns current `User`), `POST /api/auth/logout` (clears cookie)
- [x] Add user settings endpoints to `src/api/main.py`: `PUT /api/users/me` (requires auth, accepts `UserUpdateRequest`, updates user via repository), `GET /api/users/me/search-preferences` (returns current search prefs), `PUT /api/users/me/search-preferences` (updates search prefs)
- [x] Update CORS config in `src/api/main.py` to include `allow_credentials=True` so cookies are sent cross-origin between the Vite dev server and FastAPI
- [x] Write tests in `tests/unit/test_auth.py`: test JWT creation/decoding, test magic link token generation/verification, test expired token rejection, test auto-create user on first login
- [x] Write tests in `tests/unit/test_user_repository.py`: test user CRUD, test search preferences storage/retrieval, test `get_all_with_search_prefs()` returns only configured users
- [x] Run project test suite — must pass before task 3

### Task 3: Thread user_id through repository + domain services

Add user_id filtering to all job queries so each user sees only their own jobs. Thread user_id through workflows, orchestrator, and HITL processor.

**Files:**
- Modify: `src/services/job_repository.py`
- Modify: `src/services/job_orchestrator.py`
- Modify: `src/services/hitl_processor.py`
- Modify: `src/agents/_shared.py`
- Modify: `src/agents/preparation_workflow.py`
- Modify: `src/agents/retry_workflow.py`
- Modify: `src/models/unified.py`
- Modify: `src/api/main.py`

- [x] Add `user_id: str` field to `JobRecord` in `src/models/unified.py`. Update `JobSubmitRequest` or workflow invocation to carry user_id
- [x] Update `JobRepository` ABC in `src/services/job_repository.py`: add `user_id: str` parameter to `get_pending(user_id)`, `get_by_status(user_id, status, ...)`, `get_all(user_id, ...)`, `get_history(user_id, ...)`. Keep `get(job_id)` without user_id filter (used internally) but add `get_for_user(job_id, user_id)` that verifies ownership
- [x] Update `InMemoryJobRepository` to filter by `user_id` in all list queries. Store user_id on JobRecord during `create()`
- [x] Update `SQLiteJobRepository` to add `WHERE user_id = ?` to all list queries. Add user_id to INSERT statements
- [x] Update `load_master_cv()` in `src/agents/_shared.py`: change from reading filesystem to accepting `master_cv_json: dict` passed via workflow state (loaded from User table before workflow invocation). Remove the filesystem path dependency
- [x] Add `user_id` and `master_cv` to preparation workflow state TypedDict and retry workflow state TypedDict. Ensure these are populated before workflow invocation
- [x] Update `JobOrchestrator.submit_job()` in `src/services/job_orchestrator.py` to accept and thread `user_id`. Load master CV from user's DB record. Pass user_id in job record and workflow state
- [x] Update `HITLProcessor` in `src/services/hitl_processor.py`: `get_pending(user_id)`, `get_history(user_id, ...)` to filter by user. `process_decision(job_id, decision, user_id)` to verify ownership
- [x] Update PDF generation to use per-user directories: `data/generated_cvs/{user_id}/{job_id}.pdf`. Update `pdf_generator.py` if path construction happens there, or update workflow nodes where path is built
- [x] Update all API endpoints in `src/api/main.py` that return job data to require auth and pass `user_id` from `get_current_user` dependency to orchestrator/HITL processor. Endpoints: `POST /api/jobs/submit`, `GET /api/jobs/{job_id}/status`, `GET /api/jobs/{job_id}/pdf`, `GET /api/jobs/{job_id}/html`, `GET /api/hitl/pending`, `POST /api/hitl/{job_id}/decide`, `GET /api/hitl/history`, `DELETE /api/jobs/cleanup`
- [x] Update tests: `tests/unit/test_job_orchestrator.py`, `tests/unit/test_hitl_processor.py` — add user_id to all test cases. Update any repository mocks to expect user_id parameters
- [x] Run project test suite — must pass before task 4

### Task 4: Per-user LinkedIn search scheduler

Update the scheduler to iterate over all users with configured search preferences and run separate LinkedIn searches per user. Jobs are tagged with the owning user's ID.

**Files:**
- Modify: `src/services/scheduler.py`
- Modify: `src/services/job_queue.py`

- [x] Update `LinkedInSearchScheduler.__init__()` to accept `user_repository: UserRepository` instead of using hardcoded settings for search params
- [x] Refactor `LinkedInSearchScheduler._do_search()`: call `user_repository.get_all_with_search_prefs()` to get all users with configured search preferences. For each user, build `LinkedInSearchParams` from their `search_preferences` JSON, scrape, and enqueue jobs with that user's `user_id` attached
- [x] Update `JobQueue` items to carry `user_id` so that when the consumer processes a queued job, it knows which user owns it. Update the queue item structure (currently just job data) to include user context
- [x] Update `AppContext` creation to pass `user_repository` to scheduler
- [x] Keep the fallback: if no users have search preferences configured, fall back to env-var-based global search params (for backwards compat during transition)
- [x] Write tests for per-user search: mock UserRepository returning 2 users with different search prefs, verify scheduler builds correct params for each and tags jobs with correct user_id
- [x] Run project test suite — must pass before task 5

### Task 5: Frontend auth flow

Add login page, magic link callback, auth state management, and protected route logic. Update API client to send credentials.

**Files:**
- Create: `ui/src/routes/login/+page.svelte`
- Create: `ui/src/routes/auth/verify/+page.svelte`
- Create: `ui/src/lib/stores/auth.svelte.ts`
- Create: `ui/src/lib/api/auth.ts`
- Modify: `ui/src/lib/api/client.ts`
- Modify: `ui/src/lib/api/hitl.ts`
- Modify: `ui/src/routes/+layout.svelte`
- Modify: `ui/src/routes/+layout.ts`

- [x] Create `ui/src/lib/stores/auth.svelte.ts`: auth state store with `user` (User | null), `loading` (boolean), `isAuthenticated` (derived). Methods: `checkAuth()` (calls GET /api/auth/me, updates user or sets null), `logout()` (calls POST /api/auth/logout, clears user)
- [x] Create `ui/src/lib/api/auth.ts`: `requestMagicLink(email: string)`, `verifyToken(token: string)`, `getCurrentUser()`, `logout()`. All calls use `credentials: 'include'`
- [x] Update `ui/src/lib/api/client.ts` and `ui/src/lib/api/hitl.ts`: add `credentials: 'include'` to all `fetch()` calls so the httpOnly cookie is sent
- [x] Change `ui/src/routes/+layout.ts`: set `prerender = false` (needed for dynamic auth state). Keep `ssr = false`
- [x] Create `ui/src/routes/login/+page.svelte`: email input form, submit calls `requestMagicLink()`, shows "Check your email" confirmation. Minimal styling matching existing design system (Tailwind, monospace, border-heavy aesthetic)
- [x] Create `ui/src/routes/auth/verify/+page.svelte`: reads `token` from URL query params on mount, calls `verifyToken()`, on success redirects to `/`, on failure shows error with link back to `/login`
- [x] Update `ui/src/routes/+layout.svelte`: on mount call `auth.checkAuth()`. If not authenticated and not on `/login` or `/auth/verify`, redirect to `/login`. Add user display name + logout button to nav bar (only when authenticated). Add "Settings" nav link
- [x] Test manually: full magic link flow (request → email → click → redirect → authenticated session). Test 401 redirect to login. Test logout clears session
- [x] Run `cd ui && npm run check` (svelte-check) — must pass before task 6

### Task 6: Settings UI page

Build the /settings page with profile editing, master CV upload (JSON), and LinkedIn search preferences configuration.

**Files:**
- Create: `ui/src/routes/settings/+page.svelte`
- Create: `ui/src/lib/api/settings.ts`
- Create: `ui/src/lib/components/settings/ProfileSection.svelte`
- Create: `ui/src/lib/components/settings/CVUploadSection.svelte`
- Create: `ui/src/lib/components/settings/SearchPreferencesSection.svelte`

- [x] Create `ui/src/lib/api/settings.ts`: `updateProfile(data)`, `updateCV(cvJson)`, `getSearchPreferences()`, `updateSearchPreferences(prefs)`. All use `credentials: 'include'`
- [x] Create `ui/src/lib/components/settings/ProfileSection.svelte`: display name edit field with save button. Shows current email (read-only)
- [x] Create `ui/src/lib/components/settings/CVUploadSection.svelte`: textarea for pasting master CV JSON with syntax validation (try JSON.parse on input), file upload button for `.json` files, save button. Show validation status (valid/invalid JSON). Display current CV summary (name from contact section, number of experiences/skills) if CV exists
- [x] Create `ui/src/lib/components/settings/SearchPreferencesSection.svelte`: form fields for all `LinkedInSearchParams` values: keywords (text input), location (text input), remote filter (select: on-site/remote/hybrid/any), date posted (select: 24h/week/month/any), experience level (multi-select checkboxes: internship/entry/associate/mid-senior/director/executive), job type (multi-select checkboxes: full-time/part-time/contract/temporary/internship/volunteer), easy apply only (toggle), max jobs per search (number input, default 50). Save button
- [x] Create `ui/src/routes/settings/+page.svelte`: page layout with three sections (Profile, Master CV, Search Preferences) using the components above. Load current user data on mount via `GET /api/auth/me` and `GET /api/users/me/search-preferences`. Follow the existing design system
- [x] Test manually: upload a master CV JSON, configure search preferences, verify data persists across page reloads
- [x] Run `cd ui && npm run check` — must pass before task 7

### Task 7: Verification + documentation

- [ ] Wipe existing SQLite database (`rm data/jobs.db`) and verify clean startup with new schema
- [ ] Manual end-to-end test: register new user via magic link → upload master CV in settings → configure search preferences → submit a job via Generate page → see it in Review queue → approve/decline → verify only this user's jobs appear
- [ ] Manual test: register a second user → verify jobs are isolated between users
- [ ] Run full test suite: `uv run pytest tests/ -x -q`
- [ ] Run linter: `uv run ruff check src/`
- [ ] Run type check: `cd ui && npm run check`
- [ ] Update `CLAUDE.md`: document User entity, auth flow, per-user data ownership, new API endpoints, new settings, new dependencies
- [ ] Move this plan to `docs/plans/completed/`
