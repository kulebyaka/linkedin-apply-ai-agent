# Claude.md - LinkedIn Job Application Agent

This document provides context for Claude Code (or any AI assistant) to effectively work with this codebase.

**IMPORTANT**: The `implementation-plan.md` file is the **source of truth** for all functional and non-functional requirements, architecture decisions, and design specifications. Always refer to it when making architectural decisions or implementing features.

## Project Overview

**LinkedIn Job Application Agent** is an intelligent automation system that:
- Supports multiple users with magic-link email authentication (via Resend.com)
- Fetches job postings from LinkedIn hourly (per-user search preferences)
- Uses LLM to filter jobs and detect hidden disqualifiers
- Tailors CV for each job using AI (per-user master CV stored in DB)
- Generates professional PDF resumes
- Automates LinkedIn job applications via browser automation
- Implements Human-in-the-Loop (HITL) approval with Tinder-like UI
- Supports multiple LLM providers (OpenAI, DeepSeek, Grok, Anthropic)

## Architecture

### Core Technology Stack
- **Workflow Orchestration**: LangGraph (state machine for agent workflow)
- **Backend Framework**: FastAPI (for HITL UI API)
- **Authentication**: Magic link via Resend.com + JWT (httpOnly cookie)
- **Browser Automation**: Playwright
- **Data Validation**: Pydantic v2
- **PDF Generation**: WeasyPrint + Jinja2
- **LLM Integration**: Multi-provider support (OpenAI, Anthropic, DeepSeek, Grok)
- **Auth Dependencies**: `resend` (email sending), `pyjwt` (JWT tokens)

### Directory Structure

```
src/
├── context.py                  # AppContext DI container (replaces module globals)
├── agents/                     # LangGraph workflow definitions (async-native)
│   ├── _shared.py              # Shared workflow utilities (LLM init, CV compose, PDF gen)
│   ├── preparation_workflow.py # Main pipeline: job → CV → PDF → DB
│   ├── application_workflow.py # Deterministic Easy Apply over the WS bridge (no LLM)
│   ├── dispatcher.py           # WorkflowDispatcher: track + recover preparation/retry/application runs
│   └── retry_workflow.py       # Re-compose CV with user feedback
├── bridge/                     # WebSocket bridge to the Chrome extension actuator
│   ├── session_store.py        # user_id → WebSocket registry (newest wins, asyncio.Lock)
│   └── ws_relay.py             # WsRelay: JWT auth, RPC correlation, timeout/disconnect handling
├── llm/                        # LLM provider integrations
│   └── provider.py             # Abstract base + provider implementations
├── services/                   # Business logic services (grouped by domain)
│   ├── auth/                   # Authentication & user management
│   │   ├── auth.py             # AuthService: magic link + JWT authentication
│   │   └── user_repository.py  # UserRepository: user CRUD + search prefs + magic links
│   ├── cv/                     # CV composition, validation & PDF generation
│   │   ├── cv_composer.py      # LLM-powered CV tailoring
│   │   ├── cv_validator.py     # CV validation with configurable hallucination policy
│   │   ├── cv_prompts.py       # CV composition prompts + PromptLoader
│   │   └── pdf_generator.py    # PDF generation from JSON (WeasyPrint)
│   ├── db/                     # Persistence layer (Piccolo ORM + repository)
│   │   ├── job_repository.py   # Data access layer (in-memory + SQLite via Piccolo)
│   │   ├── tables.py           # Piccolo ORM table definitions
│   │   └── piccolo_app.py      # Piccolo app config for migrations
│   ├── jobs/                   # Job pipeline, queue, filter, scheduling, HITL
│   │   ├── job_orchestrator.py # Domain service: job submission & status queries
│   │   ├── hitl_processor.py   # Domain service: HITL decision processing
│   │   ├── job_filter.py       # LLM-based job filtering with two-threshold routing
│   │   ├── job_source.py       # Job source adapters (URL, manual, LinkedIn)
│   │   ├── job_queue.py        # Async job queue + ConsumerManager for lifecycle
│   │   ├── job_fixtures.py     # Record/replay scraped jobs for testing
│   │   └── scheduler.py        # APScheduler-based per-user LinkedIn search scheduler
│   └── linkedin/               # LinkedIn scraping, browser automation & Easy Apply
│       ├── browser_automation.py # Playwright stealth browser with cookie auth
│       ├── linkedin_scraper.py # LinkedIn job search results scraper
│       ├── linkedin_search.py  # LinkedIn search URL builder + filters
│       ├── easy_apply_selectors.py # Ported AutoApplyMax selectors + daily-limit/Done patterns
│       ├── field_classifier.py # Multilingual label→profile-value classifier (Unknown = abort)
│       └── apply_bridge.py     # ApplyBridge: deterministic per-field tools over WsRelay
├── models/                     # Pydantic data models
│   ├── job.py                  # Job posting models
│   ├── cv.py                   # CV data models
│   ├── cv_attempt.py           # CVCompositionAttempt for retry history tracking
│   ├── job_filter.py           # FilterResult + UserFilterPreferences models
│   ├── state_machine.py        # BusinessState + WorkflowStep enums, transition validation
│   ├── unified.py              # Unified models for two-workflow architecture
│   └── user.py                 # User, auth, and search preference models
├── api/                        # FastAPI endpoints (thin adapters)
│   └── main.py                 # REST API — delegates to domain services
├── config/                     # Configuration
│   └── settings.py             # Pydantic settings with env vars
└── utils/                      # Utilities
    └── logger.py               # Logging setup

extension/                      # Chrome MV3 extension (dumb DOM actuator)
├── manifest.json               # MV3; on-demand injection, externally_connectable for /extension-auth
├── background.js               # WS bridge to /ws/extension, JWT auth, routes RPC to LinkedIn tab
├── content_script.js           # DOM primitives: serialize_form, fill_field, upload, click, discard
└── popup/                      # Connection status + Pause/Resume + last-apply result

data/
├── cv/                         # Legacy master CV location (now stored in User DB record)
├── jobs/                       # Fetched job data
└── generated_cvs/              # Tailored CV PDFs (per-user: {user_id}/{job_id}.pdf)

prompts/
└── job_filter/
    ├── default_filter_prompt.txt       # Default LLM filter prompt template
    └── generate_prompt_from_prefs.txt  # Meta-prompt: natural language → filter prompt
```

