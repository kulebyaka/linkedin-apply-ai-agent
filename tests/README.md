# Tests

Layout, fixtures, and conventions. For how to *run* each tier, the markers, and the gotchas, see
`docs/testing_guide.md`.

## Layout

```
tests/
├── conftest.py                  # Shared fixtures (CV/job data, llm_client, cv_composer)
├── fixtures/
│   ├── master_cv.json           # Sample master CV (experienced)
│   ├── master_cv_minimal.json   # Sample master CV (junior)
│   ├── job_posting.json         # Sample job posting
│   └── sample_scraped_jobs.json # Recorded scraper output for replay tests
│
├── unit/                        # ~820 tests. Fast, fully mocked. `pytest -m "not e2e"`.
│   ├── _route_helpers.py        # Shared API-test plumbing (app + auth wiring)
│   ├── test_state_machine.py    # BusinessState / WorkflowStep / ALLOWED_TRANSITIONS
│   ├── test_inmemory_repository.py, test_sqlite_repository.py, test_filter_repository.py,
│   │   test_job_repository_admin.py, test_user_repository.py, test_user_role_migration.py
│   ├── test_preparation_workflow_filter.py, test_dispatcher.py, test_persist_at_discovery.py
│   ├── test_job_orchestrator.py, test_hitl_processor.py, test_job_queue.py, test_scheduler.py
│   ├── test_job_filter.py, test_job_filter_models.py, test_auto_refinement.py
│   ├── test_cv_composer.py, test_cv_prompts.py, test_cv_attempts.py, test_pdf_generator.py
│   ├── test_pdf_extraction.py, test_pdf_extraction_api.py
│   ├── test_instructor_client.py, test_create_llm_client.py, test_model_catalog.py,
│   │   test_pricing_source.py, test_llm_models_api.py
│   ├── test_auth.py, test_admin_authz.py, test_admin_endpoints.py, test_admin_alerts.py,
│   │   test_promote_user_cli.py
│   ├── test_api_filter_preferences.py, test_api_notifications_refinement.py,
│   │   test_jobs_list_api.py, test_legacy_endpoints_removed.py
│   ├── test_linkedin_scraper.py, test_linkedin_search.py, test_browser_automation.py,
│   │   test_job_fixtures.py
│   └── test_context.py, test_models.py, test_user_models.py, test_notimplemented_propagation.py
│
├── e2e/                         # 20 tests. Playwright; auto-starts API + Vite. Marker: e2e
│   │                            # Included in a bare `pytest` run — needs a Chromium binary
│   ├── conftest.py              # Session fixtures: free ports, subprocess servers, browser
│   ├── _test_api_server.py      # API entrypoint with the LLM stubbed out
│   ├── test_hitl_review.py      # Review queue flow
│   ├── test_admin_ui.py         # Admin dashboard
│   └── test_llm_dropdowns.py    # Provider/model selector
│
├── eval/                        # Real-LLM quality evals. Needs a manual `deepeval` install
│   ├── conftest.py              # Skips this whole dir when deepeval is absent; otherwise
│   │                            # auto-marks everything here eval + llm + slow
│   ├── test_cv_faithfulness.py  # Hallucination detection — the critical one
│   ├── test_cv_relevancy.py, test_cv_contextual.py, test_cv_bias_toxicity.py
│   ├── metrics/                 # Custom DeepEval metrics (hallucination guard, schema compliance)
│   └── fixtures/eval_scenarios.json
│
└── helpers/
    └── llm_clients.py           # Real LLM client factory (eval tier only)
```

## Shared fixtures (`tests/conftest.py`)

| Fixture | Provides |
|---|---|
| `fixtures_dir` | Session-scoped path to `tests/fixtures/` |
| `sample_master_cv` | Parsed `master_cv.json` |
| `sample_job_posting` | Parsed `job_posting.json` |
| `sample_job_summary` | Pre-computed job analysis |
| `mock_llm_response_job_summary` / `_summary` / `_experiences` | Canned LLM payloads |
| `llm_client` | **Marker-aware**: `MockLLMClient` normally, a real client when the test carries the `eval` marker |
| `cv_composer` | `CVComposer` wired to whichever `llm_client` the above resolved |

`llm_client` switching on the `eval` marker is the one piece of fixture magic here — a test that
accidentally carries the `eval` marker will start making real API calls.

## MockLLMClient

Defined in `tests/unit/test_cv_composer.py` (and imported by `conftest.py` from there — it is not in
a helpers module). It implements `BaseLLMClient` and dispatches on prompt content:

- `set_response(keyword, payload)` registers a payload for any prompt containing `keyword`.
- Matching is done across the **combined system + user** text of the `PromptSpec`, so a keyword may
  live in either half.
- When a call passes `response_model=`, the configured dict is validated into that Pydantic model and
  returned as an instance — mirroring the real Instructor client, so tests exercise the same code
  path as production. A raw `schema=` call returns a plain dict.
- `call_count` is available for asserting how many LLM round-trips a composition made.

## Conventions

- **`asyncio_mode = auto`** — no `@pytest.mark.asyncio` needed.
- **Markers are strict.** Declared set: `unit`, `integration`, `eval`, `e2e`, `llm`, `slow`,
  `expensive`. A misspelled marker fails the run. `unit` is declared but never applied — `-m unit`
  selects nothing; use the path instead.
- **Mock at the seam.** Inject a fake repository or LLM client; don't patch service internals.
- **Both repository backends get the same behavioural coverage** (`test_inmemory_repository.py` /
  `test_sqlite_repository.py`) so dev and production can't drift.
- **API tests go through `_route_helpers.py`** for app construction and auth wiring rather than
  hand-rolling a client per file.
- **Deleted endpoints stay deleted**: `test_legacy_endpoints_removed.py` fails if a legacy path is
  reintroduced.
- Name tests after the behaviour, and keep to arrange / act / assert.
