# Implementation Plan & Status

This document is the **source of truth** for requirements, architecture, and design decisions. It
describes the system **as it is implemented today**, and marks explicitly what is not built.
Per-feature specs live in `docs/plans/`; anything under `docs/plans/completed/` is historical record
and may describe an earlier shape of the code.

## Functional requirements

Every hour, based on a set of filters (in the future, filters will be built from the user's verbal
request), the system searches LinkedIn and returns a set of positions. An LLM evaluates each position
to determine whether it actually matches the request (the description may reveal that it is not
really remote, that a visa is required, etc.). Positions that pass the filter are matched against the
user's data set (a very comprehensive CV in JSON format). From the position description and the CV,
an LLM generates a new JSON representing a CV with information recomposed to be relevant to that
position. A PDF is created from the JSON. The agent then goes to LinkedIn and attempts to apply for
the position; if unsuccessful it notifies the user. At every step it should be able to leverage HITL
when there is uncertainty about the success of the current step — and HITL is mandatory on the last
step, to review the modified CV and the position before submitting. Review happens in batches, in a
Tinder-like UI where the user sees the job position plus the new CV and can approve (swipe right),
decline (swipe left, with a note on why) or ask to try again (swipe down, with a note on what to do
differently).

### Requirement coverage

| Requirement | State |
|---|---|
| Hourly filtered LinkedIn search | **Met** — per-user, APScheduler, opt-in via `LINKEDIN_SEARCH_SCHEDULE_ENABLED` |
| Filters from a verbal request | **Met for filtering** — natural-language preferences are compiled into a filter prompt by an LLM. Search *keywords/location* are still structured fields |
| LLM suitability evaluation with hidden disqualifiers | **Met** — score 0–100, red flags, hard-disqualifier flag |
| CV recomposition against the posting | **Met** |
| PDF from JSON | **Met** |
| Apply on LinkedIn | **Not met** — no application workflow exists. The user applies manually |
| Notify the user on failure | **Partially met** — persistent in-app notification centre + admin email alerts. No per-job failure webhook/Telegram yet |
| HITL at every uncertain step | **Deliberately narrowed** — HITL is a single batch gate after CV generation, not a per-step interrupt. Filter warnings and red flags surface *inside* that gate |
| HITL on the final step, Tinder-like, batch | **Met** |

## Non-functional requirements

- Self-hosted on a VPS — **met** (Docker Compose + Caddy, see `docs/deployment.md`)
- At least 3 LLM providers, easily switchable — **met** (4: OpenAI, Anthropic, DeepSeek, Grok; a
  single `InstructorClient` over LiteLLM, switchable per user *and* per operation)
- Modular design for easy updates — **met** (AppContext DI, repository pattern, adapter interfaces)
- Prefer Playwright for browser automation — **met** (scraping; applying is unbuilt)

## Implementation Status

