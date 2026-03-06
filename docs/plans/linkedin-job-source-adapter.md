# LinkedIn Job Source Adapter - Implementation Plan

## Overview

Implement a LinkedIn Job Source Adapter that scrapes job listings from LinkedIn search results using Playwright with stealth anti-detection, feeds them through an async queue into the existing preparation workflow, and supports both manual API triggers and scheduled execution.

## Context

- Files involved:
  - `src/services/job_source.py` (existing — `LinkedInJobAdapter` skeleton, `JobSourceFactory`)
  - `src/services/browser_automation.py` (existing — `LinkedInAutomation` skeleton with Playwright)
  - `src/config/settings.py` (existing — has `linkedin_email`, `linkedin_password`, `browser_headless`)
  - `src/models/job.py` (existing — `JobPosting` model)
  - `src/models/unified.py` (existing — `JobRecord`, status lifecycle)
  - `src/agents/preparation_workflow.py` (existing — preparation pipeline)
  - `src/api/main.py` (existing — FastAPI endpoints)
  - `src/services/linkedin_scraper.py` (to be created — core scraping logic)
  - `src/services/linkedin_search.py` (to be created — search URL builder + filters)
  - `src/services/job_queue.py` (to be created — async job queue)
  - `src/services/scheduler.py` (to be created — APScheduler integration)
  - `tests/test_linkedin_scraper.py` (to be created)
  - `tests/test_linkedin_search.py` (to be created)
  - `tests/test_job_queue.py` (to be created)
  - `tests/test_scheduler.py` (to be created)
- Related patterns: `JobSourceAdapter` ABC, `JobSourceFactory` factory pattern, async Playwright API, Pydantic models
- Dependencies:
  - `playwright` (already installed)
  - `playwright-stealth` (NEW — anti-detection plugin for Playwright)
  - `apscheduler` (already installed)
  - `httpx` (already installed)

## Development Approach

- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- Follow existing `JobSourceAdapter` ABC pattern
- Use Playwright async API consistently (already project standard)
- All browser interactions must use stealth + human-like delays
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**

## Implementation Steps

### Task 1: LinkedIn search URL builder and filter models

**Files:**
- Create: `src/services/linkedin_search.py`
- Modify: `src/config/settings.py`
- Create: `tests/test_linkedin_search.py`

- [x] Add LinkedIn search settings to `Settings` in `src/config/settings.py`:
  - `linkedin_search_keywords: str = ""` — search keywords
  - `linkedin_search_location: str = ""` — location filter
  - `linkedin_search_remote_filter: Optional[str] = None` — "remote", "on-site", "hybrid"
  - `linkedin_search_date_posted: Optional[str] = None` — "24h", "week", "month"
  - `linkedin_search_experience_level: Optional[list[str]] = None` — "entry", "associate", "mid-senior", "director", "executive"
  - `linkedin_search_job_type: Optional[list[str]] = None` — "full-time", "part-time", "contract", "temporary", "internship"
  - `linkedin_search_easy_apply_only: bool = False`
  - `linkedin_search_max_jobs: int = 50` — max jobs per search session
  - `linkedin_session_cookie_path: str = "./data/linkedin_cookies.json"` — cookie persistence path
  - `linkedin_min_delay: float = 3.0` — min delay between actions (seconds)
  - `linkedin_max_delay: float = 8.0` — max delay between actions (seconds)
  - `linkedin_page_delay_min: float = 2.0` — min delay between pages
  - `linkedin_page_delay_max: float = 5.0` — max delay between pages
- [x] Create `src/services/linkedin_search.py` with:
  - `LinkedInSearchParams` Pydantic model encapsulating all search filters
  - `LinkedInSearchURLBuilder` class with `build_url(params: LinkedInSearchParams, page: int = 0) -> str` that constructs LinkedIn job search URLs with proper query parameters (keywords, location, f_TPR for date, f_WT for remote, f_E for experience, f_JT for job type, f_AL for Easy Apply)
  - `LinkedInSearchURLBuilder.build_url_from_settings(settings, page: int = 0) -> str` convenience method
- [x] Write unit tests for URL builder: verify correct query parameter encoding for each filter combination, edge cases (empty filters, multiple experience levels)
- [x] Run project test suite - must pass before task 2

### Task 2: Stealth browser manager with cookie-based auth

**Files:**
- Modify: `src/services/browser_automation.py`
- Modify: `pyproject.toml`
- Create: `tests/test_browser_automation.py`

