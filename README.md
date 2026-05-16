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
| Job Filter (LLM) | **Complete** | Two-threshold routing, hidden disqualifier detection, per-user prompt, HITL badge |
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
- [x] LLM-based job filtering for hidden disqualifiers
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

## Quick Start

For a fresh VPS deployment walkthrough — system deps, env vars, smoke test,
and LinkedIn cookie refresh — see [`docs/DEPLOY.md`](docs/DEPLOY.md).

## Development

```bash
uv run pytest              # Run tests
uv run black src/          # Format code
uv run mypy src/           # Type check
```

## Deploying to a Real Domain

When moving from `localhost` to a public domain, set the following in `.env`:

- `APP_URL=https://apply.example.com` — base URL for magic-link callbacks.
- `CORS_ORIGINS=https://apply.example.com` — comma-separated origins (or a
  JSON list). The API will log a startup warning if `APP_URL` is non-local
  but `CORS_ORIGINS` only allows localhost.
- `JWT_SECRET` — generate with `openssl rand -hex 32`. Must be at least 32
  characters; the placeholder `change-me-in-production` will refuse to sign
  tokens at runtime.
- `RESEND_API_KEY` and `RESEND_FROM` — magic-link email delivery.
- One of `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` /
  `GROK_API_KEY` matching `PRIMARY_LLM_PROVIDER`. On startup the API checks
  the configured provider's key; if missing, `POST /api/jobs/submit` returns
  `503 llm_not_configured` until the operator fixes it (no restart needed
  for the next attempt).
- `REPO_TYPE=sqlite` and `DB_PATH=./data/jobs.db` for persistence.

## System Dependencies

PDF generation uses WeasyPrint, which needs native libraries (Pango, GLib,
Cairo, gdk-pixbuf). The API runs a startup pre-flight; `GET /api/health`
returns `pdf_ok: false` with a hint when something is missing.

- Debian/Ubuntu:
  ```bash
  sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
      libglib2.0-0 libcairo2 libgdk-pixbuf2.0-0
  ```
- macOS (Homebrew):
  ```bash
  brew install pango glib cairo gdk-pixbuf
  export DYLD_LIBRARY_PATH=/opt/homebrew/lib   # Apple Silicon
  ```
- Playwright (LinkedIn scraping):
  ```bash
  uv run playwright install chromium
  ```

## License

All Rights Reserved. This code is provided for viewing purposes only. No permission is granted to use, copy, modify, or distribute this software.

## Disclaimer

This tool is for personal use. Always comply with LinkedIn's Terms of Service. Automated applications may violate platform policies - use responsibly.
