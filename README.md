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
| Application Workflow | **Complete** | Deterministic LinkedIn Easy Apply (no LLM) via Chrome extension bridge |
| Chrome Extension Bridge | **Complete** | MV3 DOM actuator + WebSocket relay; per-field server-orchestrated fill |
| LLM Form-Fill Agent | *Deferred* | Agentic screening-question answering + vision fallback (next sprint) |

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
│ Deterministic Easy  │  │ Re-compose CV with  │  │ Archived, no action │
│ Apply via Chrome    │  │ user feedback       │  │                     │
│ extension bridge    │  │ (complete)          │  │                     │
│ (complete, no LLM)  │  │                     │  │                     │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
```

The Application Workflow drives the LinkedIn **Easy Apply** modal field-by-field over a
WebSocket bridge to a Chrome MV3 extension running in your own logged-in browser — the server
never sees your LinkedIn credentials. Field values come from your **Application Profile** and
tailored CV; any unrecognized screening question aborts the application to `manual_required`
(it never guesses). Enable `auto_apply` to skip the human checkpoint for filtered-in jobs.

## Tech Stack

- **Workflow**: LangGraph (state machine orchestration)
- **Backend**: FastAPI, Pydantic v2
- **Frontend**: SvelteKit, TailwindCSS
- **PDF**: WeasyPrint + Jinja2
- **Database**: SQLite (Piccolo ORM)
- **Browser Automation**: Playwright (scraping) + Chrome MV3 extension bridge (Easy Apply)
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
- [x] Browser automation for LinkedIn Easy Apply (deterministic, via Chrome extension bridge)
- [ ] LLM form-fill agent for novel screening questions + non-LinkedIn ATS (vision fallback)

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/jobs/submit` | Submit job for CV generation |
| GET | `/api/jobs/{job_id}/status` | Get job status |
| GET | `/api/jobs/{job_id}/pdf` | Download generated PDF |
| GET | `/api/hitl/pending` | Get pending approvals |
| POST | `/api/hitl/{job_id}/decide` | Submit approval decision (approve triggers Easy Apply) |
| POST | `/api/jobs/{job_id}/apply` | (Re-)trigger Easy Apply (e.g. after connecting the extension) |
| WS | `/ws/extension` | Chrome extension bridge (JWT in first frame) |

## Chrome Extension (Easy Apply)

Automated Easy Apply runs inside your own browser via a Chrome MV3 extension, so the server
never handles your LinkedIn credentials.

1. **Load the extension**: open `chrome://extensions`, enable *Developer mode*, click *Load unpacked*, and select the `extension/` directory. Set the assigned extension ID as the frontend build var `VITE_EXTENSION_ID` (the `/extension-auth` page targets it; you can also append `?ext=<id>` to that URL). If your app origin isn't `localhost:5173` / `*.kuule.cc`, add it to the hardcoded `externally_connectable.matches` list in `extension/manifest.json`.
2. **Fill your Application Profile**: in *Settings*, complete the *Application Profile* card (phone, years of experience, work authorization, etc.). Incomplete profiles cause applies to abort to *manual required* rather than guessing.
3. **Connect**: click *Connect* in the extension popup (or open `/extension-auth` in the app) to hand the extension a session token. The popup shows the live connection status.
4. **Apply**: approve a job in the HITL review (or enable *auto-apply* in Settings to skip the checkpoint). Jobs approved while the extension is disconnected land in `needs_extension`; connect and hit *Apply now*.

## Development

```bash
pytest              # Run tests
black src/          # Format code
mypy src/           # Type check
```

### First admin

Users sign up via magic-link login and default to the `trial` role. To bootstrap the first admin (and unlock the `/admin` dashboard), run the promotion CLI against the local DB:

```bash
uv run python scripts/promote_user.py --email you@example.com --role admin
```

The script also accepts `--role trial|premium` and `--list-admins`.

## License

All Rights Reserved. This code is provided for viewing purposes only. No permission is granted to use, copy, modify, or distribute this software.

## Disclaimer

This tool is for personal use. Always comply with LinkedIn's Terms of Service. Automated applications may violate platform policies - use responsibly.
