# Feature Specification: LinkedIn Apply Chrome Extension

## Overview
- **Feature**: MV3 Chrome Extension for Easy-Apply auto-submission from the HITL UI
- **Status**: Draft
- **Created**: 2026-04-19
- **Author**: User + Claude Code

## Problem Statement

The backend's `application_workflow` is stubbed. Browser-side Playwright automation (`src/services/linkedin/browser_automation.py`) is brittle against LinkedIn anti-automation defenses, cookie/session drift, and cannot reuse the user's real logged-in session. For the **Easy Apply** subset of jobs, the user's own Chrome session is the highest-signal apply channel. A companion Manifest V3 Chrome extension, triggered from the HITL UI on approval, can open the LinkedIn job in an inactive tab, fill the Easy Apply form from a stored answer bank (with LLM fallback), submit, and report the outcome back to the backend so the job-lifecycle state machine correctly transitions `applying → applied | failed`.

Non-Easy-Apply jobs (external career-site redirects) and the deep-agent branch of `application_workflow` remain stubbed.

## Goals & Success Criteria

- When an EA-eligible job is approved in the HITL UI with the extension installed, the apply completes without further user interaction and the job transitions to `applied`.
- When apply cannot complete (unknown field, login wall, validation, captcha, timeout), the job transitions to `failed` with a machine-readable outcome code and a human-readable reason.
- The extension is queue-paced to minimize LinkedIn anti-automation signal: at most one apply in flight, minimum inter-apply jitter.
- The backend records enough signal (outcome code, offending field label for unknown-field failures) that the user can iteratively grow their `application_answers_json` store and reduce future failures.
- **Success metric**: for EA jobs approved in HITL with the extension active, ≥70% reach `applied` on the first attempt after one week of answer-store seeding.

## User Stories

1. As a job seeker running the app locally, I want approved Easy-Apply jobs to apply themselves without my intervention, so I can batch-review in HITL and walk away.
2. As a user, I want the extension to reuse my real LinkedIn browser session, so I don't manage a second cookie jar or worry about headless detection.
3. As a user, when an apply fails because of an unknown question, I want the extension to tell me which question failed, so I can add the answer once and have future applies succeed.
4. As a user, I want the extension's auto-apply cadence throttled, so I don't get my LinkedIn account flagged for rapid-fire applications.
5. As the product owner, I want the extension's outcomes to drive the existing job state machine, so `application_workflow` becomes a real terminal state rather than a stub.

## Functional Requirements

### Core Capabilities

- **EA gating**: extension auto-apply runs only for jobs with `is_easy_apply=true` on the `JobRecord`.
- **Automatic trigger on approve**: the HITL UI's Approve button, for EA jobs when the extension marker is present in DOM, fires an extension message instead of (or in addition to) the normal approve flow.
- **Inactive-tab open**: extension service worker opens the LinkedIn job URL with `chrome.tabs.create({ active: false })`.
- **Readiness detection**: injected page script verifies logged-in state, absence of auth/captcha walls, and presence of the Easy Apply button before proceeding.
- **Single-pass form fill**: page script iterates visible fields, fills from stored answers, falls back to a backend LLM endpoint for unknowns, clicks Next/Submit, recurses until the modal closes or an unknown field/validation error is hit.
- **PDF upload**: the tailored CV PDF (fetched from `GET /api/jobs/{id}/pdf`) is attached to the Easy Apply resume field, replacing LinkedIn's default resume for this application. *(In scope — not excluded in Phase 4.)*
- **Truly silent submit**: no confirmation dialog; once all fields are filled the page script clicks Submit.
- **Outcome report**: service worker `POST`s to the backend the outcome code + reason + (for unknown-field failures) the offending question label.
- **Queue & pacing**: approved EA jobs land in `chrome.storage.local` queue; worker processes one at a time, with inter-apply jitter (30–90s) and a configurable daily cap. Safety outcomes (captcha, rate-limit interstitial) pause the queue globally.

### User Flows

