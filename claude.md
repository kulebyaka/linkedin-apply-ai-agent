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
│   ├── application_workflow.py # Apply to jobs after HITL approval (stubs)
│   └── retry_workflow.py       # Re-compose CV with user feedback
├── llm/                        # LLM provider integrations
│   └── provider.py             # Abstract base + provider implementations
├── services/                   # Business logic services
│   ├── auth.py                 # AuthService: magic link + JWT authentication
│   ├── user_repository.py      # UserRepository: user CRUD + search prefs + magic links
│   ├── job_orchestrator.py     # Domain service: job submission & status queries
│   ├── hitl_processor.py       # Domain service: HITL decision processing
│   ├── job_source.py           # Job source adapters (URL, manual, LinkedIn)
│   ├── job_filter.py           # LLM-based job filtering with two-threshold routing
│   ├── job_repository.py       # Data access layer (in-memory + SQLite via Piccolo)
│   ├── cv_composer.py          # LLM-powered CV tailoring
│   ├── cv_validator.py         # CV validation with configurable hallucination policy
│   ├── cv_prompts.py           # CV composition prompts
│   ├── pdf_generator.py        # PDF generation from JSON (WeasyPrint)
│   ├── browser_automation.py   # Playwright stealth browser with cookie auth
│   ├── linkedin_scraper.py     # LinkedIn job search results scraper
│   ├── linkedin_search.py      # LinkedIn search URL builder + filters
│   ├── job_queue.py            # Async job queue + ConsumerManager for lifecycle
│   ├── scheduler.py            # APScheduler-based per-user LinkedIn search scheduler
│   └── notification.py         # Webhook/email notifications (skeleton)
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
| Application | `src/agents/application_workflow.py` | Apply to job (stubs only) |
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

### Application Workflow Nodes (Stubs)
1. **load_from_db_node**: Loads approved job
2. **apply_deep_agent_node**: Browser automation via Playwright (not implemented)
3. **apply_linkedin_node**: LinkedIn Easy Apply automation (not implemented)
4. **apply_manual_node**: Marks job for manual application
5. **update_db_node**: Records application result

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
| PUT | `/api/users/me` | Update user profile (display_name, master_cv_json, search_preferences) |
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
| POST | `/api/hitl/{job_id}/decide` | Submit HITL decision (approve/decline/retry) |
| GET | `/api/hitl/history` | Get application history |
| DELETE | `/api/jobs/cleanup` | Clean up old job records |

### System (public)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/jobs/linkedin-search` | Trigger LinkedIn job search manually |
| GET | `/api/jobs/linkedin-search/status` | Get scheduler state and last run info |
| GET | `/api/health` | Health check (includes queue consumer status) |

## Data Models

### User & Auth Models (`src/models/user.py`)

- `User` - User entity: id, email, display_name, master_cv_json, search_preferences, filter_preferences, timestamps
- `LoginRequest` - Email input for magic link request
- `LoginResponse` - Success message after magic link sent
- `VerifyRequest` - Token for magic link verification
- `AuthResponse` - User object + message after successful auth
- `UserUpdateRequest` - Optional fields for profile update (display_name, master_cv_json, search_preferences, filter_preferences)
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

- `BusinessState` - Job lifecycle states: queued, processing, cv_ready, pending_review, approved, declined, retrying, applying, applied, failed, filtered_out
  - `filtered_out`: terminal state for LLM-rejected jobs (score below reject threshold or hard disqualifier); reachable from `queued` and `processing`
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
| **Application Workflow** | 🟡 Stubs | `src/agents/application_workflow.py` - stubs only |
| **Job Filter (LLM)** | ✅ Complete | `src/services/job_filter.py` — two-threshold routing, hidden disqualifier detection, per-user prompt, HITL badge |

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

1. **Implement Application Workflow** - Deep agent with Playwright MCP for browser automation
2. **LinkedIn Easy Apply** - Automated application submission via browser automation

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