| Component | Status | Where |
|---|---|---|
| **AppContext DI** | Complete | `src/context.py` — no module-level globals |
| **Async-native LangGraph workflows** | Complete | all nodes `async def`, invoked via `ainvoke()` |
| **Workflow dispatcher** | Complete | `src/agents/dispatcher.py` — single entry point for all workflow invocations |
| **Shared workflow utils** | Complete | `src/agents/_shared.py` |
| **Preparation workflow** | Complete | `src/agents/preparation_workflow.py` |
| **Retry workflow** | Complete | `src/agents/retry_workflow.py` |
| **Application workflow** | **Not implemented** | No module. Stubs were deleted rather than left to rot |
| **Job lifecycle state machine** | Complete | `src/models/state_machine.py` |
| **Domain services** | Complete | `src/services/jobs/job_orchestrator.py`, `hitl_processor.py` |
| **Repository pattern / DAL** | Complete | `src/services/db/` — in-memory + SQLite (Piccolo) |
| **Schema migrations** | Complete | `src/services/db/migrations.py` — idempotent, self-detecting |
| **CV composer** | Complete | `src/services/cv/cv_composer.py` |
| **CV validator** | Complete | `src/services/cv/cv_validator.py` — configurable hallucination policy |
| **CV attempt history** | Complete | `src/models/cv_attempt.py` + repository methods |
| **PDF generation** | Complete | `src/services/cv/pdf_generator.py` (WeasyPrint + Jinja2) |
| **Master CV from PDF** | Complete | `src/services/cv/pdf_extraction.py` — background LLM extraction + polling |
| **LLM provider layer** | Complete | `src/llm/providers/instructor_client.py` (see `src/llm/CLAUDE.md`) |
| **Dynamic model catalog** | Complete | `src/llm/model_catalog.py`, `pricing_source.py`, daily refresh scheduler |
| **Per-operation model choice** | Complete | `UserModelPreferences` — CV generation / filtering / prompt generation |
| **Job source adapters** | Complete | `src/services/jobs/job_source.py` — url, manual, linkedin |
| **Job filter (LLM)** | Complete | `src/services/jobs/job_filter.py` — two-threshold routing |
| **Auto-refining filter prompt** | Complete | `src/services/jobs/refinement.py` + `refinement_scheduler.py` |
| **LinkedIn search builder** | Complete | `src/services/linkedin/linkedin_search.py` |
| **LinkedIn scraper** | Complete | `src/services/linkedin/linkedin_scraper.py`, `detail_parser.py`, `selectors.py` |
| **Browser automation (stealth)** | Complete | `src/services/linkedin/browser_automation.py` — scraping only |
| **Async job queue** | Complete | `src/services/jobs/job_queue.py` + `ConsumerManager` |
| **Per-user search scheduler** | Complete | `src/services/jobs/scheduler.py` on `interval_scheduler.py` |
| **Scrape-failure recovery** | Complete | `SCRAPE_FAILED` state + attempt cap + backoff |
| **Startup recovery** | Complete | `src/services/jobs/recovery.py` |
| **Job fixture record/replay** | Complete | `src/services/jobs/job_fixtures.py` |
| **Multi-user auth** | Complete | `src/services/auth/` — magic link (Resend) + JWT cookie |
| **Roles & admin authorization** | Complete | `UserRole` (trial/premium/admin), `get_admin_user` dependency |
| **Per-user data ownership** | Complete | `user_id` on every job row; per-user CV, search, filter, model prefs |
| **Notification centre** | Complete | `src/services/notifications/`, `src/api/routes/notifications.py` |
| **Admin operational alerts** | Complete | `src/services/alerts.py` — email on stale LinkedIn session |
| **HITL API** | Complete | `src/api/routes/hitl.py` |
| **Web UI** | Complete | `ui/` — SvelteKit 2 / Svelte 5 runes, Tailwind v3 |
| **Admin dashboard UI** | Complete | `ui/src/routes/admin/` — jobs, queue, errors, users |
| **VPS deployment** | Complete | `.github/workflows/release.yml`, `docs/deployment.md` |

## Pipeline Architecture

The pipeline is split at the HITL boundary so generated CVs are reviewed in a batch rather than one
at a time. Discovery runs ahead of it, independently.

```
DISCOVERY (per user, APScheduler; opt-in)
  build search URL ─► scrape result cards ─► scrape each detail page ─► dedup by scoped job id
  Each discovered job is persisted immediately as `queued`, then pushed onto an asyncio JobQueue.
  A single ConsumerManager task drains the queue into the preparation workflow.

PREPARATION WORKFLOW (LangGraph)
  extract_job
    ├─ description too short ──────────► save_scrape_failed ─► END   (retry-eligible)
    └─ ok ─► filter_job
               ├─ score < reject_threshold, or hard disqualifier ─► save_filtered_out ─► END
               └─ pass ─► compose_cv ─► generate_pdf ─► save_to_db ─► END
                                                          (mvp → completed, full → pending)

HITL BOUNDARY  (ui/src/routes/+page.svelte, GET /api/hitl/pending)
  1 Decline  (+ optional reason → feeds the auto-refiner)
  2 Retry    (+ feedback       → retry workflow)
  3 Mark reviewed + open in LinkedIn (records `approved`; you apply by hand)

RETRY WORKFLOW (LangGraph)
  load_from_db ─► compose_cv ─► generate_pdf ─► update_db (back to `pending`)
```

### Workflow files

