# LinkedIn Job Application AI Agent

A self-hosted, multi-user system that scrapes LinkedIn job postings, filters them with an LLM,
tailors your CV to each posting, renders a PDF, and puts every result in front of you for review
before anything leaves your machine.

## Vision

The end goal is a fully automated job application pipeline:

1. **Fetch jobs** — hourly LinkedIn search per user, based on their saved preferences
2. **AI filtering** — LLM scores each posting and flags hidden disqualifiers (e.g. "remote" roles
   that actually require relocation, unstated visa requirements)
3. **CV tailoring** — LLM recomposes your master CV to emphasise experience relevant to the posting
4. **PDF generation** — professional resume rendered from the tailored CV JSON
5. **Human review** — Tinder-like batch UI: decline, retry with feedback, or mark reviewed
6. **Auto-apply** — browser automation submits approved applications *(not implemented — see below)*

Steps 1–5 work end to end today. Step 6 does not exist yet: **you apply manually** via the LinkedIn
link surfaced in the review queue.

## Implementation Status

| Component | Status | Notes |
|-----------|--------|-------|
| LLM provider layer | **Complete** | One `InstructorClient` over LiteLLM → OpenAI, Anthropic, DeepSeek, Grok |
| Dynamic model catalog | **Complete** | Live model list + per-1M pricing, refreshed daily, static fallback |
| Per-operation model choice | **Complete** | User picks provider+model separately for CV generation / filtering / prompt generation |
| LinkedIn search + scraper | **Complete** | Per-user scheduled search, boolean-OR keywords, dedup, detail-page parsing |
| Job filter (LLM) | **Complete** | Two-threshold routing, hidden-disqualifier detection, per-user prompt |
| Auto-refining filter prompt | **Complete** | Learns from declines/overrides, *proposes* prompt changes for your approval |
| CV composer | **Complete** | LLM CV tailoring + configurable hallucination policy |
| Master CV from PDF | **Complete** | Upload a PDF resume; LLM extracts it into master-CV JSON in the background |
| PDF generator | **Complete** | WeasyPrint + Jinja2, three templates (`modern`/`compact`/`profile-card`) |
| Preparation workflow | **Complete** | extract → filter → compose → PDF → persist |
| Retry workflow | **Complete** | Re-compose CV with your feedback, back to the review queue |
| HITL review UI | **Complete** | Tinder-like batch review, keyboard-driven |
| Applications page | **Complete** | Filterable table of every job, per-status stats, CV download, Proceed Anyway |
| Multi-user auth | **Complete** | Magic link (Resend) + JWT cookie, open registration, per-user data scoping |
| Admin dashboard | **Complete** | Jobs, queue, errors, user roles at `/admin` (admin role required) |
| Notification centre | **Complete** | Persistent per-user notifications with unread badge |
| Failure recovery | **Complete** | Scrape-failure retries with backoff + startup recovery of in-flight jobs |
| Operational alerts | **Complete** | Emails the admin when the LinkedIn session looks unauthenticated |
| VPS deployment | **Complete** | GH Actions → GHCR → Docker Compose + Caddy TLS |
| **Auto-apply / Easy Apply** | **Not implemented** | No application workflow. Approve = "mark reviewed"; you apply by hand |

## Architecture

```
  ┌──────────────────────────────────────────────────────────────────────────┐
  │  DISCOVERY (per user, hourly — APScheduler)                              │
  │  LinkedIn search URL ─► scrape cards ─► scrape detail pages ─► dedup     │
  │  Every discovered job is persisted immediately as `queued`.              │
  └──────────────────────────────────────────────────────────────────────────┘
                                    │  (asyncio JobQueue)
                                    ▼
  ┌──────────────────────────────────────────────────────────────────────────┐
  │  PREPARATION WORKFLOW (LangGraph, async)                                 │
  │                                                                          │
  │  extract_job ─┬─► save_scrape_failed ──► END   (description too short)   │
  │               │                                                          │
  │               └─► filter_job ─┬─► save_filtered_out ──► END  (score low) │
  │                               │                                          │
  │                               └─► compose_cv ─► generate_pdf ─► save_to_db│
  └──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                       ┌───────────────────────────┐
                       │  HITL BOUNDARY            │
                       │  (Tinder-like batch UI)   │
                       │                           │
                       │  1  Decline (+ reason)    │
                       │  2  Retry   (+ feedback)  │
                       │  3  Mark reviewed + open  │
                       │     the job in LinkedIn   │
                       └───────────────────────────┘
                                    │
              ┌─────────────────────┼──────────────────────┐
              ▼                     ▼                      ▼
   ┌────────────────────┐ ┌───────────────────┐ ┌────────────────────────┐
   │ APPROVED           │ │ RETRY WORKFLOW    │ │ DECLINED               │
   │ terminal for now — │ │ re-compose CV w/  │ │ terminal; the reason   │
   │ you apply manually │ │ feedback, back to │ │ feeds the auto-refiner │
   │ in LinkedIn        │ │ `pending`         │ │                        │
   └────────────────────┘ └───────────────────┘ └────────────────────────┘
```