- [x] Add `playwright-stealth` to `pyproject.toml` dependencies
- [x] Rewrite `LinkedInAutomation` in `src/services/browser_automation.py`:
  - `__init__(self, settings)` — accept Settings object, store credentials and config
  - `async initialize(self) -> None` — launch Playwright Chromium with stealth: apply `playwright-stealth` patches, set realistic viewport (randomized 1280-1920 width), set user-agent, disable webdriver flag
  - `async _load_cookies(self) -> bool` — load cookies from `linkedin_session_cookie_path` JSON file, inject into browser context, return True if cookies loaded and session is valid
  - `async _save_cookies(self) -> None` — save current browser context cookies to JSON file
  - `async _validate_session(self) -> bool` — navigate to `linkedin.com/feed`, check if redirected to login page (session expired) or feed loads (session valid)
  - `async login(self) -> None` — automated login: navigate to linkedin.com/login, fill email/password with human-like typing delays (50-150ms per keystroke), click sign-in, handle potential security challenge page (log warning), save cookies on success
  - `async ensure_authenticated(self) -> None` — try cookie reuse first (`_load_cookies` + `_validate_session`), fall back to `login()` if session invalid, save cookies after successful auth
  - `async _random_delay(self, min_s: float = None, max_s: float = None) -> None` — sleep for random duration between min/max (defaults from settings)
  - `async _human_scroll(self, page) -> None` — scroll page with random increments and pauses to simulate human behavior
  - `async close(self) -> None` — save cookies before closing, then close browser
- [x] Write tests with mocked Playwright: test cookie load/save, test `ensure_authenticated` tries cookies first then falls back to login, test random delay ranges
- [x] Run project test suite - must pass before task 3

### Task 3: LinkedIn job scraper (search results parser)

**Files:**
- Create: `src/services/linkedin_scraper.py`
- Create: `tests/test_linkedin_scraper.py`

- [x] Create `src/services/linkedin_scraper.py` with class `LinkedInJobScraper`:
  - `__init__(self, browser: LinkedInAutomation, settings: Settings)` — store browser and settings references
  - `async scrape_search_results(self, search_params: LinkedInSearchParams) -> list[dict]` — main entry point:
    1. Build search URL via `LinkedInSearchURLBuilder`
    2. Navigate to search URL
    3. Parse job cards from search results page (selectors: `.jobs-search-results-list`, `.job-card-container`, or equivalent LinkedIn DOM selectors)
    4. For each job card, extract: job_id (from data attribute or URL), title, company, location, posted date, Easy Apply badge
    5. Paginate: scroll to load more results or click "next page", respect `linkedin_search_max_jobs` limit
    6. Between pages, call `_random_delay` and `_human_scroll`
    7. Return list of raw job dicts
  - `async scrape_job_details(self, job_url: str) -> dict` — navigate to individual job page, extract full description, requirements, salary (if shown), job type, experience level. Use `_random_delay` before navigation.
  - `async scrape_and_enrich(self, search_params: LinkedInSearchParams) -> list[dict]` — call `scrape_search_results`, then for each job call `scrape_job_details` to get full description. Returns list of enriched job dicts matching `JobPosting` field structure.
  - Internal: `_parse_job_card(self, card_element) -> dict` — extract data from a single job card DOM element
  - Internal: `_parse_job_detail_page(self, page) -> dict` — extract full details from job detail page
- [x] Add dedup logic: track seen job IDs within a session, skip already-scraped jobs
- [x] Write tests with mocked page HTML: test `_parse_job_card` with sample HTML fixture, test `_parse_job_detail_page` with sample fixture, test dedup logic, test max_jobs limit stops pagination
- [x] Run project test suite - must pass before task 4

### Task 4: Async job queue with workflow integration

**Files:**
- Create: `src/services/job_queue.py`
- Modify: `src/services/job_source.py`
- Modify: `src/agents/preparation_workflow.py`
- Create: `tests/test_job_queue.py`

- [x] Create `src/services/job_queue.py` with class `JobQueue`:
  - `__init__(self, max_size: int = 100)` — create `asyncio.Queue` with max size
  - `async put(self, job_data: dict) -> None` — enqueue a scraped job dict
  - `async get(self) -> dict` — dequeue next job
  - `async put_batch(self, jobs: list[dict]) -> int` — enqueue multiple jobs, return count added
  - `def size(self) -> int` — current queue size
  - `def is_empty(self) -> bool`
  - Global singleton: `_job_queue: JobQueue | None` with `get_job_queue() -> JobQueue` and `set_job_queue(queue)`