| Workflow | File | Nodes |
|---|---|---|
| Preparation | `src/agents/preparation_workflow.py` | `extract_job`, `filter_job`, `save_filtered_out`, `save_scrape_failed`, `compose_cv`, `generate_pdf`, `save_to_db` |
| Retry | `src/agents/retry_workflow.py` | `load_from_db`, `compose_cv`, `generate_pdf`, `update_db` |
| Dispatcher | `src/agents/dispatcher.py` | not a graph — builds `config["configurable"]`, tracks the thread, persists `failed` on exception |
| Shared | `src/agents/_shared.py` | LLM client construction, CV compose, PDF gen, master-CV loading |

Node-by-node detail lives in `agents.md`.

### Workflow modes

- **MVP mode** (`mode="mvp"`) — generate the PDF, skip HITL, terminal status `completed`. Used by the
  Generate page for one-off job descriptions.
- **Full mode** (`mode="full"`) — generate the PDF and save with status `pending` for HITL review.
  Used by everything that comes from LinkedIn discovery.

### Job sources

Adapters in `src/services/jobs/job_source.py`, selected by `JobSourceFactory.get_adapter(source)`:

| Source | Description |
|---|---|
| `manual` | User supplies the job description directly (Generate page) |
| `url` | Extract a job from an external URL |
| `linkedin` | Scraped LinkedIn posting, already normalized by the scraper |

## Design Principles

1. **Split at the HITL boundary** — batch review beats one-at-a-time interrupts, and it keeps the
   preparation pipeline free-running.
2. **Mode parameter over duplicate workflows** — one preparation graph serves both MVP and Full.
3. **Dependency injection via AppContext** — a single dataclass holds every shared dependency; there
   are no module-level globals. Workflow nodes receive repositories through LangGraph's
   `config["configurable"]`.
4. **Async-native** — every node is `async def`; no `asyncio.run()` inside the graph.
5. **Thin API handlers** — endpoints extract context, call a domain service, and return. Business
   logic lives in `JobOrchestrator` / `HITLProcessor`.
6. **Enforced state machine** — both repository implementations validate against
   `ALLOWED_TRANSITIONS`; illegal transitions raise rather than silently corrupting a row.
7. **Persist at discovery, not at completion** — a crash leaves a visible row, not a gap.
8. **Adapters and repositories at every seam** — job sources, LLM providers, and persistence are all
   swappable behind interfaces.
9. **Per-user everything** — CV, search preferences, filter preferences, model preferences, PDFs, and
   notifications are all scoped by `user_id`.
10. **Propose, don't mutate** — the auto-refiner never edits a user's filter prompt without explicit
    acceptance.

## Job Lifecycle State Machine

`src/models/state_machine.py`.

`BusinessState` (persisted in the `status` column):

| State | Meaning |
|---|---|
| `queued` | Discovered/submitted, awaiting the workflow |
| `processing` | In the preparation workflow |
| `completed` | Terminal. MVP mode: CV PDF is ready for download |
| `pending` | Awaiting HITL review (full mode) |
| `approved` | Reviewed and accepted. **Terminal in practice** — no application workflow consumes it |
| `declined` | Terminal. `decline_reason` feeds the auto-refiner |
| `retrying` | Retry workflow in flight |
| `applying` / `applied` | Reserved for the unbuilt application workflow |
| `failed` | Unrecoverable error; admin retry can push it back to `queued` |
| `filtered_out` | Rejected by the LLM filter. Not terminal: "Proceed Anyway" moves it to `processing` |
| `scrape_failed` | Description missing/too short. Retry-eligible, self-transitions to bump the counter |

`WorkflowStep` is transient in-flight step tracking (`extracting`, `filtering`, `composing_cv`,
`generating_pdf`, …) and is **not** the same field as `status`. The `applying_*` / `manual_required`
steps exist for the future application workflow.

Note the two historic name/value mismatches, kept deliberately for data and frontend compatibility:
`COMPLETED = "completed"` means "CV ready", and `PENDING = "pending"` means "pending HITL review".

## Data Models

Read the modules for field-level detail; this is the map.

