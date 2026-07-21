# Fix LinkedIn Scraper Easy Apply Badge Detection

## Overview

The scraper's Easy Apply badge selector is stale against current LinkedIn DOM, so **every**
scraped job is flagged `easy_apply=False` (0 of 51 in the local DB). This causes the apply
trigger to park real Easy Apply jobs in `manual_required` instead of dispatching. Replace the
selector with one matching the current search-card DOM and cover it with a fixture-based test.

## Context

- Files involved:
  - `src/services/linkedin/selectors.py` (existing — holds `SELECTORS["job_card_easy_apply"]`,
    currently `"li-icon[type='linkedin-bug'], span.job-card-container__apply-method"` — both
    obsolete; verified 0 matches on a live search page)
  - `src/services/linkedin/linkedin_scraper.py` (existing — `_parse_job_card` at ~line 281 does
    `easy_apply = await card_element.locator(SELECTORS["job_card_easy_apply"]).count() > 0`)
  - `tests/unit/test_linkedin_scraper.py` (existing — scraper unit tests to extend)
  - `tests/fixtures/easy_apply_card.html` (to be created — captured search-card HTML)
- Current live DOM (captured 2026-07-04 from `linkedin.com/jobs/search/?f_AL=true`):
  ```html
  <ul class="job-card-list__footer-wrapper job-card-container__footer-wrapper">
    <li class="<obfuscated-hash> job-card-container__footer-item ...">
      <svg data-test-icon="linkedin-bug-color-small">…</svg> Easy Apply
    </li>
  </ul>
  ```
  Stable anchors: `svg[data-test-icon="linkedin-bug-color-small"]` (locale-independent) and the
  `job-card-container__footer-item` class. The obfuscated hash class is NOT stable.
- Related patterns: `SELECTORS` dict + Playwright `locator(...).count()` (async API). Playwright
  locators support the `:has-text()` pseudo (non-standard CSS) — usable here.
- Dependencies: Playwright (already installed); no new deps.

## Development Approach

- **Testing approach**: Regular (fix selector, then add fixture-based test).
- Use the Playwright **async** API consistently (matches the scraper).
- Prefer the locale-independent SVG-icon anchor as primary; keep a text/`has-text` fallback.
- **CRITICAL: every task MUST include new/updated tests.**
- **CRITICAL: all tests must pass before starting the next task.**

## Implementation Steps

### Task 1: Update the Easy Apply badge selector

**Files:**
- Modify: `src/services/linkedin/selectors.py`

- [ ] (If validating against live LinkedIn) capture a real search-card's Easy Apply footer
      `<li>` outerHTML and a non-Easy-Apply card's footer into `tests/fixtures/easy_apply_card.html`
      and `tests/fixtures/non_easy_apply_card.html` (used by Task 2). The captured DOM above is
      the reference.
- [ ] Replace `SELECTORS["job_card_easy_apply"]` with:
      `"li.job-card-container__footer-item svg[data-test-icon='linkedin-bug-color-small'], li.job-card-container__footer-item:has-text('Easy Apply')"`
- [ ] Update the inline comment to note the SVG-icon anchor is primary (locale-independent) and
      the `:has-text('Easy Apply')` clause is a fallback; note the old `li-icon[type='linkedin-bug']`
      DOM is gone.
- [ ] Run project test suite (`uv run pytest`) - must pass before task 2

### Task 2: Fixture-based test for card badge detection

**Files:**
- Modify: `tests/unit/test_linkedin_scraper.py`
- Create: `tests/fixtures/easy_apply_card.html`, `tests/fixtures/non_easy_apply_card.html`

- [ ] Add an async test that loads the captured Easy-Apply card HTML via Playwright
      `page.set_content(...)` (or the existing test's page-mock pattern, whichever the file uses)
      and asserts `_parse_job_card` returns `easy_apply is True`.
- [ ] Add the negative case: a card without the badge → `easy_apply is False`.
- [ ] If `_parse_job_card` isn't directly unit-testable with static HTML in the current harness,
      add a focused test that runs `card_element.locator(SELECTORS["job_card_easy_apply"]).count()`
      against the fixture to assert the selector matches (positive) / doesn't (negative).
- [ ] Run project test suite (`uv run pytest`) - must pass before task 3

### Task 3: Verify acceptance criteria

- [ ] Manual test: with a logged-in LinkedIn session, run a scrape (or the live probe
      `scripts/linkedin_scraper_probe.py`) and confirm Easy Apply jobs now come back with
      `easy_apply=True` (spot-check ≥3 cards known to show the badge).
- [ ] Confirm downstream: an approved Easy-Apply job now passes `_is_linkedin_easy_apply` and is
      dispatched (not parked `manual_required`). (Note: this may still hit the separate SDUI
      rework — see `docs/plans/sdui-easy-apply-rework.md`.)
- [ ] Note the data caveat: the 51 already-scraped rows keep `easy_apply=False` until re-scraped;
      the hourly scheduler re-flags them on the next search. No migration/backfill needed.
- [ ] Run full test suite: `uv run pytest`
- [ ] Run linter: `uv run ruff check src/ && uv run black --check src/`

### Task 4: Update documentation

- [ ] Update `CLAUDE.md` only if the selector convention is documented there (note the SVG-icon
      anchor pattern for LinkedIn's obfuscated DOM).
- [ ] Move this plan to `docs/plans/completed/`.