**Happy path (single job)**
1. User clicks **Approve** on an EA job card in the HITL UI.
2. UI detects the extension marker (`<meta name="linkedin-apply-extension">`) and the `is_easy_apply` flag. It calls the normal `POST /api/hitl/{job_id}/decide` (approved) AND posts a `window.postMessage` payload that the extension content script relays to its service worker: `{jobId, jobUrl, masterCvUrl, answersUrl, pdfUrl}`.
3. Service worker enqueues the job in `chrome.storage.local`.
4. Worker dequeues (respecting pacing), opens `jobUrl` in an inactive tab.
5. `webNavigation.onCompleted` / `onHistoryStateUpdated` fires; worker injects `linkedin-apply.js` via `chrome.scripting.executeScript`.
6. Page script snapshot confirms logged-in + EA button present.
7. Page script clicks Easy Apply, iterates modal steps: fills fields from answers, calls backend for unknowns, uploads PDF at the resume step, clicks Next, until Submit.
8. Page script detects confirmation screen, sends `{outcome: "success"}` to service worker.
9. Service worker `POST /api/jobs/{job_id}/application-result` with `{status: "success"}`.
10. Backend transitions `applying → applied`.
11. Service worker closes the tab, moves to next queue item after jitter.

**Unknown-field failure**
- Page script encounters a field it cannot resolve locally, sends label to worker, worker calls `POST /api/jobs/{id}/answer-question`, backend LLM produces answer using master CV + job context, returns confidence score.
- If confidence is below threshold, page script aborts with outcome `failed_unknown_field` and the offending label. Worker `POST`s result, backend flips state to `failed` with the label surfaced to the HITL UI so the user can add the answer to `application_answers_json`.

**Login wall / captcha**
- Readiness detector finds a login or captcha interstitial. Page script reports `failed_login_wall` or `failed_captcha`. Worker pauses the queue globally, reports result. User must resolve by logging into LinkedIn manually; extension options page exposes a "Resume queue" button.

**User cancellation**
- If the LinkedIn tab is closed by the user before completion, worker detects `tabs.onRemoved` for the tracked tab ID and reports `user_cancelled`. Backend leaves the job in `approved` state (not `failed`).

### Data Model

**New backend fields / tables**

```python
# src/models/job.py (or unified.py) — JobRecord extensions
is_easy_apply: bool = False                    # set by scraper
application_outcome_code: str | None = None    # e.g. "success", "failed_unknown_field"
application_outcome_reason: str | None = None  # human-readable; includes field label for unknown_field
application_attempts: int = 0

# src/models/user.py — User extension
application_answers_json: dict[str, str] | None = None
# Keys are normalized question labels (e.g. "years_of_experience", "work_auth_us");
# values are the stored answers. Initially written via Settings UI; grown by LLM fallback results.
```

**State machine additions** (`src/models/state_machine.py`)
- No new states required. `applying → applied` and `applying → failed` already exist.
- `approved → applying` transition must be wired: triggered when the extension reports it has dequeued and started processing a job, via `POST /api/jobs/{id}/application-result` with `{status: "started"}`, OR more simply by the extension's first outcome call. Confirm during implementation whether to expose a `started` sub-status or route everything through the terminal-result endpoint.

**Extension-side state (`chrome.storage.local`)**
- `queue`: array of `{jobId, jobUrl, enqueuedAt}` pending apply
- `inFlight`: `{jobId, tabId, startedAt}` current job, for crash recovery
- `lastAppliedAt`: timestamp for jitter calculation
- `queuePaused`: bool, set when a safety outcome is hit

**Extension-side per-navigation state (`chrome.storage.session`)**
- `pending:<tabId>` → `{jobId, jobUrl, stage}` so webNavigation handlers can correlate events with queued work across worker suspension.

### Integration Points

- **`JobRepository`**: add `is_easy_apply` column (both in-memory and Piccolo SQLite tables). Populate from `linkedin_scraper.py` during scraping (`linkedin_scraper.py` already detects the Easy Apply badge on search results).
- **`UserRepository`**: add `application_answers_json` accessor + update methods.
- **`src/api/main.py`**: three new endpoints (see API section).
- **`application_workflow.py`**: remains stubbed for non-EA jobs; for EA jobs the flow lives entirely in the API layer (extension-driven state transitions, per Phase 3 decision).
- **Settings UI (`ui/src/lib/components/settings/`)**: new `ApplicationAnswersSection.svelte` for editing the answer bank (key/value pairs).
- **HITL UI**: Approve button logic checks `job.is_easy_apply && extensionDetected`, and on click posts to the extension via `window.postMessage` after the normal `/api/hitl/decide` call returns.
- **CORS config**: add `chrome-extension://<EXTENSION_ID>` to allowed origins with credentials enabled, so the service worker can hit `/api/*` with the JWT cookie.

