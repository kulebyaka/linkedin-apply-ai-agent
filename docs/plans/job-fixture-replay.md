# Feature Specification: Job Fixture Record & Replay

## Overview
- **Feature**: Job Fixture Record & Replay
- **Status**: Draft
- **Created**: 2026-03-13
- **Author**: User + Claude Code

## Problem Statement

Testing the HITL review flow end-to-end requires scraped LinkedIn jobs in the queue. Currently, every dev session or test run must perform a live LinkedIn scrape — requiring browser auth, network access, cookie sessions, and waiting for scraping to complete. This creates a slow, flaky, and annoying inner loop for HITL development, testing, and demos.

## Goals & Success Criteria

- **G1**: Eliminate LinkedIn scraping dependency for HITL testing and demos
- **G2**: Auto-record scraped jobs so fixture data stays fresh without manual effort
- **G3**: Replay recorded jobs into the queue via a feature flag, bypassing the scheduler/browser entirely
- **G4**: Provide an API endpoint for on-demand replay during a running session
- **G5**: Integrate with E2E tests so they can use realistic scraped data
- **Success Metrics**: App startup with `SEED_JOBS_FROM_FILE=true` populates the HITL pending queue in <5 seconds with no browser/network activity

## User Stories

1. As a **developer**, I want to start the app and immediately have pending HITL jobs from a fixture file, so I can test the review UI without waiting for a scrape
2. As a **developer**, I want scraped jobs to be auto-saved every time I do a real scrape, so my fixture file stays up-to-date without extra steps
3. As a **tester**, I want to replay fixtures via an API call during a session, so I can re-seed the queue without restarting the server
4. As a **demo presenter**, I want to seed realistic job data on demand, so I can showcase the HITL UI without LinkedIn credentials

## Functional Requirements

### Core Capabilities

#### Recording (Auto-save on scrape)
- After every successful `scrape_and_enrich()` call, save the raw `ScrapedJob` list to `data/jobs/scraped_jobs.json`
- Format: JSON array of `ScrapedJob.model_dump()` dicts
- Overwrites the file on each scrape (latest snapshot wins)
- Recording happens regardless of whether `SEED_JOBS_FROM_FILE` is set — always keep fixture data fresh

#### Replay (Load from file)
- When `SEED_JOBS_FROM_FILE=true`:
  - On startup, load `ScrapedJob` objects from `SCRAPED_JOBS_PATH` (default: `data/jobs/scraped_jobs.json`)
  - Enqueue them into the `JobQueue` for processing by the queue consumer
  - Respect `SEED_JOBS_LIMIT` to cap the number of jobs enqueued (default: no limit)
  - Skip jobs whose `job_id` already exists in the repository (deduplication)
  - Completely disable LinkedIn browser, scraper, and scheduler initialization
- API endpoint `POST /api/jobs/replay-fixtures` available for on-demand replay during a session:
  - Accepts optional `?limit=N` query parameter
  - Returns count of jobs enqueued and skipped
  - Works regardless of `SEED_JOBS_FROM_FILE` flag (always available)

#### Feature Flag Behavior
| Flag | Startup Behavior | Scheduler | Browser | Manual Scrape Endpoint |
|------|-----------------|-----------|---------|----------------------|
| `SEED_JOBS_FROM_FILE=false` (default) | Normal startup | Starts if enabled | Initialized | Works normally |
| `SEED_JOBS_FROM_FILE=true` | Load from file → enqueue | Not started | Not initialized | Returns 409 "disabled" |

### User Flows

**Flow 1: Record (automatic)**
1. Developer runs a real LinkedIn scrape (manual trigger or scheduler)
2. `scheduler.run_search()` calls `scraper.scrape_and_enrich()`
3. After scraping completes, `job_fixtures.save_scraped_jobs(jobs)` writes to `data/jobs/scraped_jobs.json`
4. Jobs proceed to queue as normal

**Flow 2: Replay on startup**
1. Developer sets `SEED_JOBS_FROM_FILE=true` in `.env`
2. App starts → skips browser/scraper/scheduler init
3. `job_fixtures.load_scraped_jobs()` reads from file
4. Jobs are filtered by `SEED_JOBS_LIMIT` and deduplication
5. Jobs enqueued → queue consumer processes them through preparation workflow → land in HITL pending

**Flow 3: Replay via API**
1. Developer calls `POST /api/jobs/replay-fixtures?limit=5`
2. Server loads from file, applies limit and dedup
3. Returns `{"enqueued": 5, "skipped": 2, "total_in_file": 12}`
4. Queue consumer picks up new jobs

### Data Model

No new Pydantic models needed. Uses existing `ScrapedJob` from `src/services/linkedin_scraper.py`.

**File format** (`data/jobs/scraped_jobs.json`):
```json
[
  {
    "job_id": "3847291056",
    "title": "Senior Python Developer",
    "company": "TechCorp",
    "location": "Remote",
    "url": "https://www.linkedin.com/jobs/view/3847291056",
    "description": "...",
    "easy_apply": true,
    "posted_date": "2 days ago",
    "salary": "$150k-$180k",
    "experience_level": "Mid-Senior level",
    "job_type": "Full-time"
  },
  ...
]
```

### Integration Points