- [x] Update `LinkedInJobAdapter` in `src/services/job_source.py`:
  - Implement `extract(self, raw_input)` — normalize LinkedIn raw data dict into `JobPosting`-compatible dict. Map fields: `job_id` -> `id`, parse `posted_date`, set `url` to LinkedIn job URL, set `is_remote` based on location/remote flag, store original data in `raw_data`
  - Update `can_handle` to also accept `{"linkedin_url": str}` format
- [x] Add a `process_queue` async function (in `job_queue.py` or a new consumer module) that:
  1. Pulls jobs from queue one at a time
  2. For each job, creates a `PreparationWorkflowState` with `source="linkedin"`, `mode="full"`
  3. Runs the preparation workflow (extract -> filter -> compose CV -> PDF -> save to DB as pending)
  4. Logs success/failure per job
  5. Respects delay between workflow runs to avoid LLM rate limits
- [x] Write tests: test queue put/get ordering, test batch enqueue, test `LinkedInJobAdapter.extract` field mapping, test queue consumer processes jobs in order
- [x] Run project test suite - must pass before task 5

### Task 5: Scheduler and API endpoint for LinkedIn search

**Files:**
- Create: `src/services/scheduler.py`
- Modify: `src/api/main.py`
- Modify: `src/config/settings.py`
- Create: `tests/test_scheduler.py`

- [x] Add to `Settings`: `linkedin_search_schedule_enabled: bool = False`, `linkedin_search_interval_hours: int = 1`
- [x] Create `src/services/scheduler.py`:
  - `LinkedInSearchScheduler` class wrapping APScheduler `AsyncIOScheduler`
  - `__init__(self, settings, scraper, queue)` — store dependencies
  - `async run_search(self) -> int` — orchestrate one search cycle: call `browser.ensure_authenticated()`, call `scraper.scrape_and_enrich(params)`, enqueue results via `queue.put_batch(jobs)`, return job count. Catch and log all exceptions (never crash the scheduler).
  - `start(self) -> None` — add interval job to APScheduler based on `linkedin_search_interval_hours`, start scheduler
  - `stop(self) -> None` — shutdown scheduler gracefully
  - `async trigger_now(self) -> int` — manually trigger a search, return job count
- [x] Add API endpoint in `src/api/main.py`:
  - `POST /api/jobs/linkedin-search` — trigger a LinkedIn search manually. Accept optional JSON body with search param overrides. Return `{"status": "started", "message": "LinkedIn search triggered"}` immediately (run search in background task). On completion, log results.
  - `GET /api/jobs/linkedin-search/status` — return current scheduler state: enabled/disabled, last run time, jobs found in last run, next scheduled run
- [x] Wire up scheduler startup in FastAPI `lifespan` event: if `linkedin_search_schedule_enabled`, initialize browser, scraper, queue, scheduler, and start. On shutdown, stop scheduler and close browser.
- [x] Write tests: test scheduler `run_search` with mocked scraper, test API endpoint returns correct response, test scheduler start/stop lifecycle
- [x] Run project test suite - must pass before task 6

### Task 6: Verify acceptance criteria

- [x] Manual test: start the API server, call `POST /api/jobs/linkedin-search` with search params, verify jobs appear in `GET /api/hitl/pending` after processing
- [x] Manual test: verify cookie persistence — authenticate once, restart server, confirm session reuse works without re-login
- [x] Manual test: verify anti-detection — run with `browser_headless=false`, observe human-like delays, scrolling, and typing behavior
- [x] Run full test suite: `pytest`
- [x] Run linter: `ruff check src/`
- [x] Verify test coverage meets 80%+ for new code: `pytest --cov=src/services/linkedin_scraper --cov=src/services/linkedin_search --cov=src/services/job_queue --cov=src/services/scheduler`

### Task 7: Update documentation

- [ ] Update CLAUDE.md: mark LinkedIn Job Source Adapter as complete, update implementation status table, add `linkedin_scraper.py`, `linkedin_search.py`, `job_queue.py`, `scheduler.py` to directory structure
- [ ] Update `.env.example` (or create if missing) with new LinkedIn search settings
- [ ] Move this plan to `docs/plans/completed/`