## Technical Design

### Architecture

```
┌────────── HITL UI (Svelte, localhost:5173) ──────────┐
│  Approve click on EA job + extension marker present   │
│     ├── POST /api/hitl/{id}/decide (approved)         │
│     └── window.postMessage({type:'ENQUEUE_APPLY', …}) │
└────────────────────────┬──────────────────────────────┘
                         │
         ┌───────────────▼──────────────────┐
         │  Site content script (ui.js)     │
         │  relays postMessage → service    │
         │  worker via chrome.runtime       │
         └───────────────┬──────────────────┘
                         │
         ┌───────────────▼──────────────────┐
         │  Service worker (sw.js)          │
         │  queue + pacing + navigation     │
         │  events + backend reporting      │
         └──────┬────────────────┬──────────┘
                │                │
     chrome.tabs.create         fetch to /api/*
     (active:false, LinkedIn)   (JWT cookie)
                │                │
         ┌──────▼─────────┐      │
         │ LinkedIn tab   │      │
         │ + injected     │      │
         │ linkedin-apply │      │
         │ page script    │      │
         └────────────────┘      │
                                 ▼
                        FastAPI backend
                        (state machine, LLM answer, PDF)
```

### Technology Stack

- **Extension**: MV3, plain JavaScript (no build step), service worker type `module`.
- **APIs used**: `chrome.runtime`, `chrome.tabs`, `chrome.scripting`, `chrome.webNavigation`, `chrome.storage` (local + session).
- **Auth**: JWT cookie (`credentials: 'include'`) shared with the HITL UI origin.
- **Backend additions**: FastAPI endpoints using existing `get_current_user` dependency. LLM answer generation reuses `LLMClientFactory` from `src/llm/provider.py`.

### Data Persistence

- Extension: `chrome.storage.local` for durable queue and answer cache; `chrome.storage.session` for per-tab navigation state. Globals in the service worker are **never** relied on (MV3 worker is short-lived).
- Backend: Piccolo SQLite migration adds `is_easy_apply`, `application_outcome_code`, `application_outcome_reason`, `application_attempts` to the job table; adds `application_answers_json` to the user table.

### API / Interface Design

**New endpoints** (all require auth, user-scoped)

```
POST /api/jobs/{job_id}/application-result
  body: {status: "success" | "failed", outcome_code: str, reason: str | None,
         offending_field_label: str | None}
  effect: validates ownership; validates state transition (applying → applied/failed
          or approved → applying → applied/failed); updates JobRecord;
          returns {state: BusinessState, next_allowed_at: ISO8601 | null}

POST /api/jobs/{job_id}/answer-question
  body: {field_label: str, field_type: "text"|"radio"|"select"|"number"|"checkbox",
         options: list[str] | None, context: str | None}
  effect: uses master CV + application_answers_json + job description via LLM to
          generate an answer; persists answer back to application_answers_json
          if confidence >= threshold; returns {answer: str, confidence: float,
          stored: bool}

GET /api/users/me/application-answers
PUT /api/users/me/application-answers
  (CRUD for the stored answer bank; backs the Settings UI section)
```

**Extension ↔ page script message protocol**

```js
// UI → content script (window.postMessage)
{type: 'ENQUEUE_APPLY', jobId, jobUrl, apiBase}

// content script → service worker (runtime.sendMessage)
{type: 'ENQUEUE_APPLY', jobId, jobUrl, apiBase, sourceTabId}

// page script → service worker (runtime.sendMessage)
{type: 'APPLY_SNAPSHOT', tabId, snapshot}           // readiness report
{type: 'APPLY_ASK_ANSWER', tabId, field}            // LLM fallback request
{type: 'APPLY_RESULT', tabId, outcome_code, reason, offending_field_label}
```

