# Deployment Guide — Fresh VPS

This guide walks you through deploying the LinkedIn Job Application Agent to a
bare-metal VPS (Ubuntu / Debian) for use by a small group (3–5 users). For the
Dockerised release pipeline see `docs/plans/vps-deployment.md`.

## 1. System Dependencies

The API uses WeasyPrint for PDF generation and Playwright (Chromium) for
LinkedIn scraping. Both need native libraries that are not bundled with the
Python wheels.

Debian / Ubuntu:

```bash
sudo apt update
sudo apt install -y \
    python3.11 python3.11-venv \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    libglib2.0-0 libcairo2 libgdk-pixbuf2.0-0 \
    fonts-liberation
```

macOS (Homebrew, for local prod-like runs):

```bash
brew install pango glib cairo gdk-pixbuf
# Apple Silicon only: WeasyPrint needs DYLD_LIBRARY_PATH set
export DYLD_LIBRARY_PATH=/opt/homebrew/lib
```

After WeasyPrint is wired up, `GET /api/health` returns
`{"pdf_ok": true, "pdf_error": null}`. If it returns `false`, the hint in
`pdf_error` names the missing library group (Pango / Cairo / GLib /
gdk-pixbuf).

## 2. Toolchain: `uv`

The project uses [`uv`](https://docs.astral.sh/uv/) as its Python package
manager — never `pip`.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Bootstrap the project:

```bash
git clone https://github.com/<owner>/linkedin-apply-ai-agent.git
cd linkedin-apply-ai-agent
uv sync
uv run playwright install chromium
```

## 3. Environment Variables

Copy `.env.example` to `.env` and fill in the values below. Anything not listed
has a sensible default in `src/config/settings.py`.

### Required for production

| Variable | Notes |
|---|---|
| `JWT_SECRET` | Generate with `openssl rand -hex 32`. Must be ≥ 32 chars; the placeholder `change-me-in-production` refuses to sign tokens at runtime. |
| `RESEND_API_KEY` | Magic-link email delivery (https://resend.com). |
| `RESEND_FROM` | A verified sender on your Resend domain, e.g. `Apply Agent <noreply@apply.example.com>`. |
| `APP_URL` | Public base URL of the UI (used in magic-link emails). e.g. `https://apply.example.com`. |
| `CORS_ORIGINS` | Comma-separated list of allowed browser origins. e.g. `https://apply.example.com`. The API logs a startup warning if `APP_URL` is non-local but `CORS_ORIGINS` only allows localhost. |
| `REPO_TYPE` | Set to `sqlite` (default `memory` is dev-only and loses data on restart). |
| `DB_PATH` | e.g. `./data/jobs.db`. Make sure the directory is writable. |
| `PRIMARY_LLM_PROVIDER` | One of `openai` / `deepseek` / `grok` / `anthropic`. |
| `<PROVIDER>_API_KEY` | The key matching the chosen primary provider. The API checks the key at startup; missing keys cause `POST /api/jobs/submit` to return `503 llm_not_configured` until the operator sets it (no restart needed for the next attempt). |

### Optional but recommended

| Variable | Notes |
|---|---|
| `FALLBACK_LLM_PROVIDER` | Used when the primary errors mid-workflow. |
| `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD` | Required for the LinkedIn scraper. The browser stores a session cookie under `data/linkedin_cookies.json` after the first login. |
| `LINKEDIN_SEARCH_SCHEDULE_ENABLED` | `true` to enable hourly per-user scheduled searches. |
| `JOB_FILTER_ENABLED` | `true` (default) to run the LLM filter before CV generation. |

## 4. First-Run Bootstrap

```bash
# 1. Create the data directory (SQLite + generated PDFs live here)
mkdir -p data/generated_cvs data/cv

# 2. Run the API (defaults to 0.0.0.0:8000)
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000

# 3. In a second terminal, build and serve the UI
cd ui
npm ci
npm run build
# Serve ui/build/ behind your reverse proxy of choice (nginx, Caddy, …).
```

For development you can run `npm run dev` instead — the dev server already
proxies API calls when `VITE_API_BASE_URL` is set.

## 5. Smoke Test

After bootstrap, walk through the first-user flow:

1. Open `APP_URL` in a browser → enter your email → receive magic link via
   Resend → click → land on the welcome screen authenticated.
2. Click **Set up your CV** → in the Settings page click **Load template** to
   populate the CV editor with a minimal valid skeleton → tweak the basics
   (name, email, one job entry) → **Save**.
3. Go to **Generate**, paste a LinkedIn job URL → **Submit**. The job
   progresses through `queued` → `processing` → `cv_ready` without hanging.
   If the LLM fails or stalls, the workflow aborts after
   `WORKFLOW_TIMEOUT_SECONDS` (default 300s) and the UI renders the
   `error_message` instead of polling forever.
4. Download the generated PDF.
5. Open the HITL review page → click **Approve** → the toast surfaces
   "Approved. Automatic application is not yet implemented — please apply via
   LinkedIn manually for now." The job moves to History as `approved`.

If any of these steps fail, check `GET /api/health` first — it surfaces
`pdf_ok`, `llm_ok`, and the queue consumer status.

## 6. LinkedIn Session Expiry

LinkedIn cookies typically last 24–72 hours. When a scheduled search hits the
sign-in redirect, the scheduler pauses itself (`state =
paused_auth_required`) instead of looping forever:

1. The Settings page shows a banner: *"LinkedIn session expired — refresh
   cookies"*.
2. Log into LinkedIn from the same browser the cookie persistence is wired
   to (or run `uv run playwright codegen` and overwrite
   `data/linkedin_cookies.json`).
3. Click **Clear after refresh** in the banner — calls
   `POST /api/jobs/linkedin-search/clear-auth-error`, scheduler resumes on
   the next tick.

## 7. Operating Notes

- **Workflow timeout**: `WORKFLOW_TIMEOUT_SECONDS=300` caps how long a single
  job can run. On timeout the job transitions to `failed` with
  `error_message="Workflow timed out after Ns"`.
- **Live LLM key rotation**: setting a new key in `.env` does *not* require a
  restart for `POST /api/jobs/submit` to recover — but the startup pre-flight
  warning won't re-run until the API is restarted, so the easiest path is to
  bounce the API process after editing `.env`.
- **Backups**: `data/jobs.db` (SQLite), `data/generated_cvs/`, and
  `data/linkedin_cookies.json` are the only stateful artefacts. Snapshot the
  `data/` directory.

## 8. Reference

- `CLAUDE.md` — architecture and implementation status.
- `docs/plans/vps-deployment.md` — Dockerised release pipeline (GHCR +
  Watchtower) for when you outgrow `uv run uvicorn`.
- `.env.example` — full list of supported env vars with inline comments.
