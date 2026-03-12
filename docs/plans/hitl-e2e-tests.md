# HITL UI End-to-End Tests

## Overview

Add Playwright E2E tests for the HITL review UI using a TDD approach: write failing tests that expose known bugs (PDF not generating, job description truncated by "read more", regenerate button broken), then fix the bugs. Tests run against real FastAPI backend with LLM calls mocked via `unittest.mock.patch`.

## Context

- Files involved:
  - `tests/e2e/test_hitl_review.py` (to be created — main E2E test file)
  - `tests/e2e/conftest.py` (to be created — fixtures: server startup, browser, LLM mock)
  - `tests/conftest.py` (existing — reuse `sample_master_cv`, `sample_job_posting` fixtures)
  - `tests/fixtures/` (existing — sample data)
  - `src/api/main.py` (existing — FastAPI app, may need fixes)
  - `src/agents/preparation_workflow.py` (existing — `_init_llm_client` to be patched)
  - `src/agents/retry_workflow.py` (existing — `_init_llm_client` to be patched)
  - `ui/src/lib/components/review/JobDescriptionPanel.svelte` (existing — may need fix for truncation)
  - `ui/src/lib/components/review/CVPreviewPanel.svelte` (existing — may need fix for PDF)
  - `ui/src/lib/api/hitl.ts` (existing — API client)
  - `ui/src/routes/+page.svelte` (existing — main review page, retry flow)
- Related patterns: Existing E2E tests in `tests/e2e/test_llm_dropdowns.py` use Playwright sync API with pytest
- Dependencies: `playwright` (already installed), `pytest-asyncio` (may need install), `httpx` (already installed)

## Development Approach

- **Testing approach**: TDD — write failing tests first, then fix bugs to make them pass
- **LLM mocking strategy**: Use `unittest.mock.patch` to replace `_init_llm_client` in both `preparation_workflow` and `retry_workflow` modules with a mock that returns canned CV JSON. This avoids any env var changes.
- **Server management**: Auto-start both FastAPI backend and Vite UI dev server in pytest fixtures (session-scoped). Kill on teardown.
- **Async**: Use Playwright async API with `pytest-asyncio` for cleaner async server management
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**

## Implementation Steps

### Task 1: E2E test infrastructure — fixtures for auto-starting servers with mocked LLM