| Module | Contents |
|---|---|
| `src/models/unified.py` | `JobRecord` (the persisted row), `JobSubmitRequest/Response`, `JobDescriptionInput`, `HITLDecision(+Response)`, `PendingApproval`, `JobStatusResponse`, `ApplicationHistoryItem` |
| `src/models/state_machine.py` | `BusinessState`, `WorkflowStep`, `ALLOWED_TRANSITIONS`, `validate_transition`, `InvalidStateTransitionError` |
| `src/models/user.py` | `User`, `UserRole`, `ModelChoice`, `UserModelPreferences`, `UserSearchPreferences`, magic-link auth request/response models |
| `src/models/job_filter.py` | `FilterResult`, `UserFilterPreferences`, `FilterRefinement`, `RefinementProposal`, `extract_learned_block` / `apply_learned_block` |
| `src/models/cv.py` | Master CV + tailored CV schema |
| `src/models/cv_attempt.py` | `CVCompositionAttempt` — retry history |
| `src/models/pdf_extraction.py` | PDF → master-CV extraction task models |
| `src/models/notification.py` | `Notification` |
| `src/models/job.py` | `ScrapedJob` and scraping-side models |

`JobRecord` carries several fields that exist purely for the operational behaviours below, and are
easy to miss: `filter_result`, `decline_reason`, `override_reason`, `refine_signal_state`,
`scrape_attempts` / `last_scrape_error` / `last_scrape_attempt_at`, `session_authenticated`, and
`recovery_attempts` / `last_recovery_attempt_at`.

## Subsystems and Non-Obvious Behaviour

### Discovery and queueing

- The scheduler iterates users **with search preferences configured** and runs a separate search per
  user, tagging scraped jobs with that `user_id`. If no user has preferences, it falls back to the
  global `LINKEDIN_SEARCH_*` settings; if those are empty too, the cycle is skipped
  (`reason="no_users"`).
- Search state is **per user**, not global: one user's in-flight or failing search never blocks or
  cancels another's, and an on-demand manual run is never dropped because a scheduled run is active.
- `UserLastRun` distinguishes `jobs_found` (raw scrape count) from `enqueued` (genuinely new) and
  `deduped`, and stores the exact `search_url` so a user can open the same query in a browser and
  see whether LinkedIn itself returns anything.
- Comma-separated keywords are translated into a LinkedIn boolean OR query, because LinkedIn treats
  a comma as literal text. A query already containing `OR`/`AND`/`NOT`, quotes, or parentheses is
  passed through untouched.

### Failure handling

- **Scrape failure**: a detail page yielding fewer than `SCRAPER_MIN_DESCRIPTION_CHARS` characters
  becomes `scrape_failed`. Re-attempts are gated by both `SCRAPER_MAX_ATTEMPTS` and
  `SCRAPER_RETRY_BACKOFF_MINUTES`, so a permanently broken posting cannot churn every tick.
- **Startup recovery**: rows left in `queued` / `processing` / `retrying` by a previous process are
  re-enqueued (LinkedIn source) or re-dispatched (url/manual) once during lifespan startup, bounded
  by `MAX_RECOVERY_ATTEMPTS` so a poison row cannot loop forever.
- **Dispatcher failure persistence**: on exception the dispatcher writes `failed` *only if
  `ALLOWED_TRANSITIONS` permits it*, so a late failure handler cannot clobber a terminal `completed`
  the workflow already wrote. If the workflow died before `save_to_db`, it can synthesize a `failed`
  record so the failure is still visible.
- **Session-death alert**: when a batch of ≥5 detail pages comes back ≥50% empty, that is treated as
  a stale `li_at` cookie and an email goes to `ADMIN_ALERT_EMAIL`. Cooldown state is persisted to
  disk so a container restart doesn't re-alert immediately. `session_authenticated` is recorded per
  scraped job for post-hoc diagnosis.

### Filtering

- Two thresholds, not one: below `reject_threshold` → `filtered_out` and CV generation is skipped;
  below `warning_threshold` → the job still reaches review but carries a warning badge and its red
  flags. A hard `disqualified` flag rejects regardless of score.
- Filtered-out jobs are **kept** with their reasoning, and the Applications page offers
  "Proceed Anyway", which re-enters CV generation with the filter skipped and an `override_reason`
  recorded.
- The filter prompt is per user: natural-language preferences are compiled into a structured prompt
  by an LLM meta-prompt, which the user can then edit by hand. All filter prompts live in
  `prompts/job_filter/` as `.system.txt` / `.user.txt` pairs —
  `default_filter_prompt`, `generate_prompt_from_prefs`, and `refine_filter_prompt`.

