# LinkedIn Job Application AI Agent

An intelligent automation system that fetches job postings, filters them with AI, tailors your CV for each position, and automates job applications with human oversight.

## Vision

The goal is a fully automated job application pipeline:

1. **Fetch Jobs** - Hourly polling of LinkedIn based on search filters
2. **AI Filtering** - LLM evaluates each posting for hidden disqualifiers (e.g., "remote" jobs that actually require relocation)
3. **CV Tailoring** - LLM recomposes your comprehensive CV to highlight relevant experience for each position
4. **PDF Generation** - Professional resume created from tailored CV
5. **Human Review** - Tinder-like UI for batch approval: swipe right (apply), left (decline), or down (retry with feedback)
6. **Auto-Apply** - Browser automation submits approved applications on LinkedIn

## Implementation Status

| Component | Status | Notes |
|-----------|--------|-------|
| LLM Provider Layer | **Complete** | OpenAI, Anthropic, DeepSeek, Grok |
| CV Composer | **Complete** | LLM-powered CV tailoring |
| PDF Generator | **Complete** | WeasyPrint + Jinja2 templates |
| Preparation Workflow | **Complete** | Job input → CV → PDF pipeline |
| Retry Workflow | **Complete** | Re-compose CV with user feedback |
| HITL API | **Complete** | Approve/decline/retry endpoints |
| MVP Web UI | **Complete** | Single-page CV generator |
| HITL Review UI | **Complete** | Tinder-like batch review interface |
| Job Source Adapters | *Interface only* | URL extraction, manual input |
| Job Filter (LLM) | *Not implemented* | Skeleton exists |
| Application Workflow | *Stubs only* | Browser automation pending |
| LinkedIn Integration | *Not implemented* | Job fetching & Easy Apply |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PREPARATION WORKFLOW                                 │
│  (runs continuously, processes jobs, saves to DB for batch review)          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Job Source ──► Extract ──► Filter ──► Compose CV ──► Generate PDF ──► DB │
│   (LinkedIn/URL)              (LLM)        (LLM)        (WeasyPrint)        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                        ┌───────────────────────┐
                        │    HITL BOUNDARY      │
                        │  (Tinder-like batch   │
                        │   review UI)          │
                        │                       │
                        │  → Approve (apply)    │
                        │  ← Decline (archive)  │
                        │  ↓ Retry (feedback)   │
                        └───────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│ APPLICATION WORKFLOW│  │   RETRY WORKFLOW    │  │      DECLINED       │
│                     │  │                     │  │                     │
│ Browser automation  │  │ Re-compose CV with  │  │ Archived, no action │
│ via Playwright      │  │ user feedback       │  │                     │
│ (not implemented)   │  │ (complete)          │  │                     │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
```

## Tech Stack

- **Workflow**: LangGraph (state machine orchestration)
- **Backend**: FastAPI, Pydantic v2
- **Frontend**: SvelteKit, TailwindCSS
- **PDF**: WeasyPrint + Jinja2
- **Database**: SQLite (Piccolo ORM)
- **Browser Automation**: Playwright (planned)
- **LLM**: Multi-provider (OpenAI, Anthropic, DeepSeek, Grok)

## What Works Today

The **MVP mode** is fully functional:

1. Paste a job description into the web UI, select LLM provider and model
2. LLM tailors your CV to highlight relevant experience
3. Professional PDF is generated and auto-downloaded

This covers the core value proposition: AI-powered CV tailoring with professional output.

## Roadmap

- [ ] Job source adapters (URL extraction, LinkedIn API)
- [ ] LLM-based job filtering for hidden disqualifiers
- [x] Tinder-like HITL UI for batch review
- [ ] Browser automation for LinkedIn Easy Apply

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/jobs/submit` | Submit job for CV generation |
| GET | `/api/jobs/{job_id}/status` | Get job status |
| GET | `/api/jobs/{job_id}/pdf` | Download generated PDF |
| GET | `/api/hitl/pending` | Get pending approvals |
| POST | `/api/hitl/{job_id}/decide` | Submit approval decision |

## Development

```bash
pytest              # Run tests
black src/          # Format code
mypy src/           # Type check
```

## License

All Rights Reserved. This code is provided for viewing purposes only. No permission is granted to use, copy, modify, or distribute this software.

## Disclaimer

This tool is for personal use. Always comply with LinkedIn's Terms of Service. Automated applications may violate platform policies - use responsibly.