**HITL UI detection marker** (extension content script on UI origin)

```html
<meta name="linkedin-apply-extension" content="0.1.0">
```

### Manifest skeleton

```json
{
  "manifest_version": 3,
  "name": "LinkedIn Apply Agent",
  "version": "0.1.0",
  "background": {"service_worker": "sw.js", "type": "module"},
  "permissions": ["storage", "scripting", "webNavigation"],
  "host_permissions": [
    "http://localhost:5173/*",
    "http://localhost:8000/*",
    "https://www.linkedin.com/*"
  ],
  "content_scripts": [
    {"matches": ["http://localhost:5173/*"],
     "js": ["content-ui.js"], "run_at": "document_idle"}
  ]
}
```

Host permissions are listed explicitly; `activeTab` is deliberately not used because the workflow opens background tabs. `tabs` permission is omitted (not required by `tabs.create`).

## Non-Functional Requirements

- **Performance**: fill-and-submit for a typical 2–3 step Easy Apply should complete within 30 s wall-clock per job.
- **Security**:
  - Service worker validates every incoming URL (origin must be `https://www.linkedin.com`, path must begin `/jobs/`) before calling `tabs.create`.
  - CORS on the backend allows `chrome-extension://<EXTENSION_ID>` with credentials; no wildcard.
  - Extension makes no cross-origin fetch to LinkedIn beyond the tab navigation — only same-origin page-script-initiated requests.
  - Messages from the page script to the service worker are treated as untrusted input; any field labels forwarded to the backend LLM are treated as untrusted context in the prompt.
- **Observability**:
  - Every state transition writes a structured log line with `{job_id, user_id, outcome_code, reason, offending_field_label}`.
  - `application_attempts` increments per terminal result; supports dashboards/alerting on failure-rate spikes.
- **Error handling**:
  - All safety outcomes (captcha, rate-limit interstitial, login wall) pause the in-extension queue. User resolves in the extension options page.
  - Worker suspension mid-apply: on next wake, `inFlight` is detected; worker checks the LinkedIn tab still exists and reinjects detection. If the tab is gone, reports `user_cancelled`.
  - Timeout: page script watchdog (120 s since modal open) reports `failed_timeout`.

## Implementation Considerations

### Design Trade-offs

- **Truly silent submit vs. confirm-on-unknown**: user chose truly silent. Risk: synthetic clicks have `isTrusted=false` and LinkedIn may detect them; a mis-filled field is submitted without user review. Mitigation: single-pass best-effort means unknown fields abort the apply rather than guessing; abort-on-low-LLM-confidence (threshold to be tuned) prevents silent submission with fabricated answers.
- **Plain JS vs. TypeScript/Vite**: plain JS chosen for simplicity; loses type-sharing with the Svelte/TS frontend and the Pydantic backend. Manual discipline required to keep message schemas in sync; mitigate with a short hand-maintained `PROTOCOL.md` in `extension/`.
- **Extension-driven state transitions vs. workflow polling**: extension-driven chosen. Keeps the LangGraph workflow simple, but means the backend trusts extension-reported outcomes without independent verification. Acceptable because the user is the one running both the extension and the backend (single-user deployments), and the extension's outcome is as authoritative as Playwright's would be.
- **Automatic on approve vs. separate Apply button**: automatic chosen. One less click, but couples approve semantics with apply semantics — declining and re-approving a job will re-queue the apply. Mitigation: `application_attempts` counter and optional "already applied" guard on the backend.
- **JWT cookie vs. bearer token for extension → backend calls**: cookie chosen. Simplest and reuses UI auth, but requires explicit CORS allowlisting of the `chrome-extension://<id>` origin with credentials.
- **PDF upload in scope**: the tailored CV PDF (produced by `preparation_workflow`) will be uploaded into the Easy Apply resume field. Adds complexity (fetching the PDF as a Blob from `/api/jobs/{id}/pdf`, programmatically attaching to the file input via `DataTransfer` + `dispatchEvent('change')`) but preserves the core value of the pipeline — without this, LinkedIn uses whatever resume was last on file and the tailoring work is wasted.

### Dependencies

