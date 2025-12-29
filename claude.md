# Claude.md - LinkedIn Job Application Agent

This document provides context for Claude Code (or any AI assistant) to effectively work with this codebase.

**IMPORTANT**: The `implementation-plan.md` file is the **source of truth** for all functional and non-functional requirements, architecture decisions, and design specifications. Always refer to it when making architectural decisions or implementing features.

## Project Overview

**LinkedIn Job Application Agent** is an intelligent automation system that:
- Fetches job postings from LinkedIn hourly
- Uses LLM to filter jobs and detect hidden disqualifiers
- Tailors CV for each job using AI
- Generates professional PDF resumes
- Automates LinkedIn job applications via browser automation
- Implements Human-in-the-Loop (HITL) approval with Tinder-like UI
- Supports multiple LLM providers (OpenAI, DeepSeek, Grok, Anthropic)

## Architecture

### Core Technology Stack
- **Workflow Orchestration**: LangGraph (state machine for agent workflow)
- **Backend Framework**: FastAPI (for HITL UI API)
- **Browser Automation**: Playwright
- **Data Validation**: Pydantic v2
- **PDF Generation**: WeasyPrint + Jinja2
- **LLM Integration**: Multi-provider support (OpenAI, Anthropic, DeepSeek, Grok)

### Directory Structure

```
src/
├── agents/                     # LangGraph workflow definitions
│   ├── preparation_workflow.py # Main pipeline: job → CV → PDF → DB
│   ├── application_workflow.py # Apply to jobs after HITL approval (stubs)
│   └── retry_workflow.py       # Re-compose CV with user feedback
├── llm/                        # LLM provider integrations
│   └── provider.py             # Abstract base + provider implementations
├── services/                   # Business logic services
│   ├── job_source.py           # Job source adapters (URL, manual, LinkedIn)
│   ├── job_filter.py           # LLM-based job filtering (skeleton)
│   ├── job_repository.py       # Data access layer (in-memory implementation)
│   ├── cv_composer.py          # LLM-powered CV tailoring
│   ├── cv_prompts.py           # CV composition prompts
│   ├── pdf_generator.py        # PDF generation from JSON (WeasyPrint)
│   ├── browser_automation.py   # Playwright LinkedIn automation (skeleton)
│   └── notification.py         # Webhook/email notifications (skeleton)
├── models/                     # Pydantic data models
│   ├── job.py                  # Job posting models
│   ├── cv.py                   # CV data models
│   ├── unified.py              # Unified models for two-workflow architecture
│   └── mvp.py                  # MVP-specific models
├── api/                        # FastAPI endpoints
│   └── main.py                 # REST API for HITL UI
├── config/                     # Configuration
│   └── settings.py             # Pydantic settings with env vars
└── utils/                      # Utilities
    └── logger.py               # Logging setup

data/
├── cv/                         # Master CV in JSON
├── jobs/                       # Fetched job data
└── generated_cvs/              # Tailored CV PDFs
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

## Key Design Patterns

### 1. LangGraph Workflows
- Defined as directed graphs with nodes for each step
- Supports conditional routing based on state
- Built-in checkpointing with MemorySaver
- State management with TypedDict classes

### 2. Multi-LLM Support
- Factory pattern for provider instantiation (`LLMClientFactory`)
- Abstract `BaseLLMClient` interface
- Easy switching via environment variables
- Fallback support for reliability

### 3. Repository Pattern
- `JobRepository` abstract interface for data persistence
- `InMemoryJobRepository` implementation for development
- Future: SQLite/PostgreSQL persistence

### 4. Job Source Adapters
- Abstract interface in `src/services/job_source.py`
- Adapters for URL extraction, manual input, LinkedIn API
- Factory pattern: `JobSourceFactory.get_adapter(source)`

#### Important Notes about strict schema support
- **OpenAI**: Requires GPT-4 or newer models for strict schema support
- **Anthropic**: Requires beta header `anthropic-beta: structured-outputs-2025-11-13` (already configured)
- **Grok**: Works with all models after grok-2-1212
- **DeepSeek**: Does NOT support strict schemas - validates after generation

See `src/llm/provider.py` module documentation for detailed implementation.

## Important Implementation Details

### Preparation Workflow Nodes
1. **extract_job_node**: Extracts structured job data from source (URL/manual/LinkedIn)
2. **filter_job_node**: LLM evaluates job suitability (LinkedIn only, currently passthrough)
3. **compose_cv_node**: LLM tailors CV to job description
4. **generate_pdf_node**: Creates PDF from tailored CV JSON
5. **save_to_db_node**: Persists job record (MVP: completed, Full: pending)

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
- Stored as JSON in `data/cv/master_cv.json`
- Schema defined in `src/models/cv.py`
- Contains comprehensive work history, skills, projects
- LLM recomposes relevant portions for each job

## API Endpoints

### Unified Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/jobs/submit` | Submit job for CV generation (URL or manual input) |
| GET | `/api/jobs/{job_id}/status` | Get job status and details |
| GET | `/api/jobs/{job_id}/pdf` | Download generated CV PDF |
| GET | `/api/hitl/pending` | Get all jobs pending HITL review |
| POST | `/api/hitl/{job_id}/decide` | Submit HITL decision (approve/decline/retry) |
| GET | `/api/hitl/history` | Get application history |