| Component | Integration | Direction |
|-----------|------------|-----------|
| `LinkedInSearchScheduler.run_search()` | Call `save_scraped_jobs()` after `scrape_and_enrich()` | Record |
| `src/api/main.py` startup | Call `load_and_enqueue()` when flag is set | Replay |
| `src/api/main.py` endpoint | New `POST /api/jobs/replay-fixtures` | Replay |
| `src/config/settings.py` | New settings: `SEED_JOBS_FROM_FILE`, `SCRAPED_JOBS_PATH`, `SEED_JOBS_LIMIT` | Config |
| `tests/e2e/conftest.py` | New `seed_from_fixtures` fixture using replay endpoint | Tests |

## Technical Design

### Architecture

New service module `src/services/job_fixtures.py` with pure functions:

```python
# src/services/job_fixtures.py

def save_scraped_jobs(jobs: list[ScrapedJob], path: Path) -> int:
    """Serialize ScrapedJob list to JSON file. Returns count saved."""

def load_scraped_jobs(path: Path, limit: int | None = None) -> list[ScrapedJob]:
    """Load ScrapedJob list from JSON file. Applies optional limit."""

async def enqueue_from_fixtures(
    path: Path,
    queue: JobQueue,
    repository: JobRepository | None = None,
    limit: int | None = None,
) -> dict:
    """Load jobs from file, dedup against repository, enqueue.
    Returns {"enqueued": int, "skipped": int, "total_in_file": int}."""
```

### Technology Stack
- **Libraries**: Standard library `json`, `pathlib` — no new dependencies
- **Serialization**: Pydantic's `model_dump()` / `model_validate()` for ScrapedJob

### Data Persistence
- Single JSON file at configurable path (default `data/jobs/scraped_jobs.json`)
- Overwritten on each scrape
- Added to `.gitignore` — real scraped data stays local
- A small sample fixture file `tests/fixtures/sample_scraped_jobs.json` checked into git for CI

### API / Interface Design

**New endpoint:**
```
POST /api/jobs/replay-fixtures?limit=5
```
Response:
```json
{
  "status": "ok",
  "enqueued": 5,
  "skipped": 2,
  "total_in_file": 12,
  "source": "data/jobs/scraped_jobs.json"
}
```

Error responses:
- `404` — fixture file not found
- `409` — no jobs to enqueue (all duplicates or empty file)

**New settings in `src/config/settings.py`:**
```python
seed_jobs_from_file: bool = False          # SEED_JOBS_FROM_FILE
scraped_jobs_path: str = "./data/jobs/scraped_jobs.json"  # SCRAPED_JOBS_PATH
seed_jobs_limit: int = 0                   # SEED_JOBS_LIMIT (0 = no limit)
```

## Non-Functional Requirements

- **Performance**: File load + enqueue should complete in <2 seconds for 100 jobs
- **Security**: Fixture file in `.gitignore` to prevent accidental commit of scraped data
- **Observability**: Log clearly on startup: "Seeding N jobs from fixtures (M skipped as duplicates)" and "LinkedIn scraping disabled — using fixture replay mode"
- **Error Handling**:
  - Missing fixture file on startup → log warning, continue without seeding (don't crash)
  - Malformed JSON → log error with details, skip invalid entries, continue with valid ones
  - Empty file → log info, no-op

## Implementation Considerations

### Design Trade-offs

| Decision | Chosen | Alternative | Rationale |
|----------|--------|-------------|-----------|
| Capture point | Raw `ScrapedJob` | Normalized `JobPosting` | Exercises full pipeline on replay (adapter → queue → workflow) |
| Record trigger | Auto on every scrape | Explicit opt-in | Zero-effort fixture freshness; file is cheap to overwrite |
| Replay trigger | Startup + API | Startup only | API endpoint enables re-seeding without restart |
| Scheduler on replay | Completely disabled | Available but not auto-started | Faster startup, no auth/cookie issues, clear separation |
| File strategy | Single file, overwrite | Timestamped snapshots | Simplicity; one fixture file is sufficient for testing |

### Dependencies
- Existing `ScrapedJob` model must support `model_dump()` / `model_validate()` (it does — Pydantic v2)
- `data/jobs/` directory must exist (already created by project setup)

### Testing Strategy
- **Unit tests** for `job_fixtures.py`: save/load round-trip, limit, dedup, malformed JSON handling
- **Integration test**: startup with `SEED_JOBS_FROM_FILE=true` populates pending queue
- **E2E test fixture**: `seed_from_fixtures` in conftest.py calls replay endpoint, verifying jobs appear in HITL UI
- **Sample fixture**: `tests/fixtures/sample_scraped_jobs.json` with 3-5 realistic jobs for CI

## Out of Scope

- Multiple fixture files / snapshot management
- UI for managing fixtures
- Recording from non-LinkedIn sources (manual/URL jobs)
- Fixture data anonymization or sanitization
- Replaying into application workflow (only preparation workflow)

## Open Questions

None — all requirements gathered.

## References

- `src/services/linkedin_scraper.py` — `ScrapedJob` model and `scrape_and_enrich()`
- `src/services/scheduler.py` — `LinkedInSearchScheduler.run_search()` (record hook point)
- `src/services/job_queue.py` — `JobQueue` and `process_queue()` consumer
- `src/api/main.py` — startup flow and endpoint definitions
- `tests/e2e/conftest.py` — existing E2E test fixtures