- `is_easy_apply` must actually be detectable by the existing `linkedin_scraper.py`. Quick audit needed during implementation; fall back to heuristic on the job detail page if search-results badge is unreliable.
- Extension ID is not fixed for dev-unpacked installs unless a `"key"` is added to the manifest. Pin it with a generated key so the backend CORS allowlist and UI's `chrome.runtime.sendMessage` target stay stable across reloads.
- LLM answer-question endpoint must tolerate poorly-structured field labels (LinkedIn's Easy Apply labels can be awkward); confidence calibration is a judgment call during prompt development.

### Testing Strategy

- **Backend unit tests**: new endpoints, state-transition validation, LLM answer-question with mocked LLM.
- **Extension unit tests**: service-worker URL validation, queue logic, pacing, outcome-code mapping. Run under `@vitest` or `jest` with `sinon-chrome` stub for Chrome APIs.
- **Integration test**: recorded/replayed LinkedIn DOM fixtures driving the page script in a jsdom harness — simulate multi-step modal, unknown-field path, login-wall path.
- **E2E**: manual first-run smoke test against a real LinkedIn account with a test job; add Playwright-based E2E later if recurring regressions appear.
- **Test E2E queue pacing**: unit-test the service worker's jitter logic with mocked timers.

## Out of Scope

- Non-Easy-Apply jobs (external career-site redirects) — remain stubbed in `application_workflow`.
- Deep-agent application workflow (Playwright MCP, LLM browser driver) — remains stubbed.
- Firefox, Safari, mobile LinkedIn, or LinkedIn mobile app.
- Chrome Web Store submission, signing, and auto-update. First version is dev-unpacked only.
- Sharing extension LinkedIn cookies with the backend's Playwright scraper.
- Multi-user extension sign-in flows — the extension trusts the cookie set by the HITL UI.
- Resuming a mid-flight apply across browser restarts (too complex for v1; on restart, mark `inFlight` as `failed_timeout` and let the user re-approve).
- Retrying `failed_unknown_field` applies after the user edits the answer store (the user must re-approve in HITL to re-queue).

## Open Questions

- **Extension ID stability**: generate a `"key"` for the manifest during implementation, or accept CORS reconfiguration on each dev reload until Chrome Web Store distribution lands.
- **`approved → applying` vs. `approved → applied` transition**: does the extension need to report a "started" sub-status, or is it acceptable for the job to stay in `approved` until the terminal result, then jump straight to `applied`/`failed`? Leaning toward the latter for v1 simplicity; revisit if observability suffers.
- **LLM confidence threshold**: what numeric cutoff distinguishes "use and persist" from "abort as `failed_unknown_field`"? Needs empirical tuning during implementation; start at 0.7.
- **Daily cap default**: 20/user/day was suggested. May need per-user override in settings or dynamic adaptation based on observed LinkedIn anti-automation responses.
- **PDF upload selector strategy**: LinkedIn's resume upload field is not a stable selector target. Strategy: find the `input[type=file]` whose accompanying label text or `aria-label` matches a localized "resume/CV" token set, verified against a small fixture library. Risk of breakage on LinkedIn UI changes is accepted.
- **Synthetic-click trust**: `HTMLElement.click()` produces `isTrusted=false` events. Spike during implementation: does LinkedIn's Easy Apply submit path reject untrusted clicks? If yes, the entire auto-submit design is undermined and we fall back to foreground-before-submit. This risk must be validated in a small spike before full implementation.

## References

- User-provided research brief (see chat history) — citations to Chrome docs on content scripts, service workers, `tabs.create`, `scripting.executeScript`, `webNavigation`, `chrome.storage` lifecycle, `externally_connectable`, `activeTab`, DOM `isTrusted`, and LinkedIn talent-product docs.
- `src/services/linkedin/linkedin_scraper.py` — existing EA detection source
- `src/services/linkedin/browser_automation.py` — Playwright reference implementation
- `src/agents/application_workflow.py` *(missing; currently only stubs exist per CLAUDE.md)*
- `src/models/state_machine.py` — `BusinessState`, `WorkflowStep`, `ALLOWED_TRANSITIONS`
- `src/api/main.py` — where the new endpoints will be added
- `ui/src/routes/+page.svelte` — HITL approve flow; extension trigger will be wired here
