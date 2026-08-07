# Testing Guide

How to run and extend the test suite. Test-tree layout, fixtures, and conventions are in
`tests/README.md`; this document covers the strategy, the tiers, and the things that trip people up.

## Tiers

| Tier | Location | Speed | Needs | Marker |
|---|---|---|---|---|
| **Unit** | `tests/unit/` | seconds | nothing — LLMs, browser, and network are mocked | `unit` (implicit; the default run) |
| **E2E** | `tests/e2e/` | minutes | Playwright + Node; auto-starts its own API and Vite servers | `e2e` (required) |
| **Eval** | `tests/eval/` | slow + costs money | real LLM API keys **and a manual `deepeval` install** | `eval`, `llm`, `slow` (auto-applied) |

Unit tests are the working tier — ~820 of them, covering repositories, workflows, the dispatcher,
auth, admin authorization, migrations, the filter, the scheduler, the model catalog, and every API
route group. Everything in CI-relevant scope lives here.

## Running

```bash
uv sync                                  # install deps (uv, not pip — there is no requirements*.txt)

uv run pytest -m "not e2e"               # the everyday loop: unit tier, ~9s
uv run pytest tests/unit/test_job_filter.py -v
uv run pytest tests/unit/test_job_filter.py::TestEvaluateJob::test_hard_disqualifier
uv run pytest -k "refinement or notification"
```

A bare `uv run pytest` collects **unit + E2E** (eval is skipped automatically when `deepeval` isn't
installed). That means it needs the E2E prerequisites below — without a Chromium binary you get 20
errors, not 20 failures. Use `-m "not e2e"` for the fast loop.

Markers are declared with `--strict-markers` in `pytest.ini`, so a typo fails the run rather than
silently matching nothing. Available: `unit`, `integration`, `eval`, `e2e`, `llm`, `slow`,
`expensive`. Note that `unit` is a *declared* marker but is not actually applied to the unit tests —
select that tier by path (`tests/unit`) or by excluding others, not with `-m unit`.

### E2E

```bash
uv run playwright install chromium
cd ui && npm install && cd ..

uv run pytest -m e2e                                   # all 20 E2E tests
uv run pytest tests/e2e/test_hitl_review.py -v -m e2e  # one file
```

E2E tests *are* part of a bare `pytest` run, so the Chromium binary and `ui/node_modules` are
effectively prerequisites for it. Session-scoped
fixtures in `tests/e2e/conftest.py` start a FastAPI backend (with the LLM stubbed by
`_test_api_server.py`) and a Vite dev server as subprocesses on free ports, wait for both to become
ready, drive a real Chromium, and kill everything on teardown. `REPO_TYPE` defaults to `memory`, so
E2E runs never touch your dev database.

Current E2E coverage: HITL review flow, admin UI, and the LLM provider/model dropdowns.

### Eval

```bash
uv pip install deepeval          # NOT a declared dependency — install it explicitly
uv run pytest -m eval
```

Eval tests call real LLMs against real API keys and cost money. `deepeval` is deliberately **not** a
declared dependency, and `tests/eval/conftest.py` skips the whole directory when it isn't importable
— so a normal run never touches this tier, and installing `deepeval` silently opts you back in.
Deselect with `-m "not eval"` once it's installed. Everything under `tests/eval/` is auto-marked
`eval` + `llm` + `slow`. The evaluator model comes from `DEEPEVAL_EVALUATOR_MODEL`.

The critical eval is **faithfulness** (`test_cv_faithfulness.py`): does the tailored CV invent
employers, institutions, or achievements the master CV doesn't contain? Custom metrics live in
`tests/eval/metrics/`.

## Mocking Strategy

Unit tests never call a real LLM. `tests/conftest.py` exposes an `llm_client` fixture backed by a
mock whose responses are keyed by a substring of the prompt:

```python
def test_summarize_job(cv_composer, llm_client, sample_job_posting):
    llm_client.set_response("job description", {"technical_skills": ["Python", "Django"], ...})
    result = cv_composer._summarize_job(sample_job_posting)
    assert "Python" in result["technical_skills"]
```

If a mock appears not to fire, the usual cause is that the keyword doesn't actually appear in the
rendered prompt, or the mock was configured after the call.

Beyond mocks, `src/services/jobs/job_fixtures.py` can **record and replay** real scraped jobs and
real LLM responses, which is how the pipeline is exercised deterministically end to end without
LinkedIn or an API key. `SEED_JOBS_FROM_FILE=true` replays them through the live app (and disables
scraping while it does).

## Gotchas

- **`DYLD_LIBRARY_PATH` cuts both ways on macOS.** WeasyPrint needs `/opt/homebrew/lib` on it to find
  gobject; Chromium *crashes* when it is set. Production code strips `DYLD_*` from the browser
  subprocess env (`browser_automation.py`), and the E2E conftest sets it for the API subprocess. If
  PDF generation fails with a library error, export it; if the browser dies instantly, unset it.
- **There is no CI test job.** `.github/workflows/release.yml` only builds images and deploys. Run
  the suite locally before tagging a release.
- **`asyncio_mode = auto`** — async tests need no `@pytest.mark.asyncio`.
- **State-machine tests are load-bearing.** `ALLOWED_TRANSITIONS` is enforced at the repository
  layer, so adding a `BusinessState` without updating the transition map will fail
  `tests/unit/test_state_machine.py` and the repository tests — that's intentional.
- **Both repositories must pass the same behavioural tests.** `test_inmemory_repository.py` and
  `test_sqlite_repository.py` exist so the dev and production backends can't diverge.
- **`tests/unit/test_legacy_endpoints_removed.py`** asserts that deleted endpoints stay deleted. If
  it fails after you add a route, you have resurrected a legacy path.

## Coverage

```bash
uv run pytest --cov=src --cov-report=term-missing
uv run pytest --cov=src --cov-report=html && open htmlcov/index.html
```

Priority areas to keep high: `src/services/cv/`, `src/services/jobs/`, `src/services/db/`,
`src/models/state_machine.py`, and `src/api/routes/`.

## Manual API Smoke Test

For the on-demand (MVP) CV generation path, without the UI:

```bash
uv run uvicorn src.api.main:app --reload
```

```bash
# Auth: with DEV_AUTH_BYPASS=true, mint a session cookie
curl -c cookies.txt -X POST http://localhost:8000/api/auth/dev-login

JOB=$(curl -b cookies.txt -s -X POST http://localhost:8000/api/jobs/submit \
  -H 'Content-Type: application/json' \
  -d '{"source":"manual","mode":"mvp","job_description":{
        "title":"Senior Python Engineer","company":"TechCorp",
        "description":"We need a Python expert with FastAPI and AWS experience.",
        "requirements":"Python, FastAPI, Docker, AWS"}}' | python -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')

# Poll until completed (a full composition takes a few minutes)
curl -b cookies.txt -s "http://localhost:8000/api/jobs/$JOB/status"

curl -b cookies.txt -o tailored_cv.pdf "http://localhost:8000/api/jobs/$JOB/pdf"
```

This requires a master CV on the user record (or at `MASTER_CV_PATH`) and a working LLM key.

## Adding Tests

1. Put it in `tests/unit/` unless it genuinely needs a browser or a real LLM.
2. Reuse the fixtures in `tests/conftest.py` (`sample_master_cv`, `sample_job_posting`,
   `llm_client`, `cv_composer`, …) rather than rebuilding test data.
3. Mock at the seam, not inside the unit: inject a fake repository or LLM client, don't patch
   internals.
4. For a new state or transition, extend `test_state_machine.py` *and* both repository test files.
5. Name the test after the behaviour (`test_compose_experiences_reorders_by_relevance`), and keep to
   arrange / act / assert.