There is deliberately **no application workflow module** — the stubs were deleted rather than left
to rot. When browser-based applying is built, it hangs off the `approved` state.

## Tech Stack

- **Workflow**: LangGraph (async state machines)
- **Backend**: FastAPI, Pydantic v2
- **Frontend**: SvelteKit 2 / Svelte 5 (runes), Vite 7, TailwindCSS v3, TypeScript
- **LLM**: Instructor + LiteLLM — one client, four providers (OpenAI, Anthropic, DeepSeek, Grok)
- **PDF out**: WeasyPrint + Jinja2 · **PDF in**: pypdf + LLM extraction
- **Database**: SQLite via Piccolo ORM (or in-memory for dev)
- **Scheduling**: APScheduler (LinkedIn search, filter refinement, model-catalog refresh)
- **Browser automation**: Playwright + playwright-stealth (scraping only)
- **Email**: Resend (magic links, admin alerts)
- **Deployment**: Docker Compose + Caddy on a VPS, images built by GitHub Actions

## Getting Started

Requires [uv](https://docs.astral.sh/uv/) and Node 20+. `pyproject.toml` allows Python 3.11+;
`.python-version` pins 3.12, which is what `uv sync` will fetch.

```bash
uv sync                              # install Python deps (uv, not pip)
uv run playwright install chromium    # only needed for LinkedIn scraping
cp .env.example .env                  # then fill in the secrets
cd ui && npm install && cd ..
```

Minimum viable `.env`: one LLM API key, plus `JWT_SECRET` (`openssl rand -hex 32`). For local
browser/UX work, `DEV_AUTH_BYPASS=true` skips the email round-trip entirely.

PDF generation needs WeasyPrint's native libraries, which pip/uv can't install for you — on macOS
that's `brew install pango gdk-pixbuf libffi` plus `export DYLD_LIBRARY_PATH=/opt/homebrew/lib`.
Without them, PDF rendering raises and its tests skip. Note the trade-off in **Things Worth
Knowing**: that same variable crashes Chromium, so the browser code strips it.

Run the two servers:

```bash
uv run uvicorn src.api.main:app --reload    # API  → http://localhost:8000
cd ui && npm run dev                        # UI   → http://localhost:5173
```

On **Windows** only, `.\scripts\dev.ps1` does both and kills previous instances first. It relies on
`Get-NetTCPConnection` and `Win32_Process`, so it does not work under `pwsh` on macOS or Linux.

### First admin

Sign-ups default to the `trial` role. To unlock `/admin`:

```bash
uv run python scripts/promote_user.py --email you@example.com --role admin
```

Also accepts `--role trial|premium` and `--list-admins`. The API refuses to demote the last
remaining admin.

### Development commands

```bash
uv run pytest -m "not e2e"          # everyday loop: ~820 unit tests, ~9s
uv run pytest -m e2e                # Playwright E2E — auto-starts its own API + Vite servers
uv run ruff check src/ tests/       # lint
uv run black src/                   # format (line length 100)
uv run mypy src/                    # type check
```

A bare `uv run pytest` includes the E2E tier, so it needs `playwright install chromium` and
`ui/node_modules`. The eval tier (real LLM quality checks) is skipped unless you explicitly install
`deepeval`. See `docs/testing_guide.md`.

## Things Worth Knowing

Behaviour that surprises people, and that the code alone doesn't advertise:

- **Persistence is opt-in.** `REPO_TYPE` defaults to `memory`, so a restart loses every job unless
  you set `REPO_TYPE=sqlite`.
- **"Approve" does not apply.** It records `approved` and opens the LinkedIn tab. Nothing is
  submitted on your behalf, anywhere.
- **Jobs are persisted at discovery, not at completion.** A crash mid-pipeline leaves a visible
  `queued`/`processing` row rather than a silent gap; startup recovery re-enqueues those rows
  (bounded by a `recovery_attempts` cap so poison rows can't loop forever).
- **Filtered-out jobs are kept, not dropped.** They land in the terminal `filtered_out` state with
  the LLM's reasoning attached, and "Proceed Anyway" on the Applications page overrides the filter
  and pushes the job into CV generation regardless.
- **Empty descriptions are their own failure mode.** A detail page yielding fewer than
  `SCRAPER_MIN_DESCRIPTION_CHARS` (200) characters becomes `scrape_failed` — retry-eligible, capped
  at `SCRAPER_MAX_ATTEMPTS` and rate-limited by `SCRAPER_RETRY_BACKOFF_MINUTES`, so a permanently
  broken posting doesn't churn on every scheduler tick.
- **Mass-empty descriptions mean your LinkedIn session died.** When ≥50% of a batch of ≥5 detail
  pages come back empty, the system emails `ADMIN_ALERT_EMAIL` (cooldown-throttled, state persisted
  across restarts) because that pattern means the `li_at` cookie is stale. Cookie re-capture is
  currently a manual step, and a replayed `li_at` is often rejected outright by LinkedIn — this is
  the most fragile part of the system.
- **Comma-separated keywords are translated to boolean OR.** LinkedIn treats commas as literal text,
  so `Junior Accountant, Finance Assistant` is rewritten to
  `"Junior Accountant" OR "Finance Assistant"`. A query already using `OR`/`AND`/`NOT`, quotes, or
  parentheses is passed through untouched.
- **The auto-refiner never edits your prompt.** It proposes a replacement for the auto-learned block
  of your filter prompt and notifies you; the change only lands when you accept it in Settings. Each
  decline/override signal feeds the refiner exactly once.
- **Search concurrency is per user.** One user's slow or failing search neither blocks nor cancels
  another's, and a manual run is never dropped because a scheduled run is in flight.
- **The model list is fetched, not hardcoded.** It comes from LiteLLM's community pricing JSON
  (cached 24h at `data/model_catalog_cache.json`), which is why the dropdowns show current models
  and live prices. The static `MODEL_CATALOG` is only the offline fallback.
- **Your master CV lives in the database**, not on disk. `data/cv/` is a legacy path; generated PDFs
  go to `data/generated_cvs/{user_id}/{job_id}.pdf`.
- **State transitions are enforced.** Both repository implementations validate against
  `ALLOWED_TRANSITIONS` and raise `InvalidStateTransitionError`, so a late failure handler cannot
  clobber a terminal `completed`.
- **`DYLD_LIBRARY_PATH` cuts both ways on macOS.** WeasyPrint needs `/opt/homebrew/lib` on it to find
  gobject; Chromium *crashes* when it is set. The browser code strips `DYLD_*` from its subprocess
  environment for exactly this reason (a no-op on Linux). PDF failing with a library error → export
  it. Browser dying instantly → that stripping is what saves you.

## API Surface

Routers live in `src/api/routes/` — read those for the authoritative list. By group:

| Group | Prefix | Auth |
|-------|--------|------|
| Auth (magic link, JWT cookie, dev-login) | `/api/auth/*` | public |
| User profile, master CV, search/filter/model prefs, refinement | `/api/users/me/*` | user |
| Job submit, list, stats, status, PDF/HTML, proceed, delete, LinkedIn search | `/api/jobs/*` | user |
| HITL pending / decide / history | `/api/hitl/*` | user |
| Notifications + unread count | `/api/notifications/*` | user |
| Admin jobs, queue, errors, scheduler, user roles | `/api/admin/*` | admin |
| Health, LLM model catalog | `/api/health`, `/api/llm/models` | public |

## Documentation Map

| Doc | What's in it |
|-----|--------------|
| `implementation-plan.md` | Source of truth for requirements, architecture, and design decisions |
| `claude.md` | Design rationale, non-obvious contracts, and gotchas for AI assistants |
| `agents.md` | The workflow graph node by node |
| `src/llm/CLAUDE.md` | LLM layer internals: Instructor/LiteLLM, caching, model catalog |
| `docs/deployment.md` | VPS, DNS, TLS, deploys, rollback |
| `docs/testing_guide.md` | Test strategy, markers, how to run each tier |
| `docs/plans/` | Per-feature specs; `docs/plans/completed/` is historical |

## Roadmap

- [ ] Durable LinkedIn session auth (persistent browser profile instead of cookie replay)
- [ ] Browser automation for LinkedIn Easy Apply (the SDUI React modal — see
      `docs/plans/sdui-easy-apply-rework.md`)
- [ ] Telegram job notifications (`docs/plans/telegram-job-notifications.md`)

## License

All Rights Reserved. This code is provided for viewing purposes only. No permission is granted to
use, copy, modify, or distribute this software.

## Disclaimer

This tool is for personal use. Always comply with LinkedIn's Terms of Service. Automated scraping
and applications may violate platform policies — use responsibly.