## Two-Workflow Pipeline Architecture

The system uses a **two-workflow pipeline** split at the HITL boundary, enabling batch review of generated CVs.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PREPARATION WORKFLOW                                 │
│  (runs continuously, processes jobs, saves to DB for batch review)          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Job Source ──► Extract ──► Filter ──► Compose CV ──► Generate PDF ──► DB │
│   (URL/Manual)                                                    (pending) │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                        ┌───────────────────────┐
                        │    HITL BOUNDARY      │
                        │  (Tinder-like batch   │
                        │   review UI)          │
                        │                       │
                        │  ✓ Approve            │
                        │  ✗ Decline            │
                        │  ↻ Retry + feedback   │
                        └───────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│ APPLICATION WORKFLOW│  │  RETRY WORKFLOW     │  │      DECLINED       │
│ (triggered on       │  │  (regenerate CV     │  │   (no action)       │
│  approve)           │  │   with feedback)    │  │                     │
├─────────────────────┤  ├─────────────────────┤  └─────────────────────┘
│ Load ──► Apply ──►  │  │ Load ──► Compose    │
│          Update DB  │  │   ──► PDF ──►       │
│                     │  │      Update DB      │
│ (stubs only -       │  │                     │
│  deep agent future) │  │ (loops back to      │
│                     │  │  HITL pending)      │
└─────────────────────┘  └─────────────────────┘
```

### Workflow Modes

- **MVP Mode** (`mode="mvp"`): Generate PDF only, skip HITL, status = `completed`
- **Full Mode** (`mode="full"`): Generate PDF, save to DB with status = `pending` for HITL review

### Workflow Files

| Workflow | File | Description |
|----------|------|-------------|
| Preparation | `src/agents/preparation_workflow.py` | Main pipeline: job input → CV PDF → DB |
| Retry | `src/agents/retry_workflow.py` | Re-compose CV with user feedback |
| Application | `src/agents/application_workflow.py` | Deterministic Easy Apply over the WS bridge (no LLM) |
| Shared | `src/agents/_shared.py` | Common utilities: LLM init, CV compose, PDF gen, master CV loading |

## Key Design Patterns

### 1. Dependency Injection via AppContext
- `src/context.py` defines a single `AppContext` dataclass holding all shared dependencies
- Created once at startup via `create_app_context()` and stored in `app.state.ctx`
- Includes `user_repository: UserRepository` and `auth_service: AuthService`
- No module-level globals — all dependencies are explicit and injected
- Workflow nodes receive `repository` via LangGraph's `config["configurable"]` dict
- Domain services (`JobOrchestrator`, `HITLProcessor`) receive the full `AppContext`

### 2. LangGraph Workflows (Async-Native)
- All workflow node functions are `async def` — use `await` directly, no `asyncio.run()` hacks
- Invoked via `workflow.ainvoke()` / `workflow.astream()`
- Shared logic extracted to `src/agents/_shared.py` to eliminate duplication
- State management with TypedDict classes
- Repository passed via `config["configurable"]["repository"]`

### 3. Job Lifecycle State Machine
- `src/models/state_machine.py` defines `BusinessState` and `WorkflowStep` enums
- `BusinessState`: queued → processing → cv_ready/pending_review → approved/declined/retrying → applied/failed; also `filtered_out` (terminal, reachable from queued/processing)
- `WorkflowStep`: transient step tracking (extracting, composing_cv, generating_pdf, etc.)
- `ALLOWED_TRANSITIONS` map enforces valid state changes; raises `InvalidStateTransitionError` on violations
- Both `InMemoryJobRepository` and `SQLiteJobRepository` validate transitions on `update()`

### 4. Domain Services (Thin API Handlers)
- `JobOrchestrator`: job submission, status queries, workflow dispatch
- `HITLProcessor`: approve/decline/retry decisions, pending retrieval, history
- API endpoints are thin adapters — extract context, call service, return result

### 5. Multi-LLM Support
- Factory pattern for provider instantiation (`LLMClientFactory`)
- Abstract `BaseLLMClient` interface
- Easy switching via environment variables
- Fallback support for reliability

### 6. Repository Pattern
- `JobRepository` abstract interface for data persistence
- `InMemoryJobRepository` (with `asyncio.Lock` for thread safety) for development
- `SQLiteJobRepository` via Piccolo ORM for production
- Supports `CVCompositionAttempt` tracking for retry history

### 7. CV Validation (Extracted from Composer)
- `CVValidator` in `src/services/cv_validator.py` handles hallucination checks
- Configurable `HallucinationPolicy`: STRICT (raises), WARN (logs), DISABLED (skips)
- `CVComposer` delegates validation to `CVValidator` after composition

### 8. Job Source Adapters
- Abstract interface in `src/services/job_source.py`
- Adapters for URL extraction, manual input, LinkedIn API
- Factory pattern: `JobSourceFactory.get_adapter(source)`

### 9. Multi-User Authentication
- Magic link flow: user enters email → `AuthService` generates token → sends email via Resend.com → user clicks link → JWT cookie set
- `AuthService` in `src/services/auth.py`: magic link generation/verification, JWT creation/decoding
- `UserRepository` in `src/services/user_repository.py`: user CRUD, magic link storage, search preferences
- Open registration: first login auto-creates user account
- JWT stored in httpOnly cookie (`auth_token`), 30-day expiry
- FastAPI dependencies: `get_current_user` (401 on missing auth), `get_optional_user` (returns None)
- All job data is user-scoped: `user_id` FK on `Job` and `CVAttemptTable`

### 10. Per-User Data Ownership
- `JobRecord` includes `user_id` field — all list queries filter by user
- Master CV stored as JSON in `UserTable.master_cv_json` (loaded from DB, not filesystem)
- Search preferences stored as JSON in `UserTable.search_preferences`
- PDF output stored in per-user directories: `data/generated_cvs/{user_id}/{job_id}.pdf`
- Scheduler iterates all users with configured search preferences, runs separate LinkedIn searches per user
- `JobRepository.get_for_user(job_id, user_id)` enforces ownership verification

### 11. Admin & Roles
- `UserRole` enum (`src/models/user.py`): `trial`, `premium`, `admin`. Default is `trial`; the enum is the extension point for future tiers.
- Persisted as a `role` `Varchar(20)` column on `UserTable` with an index; new sign-ups land in `trial`. `UserRepository.initialize()` runtime-migrates older DBs by adding the column and defaulting existing rows to `"trial"` (same pattern as `filter_preferences`).
- `UserRepository` exposes `set_role(user_id, role)` and `list_all_users(limit, offset)` for admin operations.
- Authorization in the API uses two layered dependencies in `src/api/main.py`:
  - `get_current_user` — extracts the JWT and 401s when missing.
  - `get_admin_user` — depends on `get_current_user` and raises `HTTPException(403)` when `user.role != "admin"`. Type alias: `AdminUser = Annotated[User, Depends(get_admin_user)]`.
- Admin-scope repository methods are additive on top of the user-scoped ones: `list_all_jobs`, `count_all_jobs`, `count_by_status_global`, `list_jobs_with_errors`, `delete`. User-scoped methods (`list_for_user`, `get_for_user`, etc.) remain the default path for non-admin callers.
- Bootstrapping the first admin: `uv run python scripts/promote_user.py --email you@example.com --role admin`. The same script supports `--role trial|premium|admin` and `--list-admins`.
- Last-admin guard: `PUT /api/admin/users/{user_id}/role` refuses (409) to demote yourself when you are the only remaining admin. The UI mirrors this guard, but the server-side check is authoritative.
- Frontend: `ui/src/routes/admin/+layout.svelte` redirects to `/` when `authStore.isAdmin` is false. The auth store reads `role` from `/api/auth/me` and exposes `isAdmin` as a `$derived` value.

#### Important Notes about strict schema support
- **OpenAI**: Requires GPT-4 or newer models for strict schema support
- **Anthropic**: Requires beta header `anthropic-beta: structured-outputs-2025-11-13` (already configured)
- **Grok**: Works with all models after grok-2-1212
- **DeepSeek**: Does NOT support strict schemas - validates after generation

See `src/llm/provider.py` module documentation for detailed implementation.

## Important Implementation Details

### Preparation Workflow Nodes
1. **extract_job_node**: Extracts structured job data from source (URL/manual/LinkedIn)
2. **filter_job_node**: LLM evaluates job suitability (LinkedIn only); scores 0-100, hard rejects go to `save_filtered_out_node`, warnings surfaced in HITL review
3. **save_filtered_out_node**: Persists a minimal `JobRecord` with status=`filtered_out` for LLM-rejected jobs; terminal — workflow ends here
4. **compose_cv_node**: LLM tailors CV to job description
5. **generate_pdf_node**: Creates PDF from tailored CV JSON
6. **save_to_db_node**: Persists job record (MVP: completed, Full: pending)

### Retry Workflow Nodes
1. **load_from_db_node**: Loads job record for retry
2. **compose_cv_node**: Re-composes CV with user feedback
3. **generate_pdf_node**: Regenerates PDF
4. **update_db_node**: Updates record, returns to pending status

### Application Workflow Nodes (Deterministic Easy Apply, no LLM)
The apply workflow drives the LinkedIn Easy Apply modal field-by-field over the WebSocket
bridge (`ApplyBridge` → `WsRelay` → extension content script). No LLM is involved; field
values come straight from the user's `ApplyProfile` + CV `ContactInfo` via `field_classifier`.
1. **open_easy_apply_node**: Navigate + click Easy Apply (handles the safety-reminder modal); verify the modal opened. On `BridgeDisconnected`/`ExtensionUnavailable` → `needs_extension`.
2. **fill_step_node**: Loop (max 10 steps). `read_form_state` → if any `unknown_fields` (unrecognized question, or recognized field with no profile value) → `discard` + `manual_required` (never guess). Otherwise `upload_file` for file inputs + `fill_field` per `fill_plan`, then `advance_step`. Unfixable validation errors → `discard` + `manual_required`. Daily-limit flag → stop without submit. Submit button present → go to submit.
3. **submit_node**: `submit_form` (un-follow company, click Submit, find-and-click Done, capture confirmation). `confirmed` → `applied`, else `failed`.
4. **finalize_node**: Persists the terminal state (`APPLIED` + application_url + saved confirmation screenshot, `MANUAL_REQUIRED` + reason, `NEEDS_EXTENSION`, or `FAILED` + error), respecting `ALLOWED_TRANSITIONS`.

Cross-cutting: per-app wall-clock timeout (`apply_per_app_timeout_seconds`) → discard + fail; mid-apply bridge drop → `needs_extension`.

### Master CV Format
- Stored as JSON in each user's DB record (`UserTable.master_cv_json`)
- Uploaded via Settings UI or API (`PUT /api/users/me`)
- Schema defined in `src/models/cv.py`
- Contains comprehensive work history, skills, projects
- LLM recomposes relevant portions for each job
- Loaded from user record and passed via workflow state (`master_cv` key)

## API Endpoints

### Authentication (public)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/login` | Request magic link email |
| GET | `/api/auth/verify?token=...` | Verify magic link, set JWT cookie |
| GET | `/api/auth/me` | Get current authenticated user |
| POST | `/api/auth/logout` | Clear auth cookie |