### Auto-refining filter prompt

- Declines and "Proceed Anyway" overrides become *signals* on the job row
  (`refine_signal_state`: pending → proposed → consumed). Each signal feeds the refiner exactly once.
- A weekly scheduler (`AUTO_REFINE_INTERVAL_HOURS`, gated by `AUTO_REFINE_MIN_SIGNALS` and capped at
  `AUTO_REFINE_SIGNAL_CAP`) makes one LLM call per qualifying user and produces a
  `RefinementProposal` for the auto-learned block of their `custom_prompt`, plus a persistent
  notification linking to `/settings#filter`.
- The proposal is **never applied automatically**. Accepting it splices the new block between the
  auto-learned markers; rejecting discards it. Both consume the signals.
- Global kill switch `AUTO_REFINE_ENABLED`; per-user opt-in defaults to **off**.

### LLM layer

Internals — Instructor `Mode.TOOLS` structured output, prompt caching, retry behaviour, the dynamic
model catalog, and how to add a provider — are documented in **`src/llm/CLAUDE.md`**. The short
version: a single `InstructorClient` backs all four providers, routing through LiteLLM via prefixed
model strings (note `grok → xai/`), and there is no `LLMClientFactory`.

Model selection resolves per operation: the caller looks up the user's `UserModelPreferences` entry
(`cv_generation`, `job_filtering`, or `filter_prompt_generation`) and passes it to
`create_llm_client` as an override. With no per-user choice, it falls back to `PRIMARY_LLM_PROVIDER`
plus that provider's `*_MODEL` setting. The catalog and settings store **bare** model ids; the
LiteLLM route prefix is reattached at client construction.

### Auth and roles

- Magic link: email → token → Resend email → `/api/auth/verify` sets an httpOnly `auth_token` JWT
  cookie (30-day expiry). Registration is open — first login creates the account with role `trial`.
- `DEV_AUTH_BYPASS=true` exposes `POST /api/auth/dev-login`, which mints that cookie with no email
  round-trip. The app **refuses to start** if this is set while `APP_URL` is not localhost, and the
  route 404s when it is off.
- Two layered FastAPI dependencies: `get_current_user` (401 when unauthenticated) and
  `get_admin_user` (403 unless `role == "admin"`). Admin-scope repository methods (`list_all_jobs`,
  `count_all_jobs`, …) are additive on top of the user-scoped ones, which remain the default path.
- `PUT /api/admin/users/{user_id}/role` refuses (409) to demote the last remaining admin. The UI
  mirrors the guard; the server check is authoritative.
- Bootstrap the first admin with `uv run python scripts/promote_user.py --email … --role admin`.

### Persistence

- `REPO_TYPE` defaults to `memory` — persistence is opt-in. Set `sqlite` for anything you care about.
- `InMemoryJobRepository` guards mutations with an `asyncio.Lock`; `SQLiteJobRepository` uses Piccolo
  on a shared engine that `UserRepository` and `NotificationRepository` also ride on.
- Migrations are idempotent and self-detecting: each one inspects the live schema, no-ops when the
  change is already present, and records itself in `schema_migrations`. That is what makes legacy
  DBs (pre-`role`, pre-`filter_preferences`) upgrade cleanly in place.
- The **master CV** is stored as JSON on the user row (`UserTable.master_cv_json`), uploaded via
  Settings or `PUT /api/users/me`. When a user record has none, every call site falls back to
  reading `MASTER_CV_PATH` (`data/cv/master_cv.json`) from disk — a single-user legacy path that is
  still live, and the reason a missing master CV surfaces as `FileNotFoundError` rather than a clean
  validation error.
- Generated PDFs are written per user: `data/generated_cvs/{user_id}/{job_id}.pdf`.

## API Surface

Routers in `src/api/routes/`. `src/api/main.py` owns settings, lifespan, middleware, and the static
mount; shared dependencies live in `src/api/deps.py`.