### Legacy Endpoints (Backward Compatible)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/cv/generate` | Submit CV generation (MVP mode) |
| GET | `/api/cv/status/{job_id}` | Get CV generation status |
| GET | `/api/cv/download/{job_id}` | Download generated CV PDF |

## Data Models

Defined in `src/models/unified.py`:

- `JobSubmitRequest` - Input for job submission (source, mode, url/job_description)
- `JobSubmitResponse` - Response with job_id and status
- `HITLDecision` - User decision (approved/declined/retry + feedback)
- `HITLDecisionResponse` - Response after decision processed
- `PendingApproval` - Job details for HITL review UI
- `JobStatusResponse` - Full job status with CV and PDF info
- `JobRecord` - Database record for job persistence
- `ApplicationHistoryItem` - History entry for completed jobs

## Implementation Status

| Component | Status | Notes |
|-----------|--------|-------|
| **Two-Workflow Architecture** | ✅ Complete | Preparation + Application workflows with HITL boundary |
| **LLM Provider Layer** | ✅ Complete | `src/llm/provider.py` |
| **Preparation Workflow** | ✅ Complete | `src/agents/preparation_workflow.py` |
| **Retry Workflow** | ✅ Complete | `src/agents/retry_workflow.py` |
| **Compose Tailored CV** | ✅ Complete | `src/services/cv_composer.py` |
| **Generate PDF** | ✅ Complete | `src/services/pdf_generator.py` (WeasyPrint + Jinja2) |
| **HITL API Endpoints** | ✅ Complete | `src/api/main.py` |
| **Unified Data Models** | ✅ Complete | `src/models/unified.py` |
| **Job Repository (DAL)** | ✅ Complete | `src/services/job_repository.py` (in-memory) |
| **Job Source Adapters** | 🟡 Interface | `src/services/job_source.py` - interface only |
| **Application Workflow** | 🟡 Stubs | `src/agents/application_workflow.py` - stubs only |
| **Job Filter (LLM)** | 🔴 Pending | `src/services/job_filter.py` skeleton |
| **Browser Automation** | 🔴 Pending | `src/services/browser_automation.py` skeleton |
| **HITL Frontend UI** | 🔴 Pending | Tinder-like React/Vue interface |
| **LinkedIn Integration** | 🔴 Pending | Job fetching and Easy Apply |

## Development Guidelines

### Testing Strategy

- Unit tests for each service class
- Integration tests for workflow
- Mock LLM responses for determinism
- Playwright tests for browser automation
- API endpoint tests with TestClient

### Configuration

All settings in `.env`:
- Credentials (LinkedIn, LLM APIs)
- Provider selection (primary/fallback)
- Paths and directories
- Workflow parameters (fetch interval, concurrency)
- API server settings

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
3. Test with various job descriptions
4. Consider adding user feedback loop

### Adding New Workflow Step

1. Define node function in appropriate workflow file
2. Add node to workflow graph
3. Update state TypedDict if needed
4. Add routing logic
5. Update tests

### Debugging Workflow Issues

1. Check logs (configured in `src/utils/logger.py`)
2. Inspect workflow state at each node
3. Use LangGraph visualization tools
4. Test nodes individually before integration

## Next Steps

1. **Implement Job Source Adapters** - URL extraction using HTTP + LLM, manual input processing
2. **Build HITL Frontend** - Tinder-like React/Vue UI for batch review
3. **Implement Application Workflow** - Deep agent with Playwright MCP for browser automation
4. **Add Job Filter Logic** - LLM-based job suitability evaluation
5. **LinkedIn Integration** - Job fetching and Easy Apply automation
6. **Database Persistence** - SQLite or PostgreSQL for job records

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

## Useful Commands

```bash
# Development
python -m uvicorn src.api.main:app --reload  # Start API server
python main.py                               # Run workflow
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