### User Settings (requires auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| PUT | `/api/users/me` | Update user profile (display_name, master_cv_json, search_preferences, apply_profile, auto_apply) |
| GET | `/api/auth/extension-token` | Mint a short-lived JWT for the Chrome extension (session JWT is httpOnly; `/extension-auth` posts this to the extension) |
| GET | `/api/users/me/search-preferences` | Get current search preferences |
| PUT | `/api/users/me/search-preferences` | Update search preferences |
| GET | `/api/users/me/filter-preferences` | Get current filter preferences |
| PUT | `/api/users/me/filter-preferences` | Update filter preferences |
| POST | `/api/users/me/filter-preferences/generate-prompt` | Generate filter prompt from natural language description |

### Jobs & HITL (requires auth, user-scoped)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/jobs/submit` | Submit job for CV generation (URL or manual input) |
| GET | `/api/jobs/{job_id}/status` | Get job status and details |
| GET | `/api/jobs/{job_id}/pdf` | Download generated CV PDF |
| GET | `/api/jobs/{job_id}/html` | Get generated CV as HTML |
| GET | `/api/hitl/pending` | Get all jobs pending HITL review |
| POST | `/api/hitl/{job_id}/decide` | Submit HITL decision (approve/decline/retry) — approve dispatches the apply (or sets `needs_extension`) |
| POST | `/api/jobs/{job_id}/apply` | (Re-)trigger Easy Apply for a job in `needs_extension`/`approved` — used by the "Apply now" button after connecting the extension |
| GET | `/api/hitl/history` | Get application history |
| DELETE | `/api/jobs/cleanup` | Clean up old job records |