**Files:**
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_hitl_review.py` (skeleton)
- Modify: `pytest.ini` (add `e2e` marker if missing)

- [x] Create `tests/e2e/conftest.py` with session-scoped fixtures:
  - `mock_llm_and_api_server` fixture: patches `src.agents.preparation_workflow._init_llm_client` and `src.agents.retry_workflow._init_llm_client` to return a mock `BaseLLMClient` that returns canned CV JSON from `tests/fixtures/`. Starts FastAPI via `uvicorn` in a subprocess on a free port. Waits for health check. Yields `api_url`. Kills process on teardown.
  - `ui_dev_server` fixture: runs `npm run dev -- --port {free_port}` in `ui/` directory as subprocess. Waits for the dev server to respond. Yields `ui_url`. Kills process on teardown.
  - `browser` fixture (session-scoped): launches Playwright Chromium, yields browser, closes on teardown.
  - `page` fixture (function-scoped): creates a new browser page per test, closes after.
  - Helper: `wait_for_server(url, timeout=30)` — polls until server responds or times out.
- [x] Create skeleton `tests/e2e/test_hitl_review.py` with a single smoke test: navigate to UI URL, assert "Review Applications" heading is visible.
- [x] Add `e2e` marker to `pytest.ini` if not already present.
- [x] Verify: run `pytest tests/e2e/test_hitl_review.py -v -m e2e` — smoke test passes with both servers auto-started.

### Task 2: Write failing tests for basic HITL flows (load pending, navigate, approve, decline)

**Files:**
- Modify: `tests/e2e/test_hitl_review.py`
- Modify: `tests/e2e/conftest.py` (add fixture to seed a job via API)

- [ ] Add `seed_pending_job` fixture: POST to `/api/jobs/submit` with `mode=full` and manual job input (reuse `sample_job_posting` data). Wait for status to reach `pending`. Return `job_id`.
- [ ] Write test `test_pending_jobs_displayed`: seed 1 job, navigate to UI, verify job card shows title, company, and "1 pending" badge.
- [ ] Write test `test_navigate_between_jobs`: seed 2 jobs, navigate to UI, verify "1 of 2" counter, click Next, verify "2 of 2", click Previous, verify "1 of 2".
- [ ] Write test `test_approve_job`: seed 1 job, navigate to UI, click Approve button, verify toast "Application Approved" appears, verify empty state shown after.
- [ ] Write test `test_decline_job`: seed 1 job, click Decline, enter optional feedback in modal, submit, verify toast "Application Declined", verify empty state.
- [ ] Run tests — verify they fail for the right reasons (e.g., pending job not appearing because LLM mock needs tuning). Fix fixture issues only, not app code.
- [ ] Run project test suite: `pytest tests/ -v --ignore=tests/eval` — must pass before task 3.

### Task 3: Write failing tests for known bugs — PDF generation, job description truncation, retry button

**Files:**
- Modify: `tests/e2e/test_hitl_review.py`

- [ ] Write test `test_pdf_download_works`: seed 1 job, navigate to UI, switch to CV tab, click "Download Full PDF" button, intercept the download or verify the response from `/api/jobs/{job_id}/pdf` returns 200 with `application/pdf` content type.
- [ ] Write test `test_cv_html_preview_loads`: seed 1 job, switch to CV tab, verify CV HTML content is rendered (not loading spinner, not error state).
- [ ] Write test `test_job_description_not_truncated`: seed 1 job with a long description (500+ words), navigate to UI, verify full description text is present in the DOM (no "read more" or "show more" truncation).
- [ ] Write test `test_retry_regenerates_cv`: seed 1 job, click Retry button, enter feedback "Emphasize Python skills" in modal, submit, verify toast "CV Regeneration Started", wait for job to reappear in pending queue (poll `/api/hitl/pending`), verify retry_count incremented.
- [ ] Run tests — confirm they fail, documenting which bugs each test exposes.
- [ ] Run project test suite: `pytest tests/ -v --ignore=tests/eval` — must pass before task 4.

### Task 4: Fix bug — PDF not generating / not downloadable

**Files:**
- Modify: `src/api/main.py` (PDF endpoint fixes if needed)
- Modify: `src/agents/preparation_workflow.py` (PDF generation node fixes if needed)
- Modify: `ui/src/lib/components/review/CVPreviewPanel.svelte` (if UI-side issue)

- [ ] Investigate why PDF download fails: check if `pdf_path` is set correctly in JobRecord, if the file exists on disk, if the `/api/jobs/{id}/pdf` endpoint returns the file correctly.
- [ ] Fix the root cause (likely: PDF path not persisted, or file not found at expected path).
- [ ] Fix the CV HTML preview endpoint if it also fails (`/api/jobs/{id}/html`).
- [ ] Verify `test_pdf_download_works` and `test_cv_html_preview_loads` now pass.
- [ ] Run project test suite: `pytest tests/ -v --ignore=tests/eval` — must pass before task 5.

### Task 5: Fix bug — job description truncated by "read more"

**Files:**
- Modify: `ui/src/lib/components/review/JobDescriptionPanel.svelte` (if CSS truncation)
- Modify: `src/agents/preparation_workflow.py` or `src/services/linkedin_scraper.py` (if description fetched truncated)
- Modify: `src/api/main.py` (if API truncates description)

- [ ] Investigate where truncation happens: check if the LinkedIn scraper fetches truncated descriptions, if the API response truncates, or if the UI has CSS that clips content.
- [ ] Fix the root cause. If it's the scraper not clicking "read more" on LinkedIn, fix `linkedin_scraper.py`. If it's CSS, fix the Svelte component.
- [ ] Verify `test_job_description_not_truncated` now passes.
- [ ] Run project test suite: `pytest tests/ -v --ignore=tests/eval` — must pass before task 6.

### Task 6: Fix bug — regenerate/retry button doesn't work

**Files:**
- Modify: `src/api/main.py` (retry endpoint fix if needed)
- Modify: `src/agents/retry_workflow.py` (retry workflow fix if needed)
- Modify: `ui/src/routes/+page.svelte` or `ui/src/lib/stores/reviewQueue.svelte.ts` (if UI-side issue)

- [ ] Investigate retry failure: check if the `/api/hitl/{id}/decide` endpoint with `decision=retry` returns an error, if the retry workflow fails to start, or if the UI doesn't handle the response correctly.
- [ ] Fix the root cause (likely: retry workflow can't load job from repository, or state management issue in the store after retry).
- [ ] Verify `test_retry_regenerates_cv` now passes.
- [ ] Run project test suite: `pytest tests/ -v --ignore=tests/eval` — must pass before task 7.

### Task 7: Verify acceptance criteria

- [ ] Run all HITL E2E tests: `pytest tests/e2e/test_hitl_review.py -v`
- [ ] Verify all 8+ tests pass:
  - Smoke test (page loads)
  - Pending jobs displayed
  - Navigation between jobs
  - Approve flow
  - Decline flow
  - PDF download
  - Job description not truncated
  - Retry/regenerate flow
- [ ] Run full test suite: `pytest tests/ -v --ignore=tests/eval`
- [ ] Run linter: `black --check src/ tests/`

### Task 8: Update documentation

- [ ] Update CLAUDE.md testing section to mention HITL E2E tests and how to run them
- [ ] Add run instructions to `tests/e2e/test_hitl_review.py` docstring (similar to `test_llm_dropdowns.py`)
- [ ] Move this plan to `docs/plans/completed/`