### Authentication — `src/api/routes/auth.py` (public)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/auth/login` | Request a magic-link email |
| GET | `/api/auth/verify?token=…` | Verify the magic link, set the JWT cookie |
| POST | `/api/auth/dev-login` | Local-only bypass; 404 unless `DEV_AUTH_BYPASS=true` |
| GET | `/api/auth/me` | Current authenticated user |
| POST | `/api/auth/logout` | Clear the auth cookie |

### User settings — `src/api/routes/users.py` (auth)

| Method | Endpoint | Description |
|---|---|---|
| PUT | `/api/users/me` | Update profile (display name, master CV, search/model prefs) |
| POST | `/api/users/me/master-cv/extract` | Start background LLM extraction of a master CV from an uploaded PDF (202) |
| GET | `/api/users/me/master-cv/extract/{extraction_id}` | Poll extraction status/result |
| GET/PUT | `/api/users/me/search-preferences` | LinkedIn search preferences |
| GET/PUT | `/api/users/me/filter-preferences` | Filter preferences (thresholds, prompt, opt-ins) |
| POST | `/api/users/me/filter-preferences/generate-prompt` | Compile natural language → filter prompt |
| GET | `/api/users/me/filter-preferences/refinement` | Pending refinement proposal + current learned block |
| POST | `/api/users/me/filter-preferences/refinement/accept` | Apply the proposed learned block |
| POST | `/api/users/me/filter-preferences/refinement/reject` | Discard the proposal |

### Jobs — `src/api/routes/jobs.py` (auth, user-scoped)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/jobs/submit` | Submit a job (url or manual) for CV generation |
| GET | `/api/jobs` | List the caller's jobs (status/source/date/search filters, paged) |
| GET | `/api/jobs/stats` | Per-status counts for the caller |
| GET | `/api/jobs/{job_id}/status` | Job status and details |
| GET | `/api/jobs/{job_id}/pdf` | Download the generated CV PDF |
| GET | `/api/jobs/{job_id}/html` | Generated CV as HTML (review preview) |
| POST | `/api/jobs/{job_id}/proceed` | "Proceed Anyway" — override a `filtered_out` verdict |
| DELETE | `/api/jobs/{job_id}` | Delete one job |
| DELETE | `/api/jobs/cleanup` | Delete old job records |
| POST | `/api/jobs/linkedin-search` | Trigger a LinkedIn search for the caller now |
| GET | `/api/jobs/linkedin-search/status` | Scheduler state + this user's last-run detail |
| POST | `/api/jobs/replay-fixtures` | Re-enqueue recorded job fixtures (dev/demo) |

### HITL — `src/api/routes/hitl.py` (auth)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/hitl/pending` | Jobs awaiting review (includes `filter_result` + the user's thresholds) |
| POST | `/api/hitl/{job_id}/decide` | Submit approve / decline / retry |
| GET | `/api/hitl/history` | Decision history |

### Notifications — `src/api/routes/notifications.py` (auth)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/notifications` | The caller's notifications |
| GET | `/api/notifications/unread-count` | Unread badge count |
| PUT | `/api/notifications/{id}/read` | Mark one read |
| PUT | `/api/notifications/read-all` | Mark all read |

### Admin — `src/api/routes/admin.py` (admin role required)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/admin/jobs` | All users' jobs, filtered/paged |
| GET | `/api/admin/jobs/{job_id}` | Full record for one job |
| POST | `/api/admin/jobs/{job_id}/retry` | Re-dispatch a job |
| DELETE | `/api/admin/jobs/{job_id}` | Delete one job |
| POST | `/api/admin/jobs/bulk-delete` | Delete many jobs |
| GET | `/api/admin/queue` | Queue depth + consumer/scheduler health |
| POST | `/api/admin/scheduler/run/{user_id}` | Force a search run for one user |
| POST | `/api/admin/scheduler/refine/{user_id}` | Force a refinement cycle for one user |
| GET | `/api/admin/errors` | Recent failures across users |
| GET | `/api/admin/users` | User list with roles |
| PUT | `/api/admin/users/{user_id}/role` | Change a role (409 on last-admin demotion) |

### System — `src/api/routes/system.py` (public)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | Health check, including queue-consumer status |
| GET | `/api/llm/models` | Current model catalog (models + per-1M pricing) |

## Startup and Shutdown

`lifespan` in `src/api/main.py`, in order:

1. Build the `AppContext`; refuse to start if `DEV_AUTH_BYPASS` is on with a non-localhost `APP_URL`.
2. Initialize the job repository, then the user repository (the in-memory repo doesn't set up the
   Piccolo engine that user/notification tables need, so this is unconditional).
3. Start the daily expired-magic-link cleanup loop.
4. Load the dynamic model catalog off the startup path and start its daily refresh scheduler.
5. Start the refinement scheduler (independent of LinkedIn search).
6. Either replay job fixtures (`SEED_JOBS_FROM_FILE`, which **disables** scraping) **or** start the
   browser + scraper + search scheduler + queue consumer.
7. Run startup recovery; if it re-enqueued anything and no consumer is running, start one.

Shutdown stops the consumer, all three schedulers, closes the browser, then closes the repository.
Every optional subsystem is wrapped so a failure logs and continues rather than blocking startup.

## Web UI

**Location** `ui/` · **Stack** SvelteKit 2 with Svelte 5 runes, Vite 7, TypeScript, Tailwind CSS v3 ·
**Design** neo-brutalist (bold borders, grain texture, brutal shadows)

| Route | Purpose |
|---|---|
| `/` | HITL review queue — job card, JD/CV panel toggle, filter score badge + red flags, decision buttons, keyboard shortcuts (`←`/`→` navigate, `1` decline, `2` retry, `3` mark reviewed + open in LinkedIn) |
| `/applications` | Every job in a filterable table: status/source/date/search filters, per-status stat cards, CV download, filter-out reason, "Proceed Anyway", delete |
| `/generate` | One-off CV generation from a pasted job description (MVP mode, bypasses review) |
| `/settings` | Profile, master CV (JSON paste or PDF upload), search prefs, filter prefs + refinement review, per-operation model prefs, "Start the Process" manual search trigger |
| `/welcome` | Onboarding guide; the default landing page for logged-out visitors |
| `/login`, `/auth/verify` | Magic-link flow |
| `/admin/jobs`, `/admin/queue`, `/admin/errors`, `/admin/users` | Admin dashboard; `/admin` redirects to `/admin/jobs`, and `admin/+layout.svelte` redirects to `/` when `auth.isAdmin` is false |

Cross-cutting UI details worth knowing:

- The auth store reads `role` from `/api/auth/me` and exposes `isAdmin` as a `$derived` value. The
  admin layout guard is a convenience — the API is the real authorization boundary.
- `WIPBadge` + `src/lib/wip/features.ts` are the single registry for "not built yet" labelling, so
  in-progress surfaces are marked consistently instead of ad hoc.
- The release version is rendered in the nav in background colour — invisible until you select it.
- Decline reasons are sent as `reasoning` (they feed the auto-refiner); retry notes are sent as
  `feedback` (they feed CV recomposition). Same modal, different field.

### Integration with the backend

- In production FastAPI serves `ui/build/` at `/`, with the API under `/api/*`; the static mount is
  registered last so it cannot shadow a route.
- The CV generator polls `/api/jobs/{job_id}/status`; the CV preview fetches
  `/api/jobs/{job_id}/html`.
- `VITE_API_BASE_URL=""` in the production image, so the browser uses relative `/api/*` URLs and
  Caddy serves both from one origin.

## Reference Implementations

`Obsolete/` was a symlink to two third-party LinkedIn-automation projects (GodsScion's
`Auto_job_applier_linkedIn` and AIHawk's `Jobs_Applier_AI_Agent_AIHawk`) kept for reference. **The
symlink is dead on any machine other than the original Windows host** — treat references to it in
older plan documents as unavailable.

## Next Steps

1. **Durable LinkedIn session auth** — replace cookie capture/replay with a persistent browser
   profile (`docs/plans/persistent-browser-profile-auth.md`). This blocks reliable scraping, which
   blocks everything downstream.
2. **Easy Apply automation** — LinkedIn moved Easy Apply to a React SDUI modal, so earlier work is
   stale (`docs/plans/sdui-easy-apply-rework.md`, `docs/plans/easy-apply-happy-path.md`,
   `docs/plans/ARCHITECTURE-browser-agent.md`).
3. **Job notifications** — Telegram delivery (`docs/plans/telegram-job-notifications.md`).