### Extension Bridge (auth via JWT in first WS frame)

| Method | Endpoint | Description |
|--------|----------|-------------|
| WS | `/ws/extension` | Chrome extension WebSocket; first frame `{"type":"auth","token":...}`, then JSON-RPC tool calls relayed to the LinkedIn tab |

### System (public)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/jobs/linkedin-search` | Trigger LinkedIn job search manually |
| GET | `/api/jobs/linkedin-search/status` | Get scheduler state and last run info |
| GET | `/api/health` | Health check (includes queue consumer status) |

### Admin (requires `role == "admin"`)

All routes depend on `get_admin_user`, which raises 403 for non-admin callers.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/admin/jobs` | Paged, filterable list of jobs across all users (filters: `user_id`, `status`, `source`, `created_from`, `created_to`, `search`, `limit`, `offset`) |
| GET | `/api/admin/jobs/{job_id}` | Full job detail for any user |
| POST | `/api/admin/jobs/{job_id}/retry` | Re-enqueue a `failed` job (409 otherwise) |
| DELETE | `/api/admin/jobs/{job_id}` | Delete a job record + associated PDF (best-effort) |
| POST | `/api/admin/jobs/bulk-delete` | Delete up to 100 jobs by id list |
| GET | `/api/admin/queue` | Consumer snapshot + scheduler state + global status counts (24h / 7d) |
| POST | `/api/admin/scheduler/run/{user_id}` | Manually fire the LinkedIn search for a specific user |
| GET | `/api/admin/errors` | Paged list of jobs whose `error_message` or `last_scrape_error` is non-null |
| GET | `/api/admin/users` | Paged user list with derived per-status job counts and `last_job_at` |
| PUT | `/api/admin/users/{user_id}/role` | Change a user's role; refuses to demote the last admin (409) |

## Data Models

### User & Auth Models (`src/models/user.py`)

- `UserRole` - Enum of role values: `TRIAL = "trial"`, `PREMIUM = "premium"`, `ADMIN = "admin"`. Extensible.
- `User` - User entity: id, email, display_name, `role` (`UserRole`, default `trial`), master_cv_json, search_preferences, filter_preferences, `apply_profile` (`ApplyProfile | None`), `auto_apply` (bool, default False), timestamps
- `ApplyProfile` - Structured answers for Easy Apply screening fields (all optional; absence = "unknown" → abort to `manual_required`): `phone_country_code`, `years_experience`, `expected_salary`, `needs_visa_sponsorship`, `legally_authorized`, `willing_to_relocate`, `drivers_license`. Helper `is_complete_for(required_kinds)` used by the classifier/abort logic.
- `LoginRequest` - Email input for magic link request
- `LoginResponse` - Success message after magic link sent
- `VerifyRequest` - Token for magic link verification
- `AuthResponse` - User object + message after successful auth
- `UserUpdateRequest` - Optional fields for profile update (display_name, master_cv_json, search_preferences, filter_preferences, apply_profile, auto_apply)
- `UserSearchPreferences` - Mirrors LinkedInSearchParams: keywords, location, remote_filter, date_posted, experience_level, job_type, easy_apply_only, max_jobs

### Core Models (`src/models/unified.py`)

- `JobSubmitRequest` - Input for job submission (source, mode, url/job_description)
- `JobSubmitResponse` - Response with job_id and status
- `HITLDecision` - User decision (approved/declined/retry + feedback + reasoning)
- `HITLDecisionResponse` - Response after decision processed
- `PendingApproval` - Job details for HITL review UI (includes `filter_result` for score badge display)
- `JobStatusResponse` - Full job status with CV and PDF info
- `JobRecord` - Database record (includes `user_id` for ownership, `filter_result` for LLM filter output)
- `ApplicationHistoryItem` - History entry for completed jobs

### State Machine (`src/models/state_machine.py`)

- `BusinessState` - Job lifecycle states: queued, processing, cv_ready, pending_review, approved, declined, retrying, applying, applied, failed, filtered_out, needs_extension, manual_required
  - `filtered_out`: terminal state for LLM-rejected jobs (score below reject threshold or hard disqualifier); reachable from `queued` and `processing`
  - `needs_extension`: recoverable state when an apply fires but no extension WebSocket is connected (or it drops mid-apply); user re-triggers via `POST /api/jobs/{id}/apply`. Transitions to `applying` or `failed`.
  - `manual_required`: terminal state when the apply hits an unrecognized/unanswerable field; the modal is discarded cleanly and the user finishes manually on LinkedIn.
  - `auto_apply` save path: `queued`/`processing` may go straight to `approved` (skipping HITL) when `user.auto_apply` is set.
- `WorkflowStep` - Transient step tracking: extracting, filtering, composing_cv, generating_pdf, etc.
- `ALLOWED_TRANSITIONS` - Valid state change map, enforced by repository
- `InvalidStateTransitionError` - Raised on illegal transitions

### CV Attempt History (`src/models/cv_attempt.py`)

- `CVCompositionAttempt` - Tracks each CV composition: attempt_number, user_feedback, cv_json, pdf_path

### Job Filter Models (`src/models/job_filter.py`)

- `FilterResult` - LLM filter output: score (0-100), red_flags (list), disqualified (bool), disqualifier_reason (str|None), reasoning (str)
- `UserFilterPreferences` - Per-user filter config: natural_language_prefs, custom_prompt, reject_threshold (default 30), warning_threshold (default 70), enabled (bool)

## Implementation Status

| Component | Status | Notes |
|-----------|--------|-------|
| **Multi-User Auth** | ✅ Complete | `src/services/auth.py` - magic link + JWT, `src/services/user_repository.py` - user CRUD |
| **User Models** | ✅ Complete | `src/models/user.py` - User, auth, search preferences models |
| **Per-User Data Ownership** | ✅ Complete | user_id FK on all job data, per-user CV storage and search prefs |
| **Per-User Search Scheduler** | ✅ Complete | `src/services/scheduler.py` - iterates users with search preferences |
| **Frontend Auth Flow** | ✅ Complete | Login, magic link verify, protected routes, auth state store |
| **Settings UI** | ✅ Complete | Profile editing, CV upload (JSON), search preferences configuration |
| **AppContext DI** | ✅ Complete | `src/context.py` - includes UserRepository + AuthService |
| **Async-Native Workflows** | ✅ Complete | All workflow nodes are `async def`, use `ainvoke()` |
| **Shared Workflow Utils** | ✅ Complete | `src/agents/_shared.py` - deduplicated across 3 workflows |
| **Job Lifecycle State Machine** | ✅ Complete | `src/models/state_machine.py` - BusinessState + WorkflowStep + transition validation |
| **Domain Services** | ✅ Complete | `JobOrchestrator` + `HITLProcessor` - thin API handlers, user-scoped |
| **CV Validator** | ✅ Complete | `src/services/cv_validator.py` - configurable hallucination policy |
| **CV Attempt History** | ✅ Complete | `src/models/cv_attempt.py` + repository methods |
| **Consumer Manager** | ✅ Complete | `src/services/job_queue.py` - resilient queue consumer lifecycle |
| **LLM Provider Layer** | ✅ Complete | `src/llm/provider.py` |
| **Preparation Workflow** | ✅ Complete | `src/agents/preparation_workflow.py` |
| **Retry Workflow** | ✅ Complete | `src/agents/retry_workflow.py` |
| **Compose Tailored CV** | ✅ Complete | `src/services/cv_composer.py` |
| **Generate PDF** | ✅ Complete | `src/services/pdf_generator.py` (WeasyPrint + Jinja2) |
| **HITL API Endpoints** | ✅ Complete | `src/api/main.py` (thin adapters to domain services) |
| **Unified Data Models** | ✅ Complete | `src/models/unified.py` |
| **Job Repository (DAL)** | ✅ Complete | `src/services/job_repository.py` (in-memory + SQLite, user-scoped queries) |
| **Job Source Adapters** | ✅ Complete | `src/services/job_source.py` - LinkedIn adapter with field mapping |
| **Browser Automation** | ✅ Complete | `src/services/browser_automation.py` - stealth Playwright with cookie auth |
| **LinkedIn Job Scraper** | ✅ Complete | `src/services/linkedin_scraper.py` - search results parser with dedup |
| **LinkedIn Search Builder** | ✅ Complete | `src/services/linkedin_search.py` - URL builder with filter models |
| **Async Job Queue** | ✅ Complete | `src/services/job_queue.py` - queue with ConsumerManager, user_id tagging |
| **LinkedIn Search Scheduler** | ✅ Complete | `src/services/scheduler.py` - per-user search with APScheduler |
| **HITL Frontend UI** | ✅ Complete | Svelte 5 SPA with Tinder-like review interface |
| **Application Workflow** | ✅ Complete | `src/agents/application_workflow.py` — deterministic Easy Apply (no LLM); aborts to `manual_required` on unknown fields |
| **WebSocket Bridge** | ✅ Complete | `src/bridge/` — `SessionStore` + `WsRelay` (JWT auth, RPC correlation, timeout/disconnect handling) |
| **Chrome MV3 Extension** | ✅ Complete | `extension/` — DOM actuator (on-demand inject, content-script primitives, popup, `/extension-auth` flow) |
| **Easy Apply Selectors** | ✅ Complete | `src/services/linkedin/easy_apply_selectors.py` — ported AutoApplyMax selectors + daily-limit/Done patterns |
| **Field Classifier** | ✅ Complete | `src/services/linkedin/field_classifier.py` — multilingual label→profile-value matching; `Unknown` ⇒ abort (no guessing) |
| **Apply Bridge** | ✅ Complete | `src/services/linkedin/apply_bridge.py` — deterministic per-field tools (MCP-wrap-ready for the LLM sprint) |
| **Apply Triggers** | ✅ Complete | `src/services/jobs/apply_trigger.py` — HITL approve + `auto_apply` save-path dispatch; `needs_extension` fail-fast |
| **Job Filter (LLM)** | ✅ Complete | `src/services/job_filter.py` — two-threshold routing, hidden disqualifier detection, per-user prompt, HITL badge |
| **Admin Role & Admin Page** | ✅ Complete | `UserRole` enum + `role` column, `get_admin_user` dependency, `/api/admin/*` endpoints, `/admin` UI (jobs / queue / errors / users), `scripts/promote_user.py` CLI |

## Development Guidelines

### Testing Strategy

- Unit tests for each service class
- Integration tests for workflow
- Mock LLM responses for determinism
- Playwright tests for browser automation
- API endpoint tests with TestClient
- HITL E2E tests (`tests/e2e/test_hitl_review.py`): Full Playwright tests for the HITL review UI covering approve/decline/retry flows, PDF download, CV preview, and job description rendering. Servers are auto-started by fixtures. Run with: `pytest tests/e2e/test_hitl_review.py -v -m e2e`

### Configuration

All settings in `.env`:
- Credentials (LinkedIn, LLM APIs)
- Provider selection (primary/fallback)
- Paths and directories
- Workflow parameters (fetch interval, concurrency)
- API server settings
- **Authentication Configuration:**
  - `RESEND_API_KEY` - Resend.com API key for sending magic link emails
  - `JWT_SECRET` - Secret key for JWT token signing
  - `MAGIC_LINK_TTL_MINUTES=15` - Magic link token validity period
  - `JWT_EXPIRY_DAYS=30` - JWT session duration
  - `APP_URL=http://localhost:5173` - Base URL for magic link callback
  - `DEV_AUTH_BYPASS=true` + `DEV_AUTH_EMAIL=dev@local.test` - Local-only auth bypass for browser/UX testing. Exposes `POST /api/auth/dev-login` which mints a JWT cookie for the dev user without an email round-trip. Server refuses to start if true and `APP_URL` is non-localhost; route returns 404 when false. See `.claude/skills/web-browser/SKILL.md` for usage.
- **Repository Configuration:**
  - `REPO_TYPE=memory` (default) or `REPO_TYPE=sqlite` for persistent storage
  - `DB_PATH=./data/jobs.db` (SQLite database path)
- **LinkedIn Search Configuration:**
  - `LINKEDIN_SEARCH_KEYWORDS`, `LINKEDIN_SEARCH_LOCATION` - fallback search filters (used when no users have configured preferences)
  - `LINKEDIN_SEARCH_REMOTE_FILTER` - "remote", "on-site", "hybrid"
  - `LINKEDIN_SEARCH_SCHEDULE_ENABLED=false` - enable hourly scheduled searches
  - `LINKEDIN_SEARCH_INTERVAL_HOURS=1` - search frequency
  - `LINKEDIN_SESSION_COOKIE_PATH=./data/linkedin_cookies.json` - cookie persistence
- **Job Filter Configuration:**
  - `JOB_FILTER_ENABLED=true` - enable LLM-based job filtering globally
  - `JOB_FILTER_REJECT_THRESHOLD=30` - jobs scoring below this are saved as `filtered_out` (skips CV generation)
  - `JOB_FILTER_WARNING_THRESHOLD=70` - jobs scoring below this show warning badge + red flags in HITL review
- **Easy Apply / Extension Bridge Configuration:**
  - `EASY_APPLY_ENABLED=true` - feature flag for deterministic Easy Apply automation
  - `APPLY_PER_APP_TIMEOUT_SECONDS=180` - wall-clock budget for a single application (else discard + fail)
  - `APPLY_STUCK_TIMEOUT_SECONDS=120` - no-progress watchdog within an application
  - `APPLY_RPC_TIMEOUT_SECONDS=30` - per-RPC timeout over the WS bridge
  - `APPLY_DAILY_LIMIT_DETECTION=true` - stop (do not retry) on LinkedIn daily-limit messages
  - `EXTENSION_ID` - Chrome-assigned unpacked-extension id; the MV3 `externally_connectable` block lists the app origin and `/extension-auth` targets this id when handing over the JWT

**Never commit `.env` or real CV data to git!**

## Common Tasks

### Adding a New LLM Provider

1. Create provider class in `src/llm/provider.py`
2. Add to `LLMProvider` enum
3. Implement API integration with native structured output support:
   - Research if provider supports JSON Schema enforcement
   - Implement strict schema mode in `generate_json()` if available
   - Fall back to `json_object` mode + manual validation if not
4. Register in factory
5. Add config to `settings.py`
6. Document structured output capabilities in provider.py docstring
7. Document in README

### Modifying CV Tailoring Logic

1. Update prompts in `src/services/cv_prompts.py`
2. Adjust `CVComposer` methods in `src/services/cv_composer.py`
3. Validation logic is in `src/services/cv_validator.py` — update `CVValidator` if changing what gets checked
4. Test with various job descriptions
5. Consider adding user feedback loop

### Adding New Workflow Step

1. If the logic is shared across workflows, add it to `src/agents/_shared.py`
2. Define `async def` node function in the appropriate workflow file — receive repository via `config["configurable"]["repository"]`
3. Add node to workflow graph
4. Update state TypedDict if needed
5. Use `BusinessState` and `WorkflowStep` enums from `src/models/state_machine.py` for status updates
6. If adding a new state, update `ALLOWED_TRANSITIONS` in `state_machine.py`
7. Add routing logic
8. Update tests

### Debugging Workflow Issues

1. Check logs (configured in `src/utils/logger.py`)
2. Inspect workflow state at each node
3. Use LangGraph visualization tools
4. Test nodes individually before integration

## Next Steps

The deterministic, no-LLM Easy Apply happy path is **shipped** (WS bridge + Chrome extension + field classifier + apply workflow). Remaining work is the deferred **LLM sprint**:

1. **LLM form-fill agent** - wrap the existing `ApplyBridge` tools with `create_sdk_mcp_server`/`@tool` (the WS protocol is already MCP-wrap-ready) and drive non-deterministic screening questions instead of aborting to `manual_required`.
2. **Vision fallback** - screenshot-driven field extraction for non-LinkedIn ATS (the `take_screenshot` primitive exists; only used for confirmation capture today).
3. **Placeholder / PII substitution** - swap real values for tokens before tool results reach the LLM + Langfuse traces.
4. **Post-submission notifications** - email/Telegram with the confirmation screenshot for `auto_apply` runs.

See `docs/plans/completed/easy-apply-happy-path.md` ("Out of Scope") and `docs/plans/ARCHITECTURE-browser-agent.md` for details.

## Reference Implementations

The `Obsolete/` directory contains **two production-ready projects** that serve as valuable reference implementations:

### 1. **Auto_job_applier_linkedIn** (GodsScion)
- **Status:** Production-ready, actively maintained
- **Architecture:** Selenium-based web automation with AI integration
- **Key Features:**
  - Web scraping with undetected-chromedriver (stealth mode)
  - Multi-LLM support (OpenAI, DeepSeek, Gemini)
  - Intelligent form filling with AI-powered question answering
  - Application history tracking (CSV + Flask web UI)
  - Comprehensive configuration system (5 config files)
  - Robust error handling and logging
- **Useful Components:**
  - `modules/clickers_and_finders.py` - Reusable Selenium utilities
  - `modules/ai/` - Multi-provider AI integration patterns
  - `modules/validator.py` - Configuration validation framework
  - `app.py` - Flask-based application history viewer
- **Documentation:** See `Obsolete/Auto_job_applier_linkedIn/ARCHITECTURE.md` for detailed analysis

### 2. **Jobs_Applier_AI_Agent_AIHawk** (AIHawk)
- **Status:** Production-ready, featured in major media (Business Insider, TechCrunch, The Verge, Wired)
- **Architecture:** LangChain-based with FAISS vector search
- **Key Features:**
  - Semantic job parsing using vector embeddings
  - LLM-powered resume tailoring (section-by-section generation)
  - Professional PDF generation via Chrome DevTools Protocol
  - Multi-LLM support (OpenAI, Claude, Gemini, HuggingFace, Ollama, Perplexity)
  - Pydantic-based type-safe data models
  - Customizable resume styling
- **Useful Components:**
  - `src/llm_manager.py` - Factory pattern for multi-LLM support
  - `src/resume_facade.py` - Facade pattern for resume generation
  - `src/llm_job_parser.py` - Semantic job description extraction
  - `src/utils/chrome_utils.py` - CDP-based PDF generation
  - `resume_schemas/` - Pydantic models for type safety
- **Documentation:** See `Obsolete/Jobs_Applier_AI_Agent_AIHawk/ARCHITECTURE.md` for comprehensive analysis

## Quick Start

Run both API and UI with one command (kills previous instances automatically):

```bash
# Windows PowerShell
.\scripts\dev.ps1
```

Then open:
- **UI**: http://localhost:5173 (Vite dev server with HMR)
- **API**: http://localhost:8000 (FastAPI with auto-reload on .py changes)

## Useful Commands

```bash
# Development
python -m uvicorn src.api.main:app --reload  # Start API server only
cd ui && npm run dev                          # Start UI dev server only
pytest                                        # Run tests
black src/                                   # Format code
mypy src/                                    # Type check

# Docker
docker-compose up -d                         # Start services
docker-compose logs -f                       # View logs
docker-compose down                          # Stop services
```

## Troubleshooting

### Common Issues

**Import errors**
- Ensure virtual environment is activated
- Check PYTHONPATH includes project root
- Verify all dependencies installed

**LLM API errors**
- Check API keys in `.env`
- Verify quota/billing on provider
- Test with simple API call first

## Security Notes

- **Never commit** `.env` or actual CV data
- **Secure storage** for LinkedIn credentials
- **Rate limiting** for API calls
- **User data** stays on self-hosted VPS

## Resources

- [LangGraph Documentation](https://python.langchain.com/docs/langgraph)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [WeasyPrint Documentation](https://doc.courtbouillon.org/weasyprint/)
