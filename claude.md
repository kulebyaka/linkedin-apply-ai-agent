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
- **PDF Generation**: WeasyPrint
- **LLM Integration**: Multi-provider support (OpenAI, Anthropic, DeepSeek, Grok)

### Directory Structure

```
src/
├── agents/              # LangGraph workflow definitions
│   └── workflow.py      # Main workflow state machine
├── llm/                 # LLM provider integrations
│   └── provider.py      # Abstract base + provider implementations
├── services/            # Business logic services
│   ├── job_fetcher.py   # LinkedIn job fetching
│   ├── job_filter.py    # LLM-based job filtering
│   ├── cv_composer.py   # LLM-powered CV tailoring
│   ├── pdf_generator.py # PDF generation from JSON
│   ├── browser_automation.py  # Playwright LinkedIn automation
│   └── notification.py  # Webhook/email notifications
├── models/              # Pydantic data models
│   ├── job.py          # Job posting models
│   ├── cv.py           # CV data models
│   └── application.py  # Application status models
├── api/                 # FastAPI endpoints
│   └── main.py         # REST API for HITL UI
├── config/              # Configuration
│   └── settings.py     # Pydantic settings with env vars
└── utils/              # Utilities
    └── logger.py       # Logging setup

data/
├── cv/                 # Master CV in JSON
├── jobs/               # Fetched job data
└── generated_cvs/      # Tailored CV PDFs
```

## Key Design Patterns

### 1. LangGraph Workflow
- Defined as directed graph with nodes for each step
- Supports conditional routing based on state
- Built-in HITL pause/resume mechanism
- State management with `WorkflowState` TypedDict

### 2. Multi-LLM Support
- Factory pattern for provider instantiation
- Abstract `BaseLLMClient` interface
- Easy switching via environment variables
- Fallback support for reliability

### 3. Data Models
- Pydantic models for all data structures
- Type safety and validation
- Easy JSON serialization
- Settings management with `pydantic-settings`

## Important Implementation Details

### Workflow Nodes (src/agents/workflow.py)
1. **fetch_jobs_node**: Queries LinkedIn API/scrapes jobs
2. **filter_job_node**: LLM evaluates job suitability
3. **compose_cv_node**: LLM tailors CV to job
4. **generate_pdf_node**: Creates PDF from JSON
5. **human_review_node**: Pauses for user approval
6. **apply_linkedin_node**: Automates application
7. **send_notification_node**: Alerts on errors

### Conditional Routing
- After filtering: suitable → compose_cv | not_suitable → END
- After HITL: approved → apply | declined → END | retry → compose_cv
- After apply: success → END | failure → notification

### Master CV Format
- Stored as JSON in `data/cv/master_cv.json`
- Schema defined in `src/models/cv.py`
- Contains comprehensive work history, skills, projects
- LLM recomposes relevant portions for each job

### Browser Automation
- Playwright for headless Chrome
- Handles LinkedIn login (potentially with 2FA via HITL)
- Navigates application forms
- Uploads tailored CV PDF
- Can pause for HITL if uncertain

## Development Guidelines

### When Adding Features

1. **LLM Integration**
   - Add new provider class in `src/llm/provider.py`
   - Inherit from `BaseLLMClient`
   - Implement `generate()` and `generate_json()`
   - Register in `LLMClientFactory`

2. **Workflow Modifications**
   - Update `WorkflowState` TypedDict
   - Add/modify nodes in `create_workflow()`
   - Update routing functions for new paths
   - Test state transitions thoroughly

3. **API Endpoints**
   - Add routes in `src/api/main.py`
   - Use Pydantic models for request/response
   - Document with FastAPI auto-docs
   - Consider CORS for frontend access

4. **Data Models**
   - Define in appropriate `models/*.py` file
   - Use type hints and Pydantic validation
   - Add docstrings for complex fields
   - Consider backward compatibility

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
3. Implement API integration
4. Register in factory
5. Add config to `settings.py`
6. Document in README

### Modifying CV Tailoring Logic

1. Update prompt in `src/services/cv_composer.py`
2. Adjust `_build_cv_prompt()` method
3. Test with various job descriptions
4. Consider adding user feedback loop

### Adding New Workflow Step

1. Define node function in `src/agents/workflow.py`
2. Add node to workflow graph
3. Update state TypedDict if needed
4. Add routing logic
5. Update tests

### Debugging Workflow Issues

1. Check logs (configured in `src/utils/logger.py`)
2. Inspect workflow state at each node
3. Use LangGraph visualization tools
4. Test nodes individually before integration

## Current Status

This is a **skeleton implementation**. All core structure is in place, but services are not yet implemented:

### ✅ Complete
- Project structure
- Data models (Pydantic)
- Configuration system
- LLM provider abstractions
- API endpoint stubs
- Docker setup
- Documentation

### 🚧 To Implement
- LinkedIn job fetching (API or scraping)
- LLM integration (actual API calls)
- LangGraph workflow execution
- PDF generation
- Playwright browser automation
- HITL approval mechanism
- Frontend UI (Tinder-like interface)
- Notification system
- State persistence (database)
- Scheduling (APScheduler)

## Next Steps for Implementation

1. **Set up environment**
   - Copy `.env.example` to `.env`
   - Add API keys for at least one LLM provider
   - Create master CV JSON

2. **Implement LLM integration**
   - Start with one provider (e.g., OpenAI)
   - Test with simple prompts
   - Add structured output for job filtering

3. **Job fetching**
   - Research LinkedIn API options
   - Implement fallback to scraping if needed
   - Test with real job searches

4. **CV tailoring**
   - Create prompts for CV composition
   - Test with sample jobs
   - Validate JSON output schema

5. **PDF generation**
   - Create CV template (HTML/CSS)
   - Implement WeasyPrint conversion
   - Test with various CV formats

6. **Browser automation**
   - LinkedIn login flow
   - Easy Apply automation
   - Error handling and recovery

7. **HITL interface**
   - Build simple React/Vue frontend
   - Implement approval endpoints
   - Create Tinder-like review UI

8. **Integration**
   - Connect all services in LangGraph
   - Test end-to-end workflow
   - Add error handling and notifications

9. **Deployment**
   - Test Docker setup
   - Deploy to VPS
   - Set up monitoring and logs

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

# Playwright
playwright install chromium                  # Install browser
playwright codegen linkedin.com              # Record automation
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

**Browser automation fails**
- Run `playwright install` for browsers
- Check headless setting for debugging
- Verify LinkedIn isn't blocking automation

**PDF generation issues**
- Install system dependencies for WeasyPrint
- Check template HTML is valid
- Verify font paths if using custom fonts

## Security Notes

- **Never commit** `.env` or actual CV data
- **Secure storage** for LinkedIn credentials
- **Rate limiting** for API calls
- **Respect** LinkedIn's terms of service
- **User data** stays on self-hosted VPS

## Resources

- [LangGraph Documentation](https://python.langchain.com/docs/langgraph)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Playwright Python](https://playwright.dev/python/)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [WeasyPrint Documentation](https://doc.courtbouillon.org/weasyprint/)

---

**Last Updated**: 2025-12-13
**Status**: Skeleton implementation complete, ready for feature development
