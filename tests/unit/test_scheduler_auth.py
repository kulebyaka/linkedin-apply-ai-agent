"""Tests for LinkedIn auth-expired resilience in the search scheduler.

Covers transitions into ``paused_auth_required`` when the browser raises
``LinkedInAuthExpiredError`` and that subsequent ticks skip cleanly until
``clear_auth_error()`` is called.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.job import ScrapedJob
from src.models.user import User, UserSearchPreferences
from src.services.jobs.job_queue import JobQueue
from src.services.jobs.scheduler import LinkedInSearchScheduler
from src.services.linkedin.browser_automation import LinkedInAuthExpiredError

pytestmark = pytest.mark.asyncio


def _job(job_id: str) -> ScrapedJob:
    return ScrapedJob(job_id=job_id, title="", company="", location="", url="")


def _user(user_id: str, prefs: UserSearchPreferences | None = None) -> User:
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        display_name=user_id,
        search_preferences=prefs or UserSearchPreferences(keywords="python"),
    )


def _make_scheduler(
    *,
    users: list[User],
    auth_raises: Exception | None = None,
    scrape_raises: Exception | None = None,
    scrape_result: list[ScrapedJob] | None = None,
) -> tuple[LinkedInSearchScheduler, MagicMock]:
    settings = MagicMock()
    settings.linkedin_search_interval_hours = 1
    settings.scraped_jobs_path = None

    scraper = MagicMock()
    scraper.reset_seen = MagicMock()
    scraper.browser = MagicMock()
    if auth_raises is not None:
        scraper.browser.ensure_authenticated = AsyncMock(side_effect=auth_raises)
    else:
        scraper.browser.ensure_authenticated = AsyncMock()

    if scrape_raises is not None:
        scraper.scrape_and_enrich = AsyncMock(side_effect=scrape_raises)
    else:
        scraper.scrape_and_enrich = AsyncMock(return_value=scrape_result or [])

    user_repo = MagicMock()
    user_repo.get_all_with_search_prefs = AsyncMock(return_value=users)

    queue = JobQueue(max_size=100)
    scheduler = LinkedInSearchScheduler(
        settings, scraper, queue, user_repository=user_repo
    )
    return scheduler, scraper


async def test_auth_expired_during_authentication_pauses_scheduler():
    scheduler, scraper = _make_scheduler(
        users=[_user("alice")],
        auth_raises=LinkedInAuthExpiredError("cookies expired"),
    )

    assert scheduler.state == "active"

    count = await scheduler.run_search()

    assert count == 0
    assert scheduler.state == "paused_auth_required"
    assert scheduler.last_auth_error_at is not None
    assert scheduler.last_auth_error_message == "cookies expired"

    # Per-user record should reflect auth_expired (not generic auth_failed).
    run = scheduler.get_last_run_for_user("alice")
    assert run is not None
    assert run.reason == "auth_expired"
    assert run.message == "cookies expired"


async def test_next_tick_after_pause_skips_without_invoking_scraper():
    scheduler, scraper = _make_scheduler(
        users=[_user("alice")],
        auth_raises=LinkedInAuthExpiredError("cookies expired"),
    )

    await scheduler.run_search()
    assert scheduler.state == "paused_auth_required"

    scraper.browser.ensure_authenticated.reset_mock()
    scraper.scrape_and_enrich.reset_mock()

    count = await scheduler.run_search(user_id="alice")

    assert count == 0
    scraper.browser.ensure_authenticated.assert_not_awaited()
    scraper.scrape_and_enrich.assert_not_awaited()

    run = scheduler.get_last_run_for_user("alice")
    assert run is not None
    assert run.reason == "paused"


async def test_clear_auth_error_resumes_scheduler():
    scheduler, scraper = _make_scheduler(
        users=[_user("alice")],
        auth_raises=LinkedInAuthExpiredError("cookies expired"),
    )

    await scheduler.run_search()
    assert scheduler.state == "paused_auth_required"

    scheduler.clear_auth_error()

    assert scheduler.state == "active"
    assert scheduler.last_auth_error_at is None
    assert scheduler.last_auth_error_message is None

    # Replace the auth mock so the next call succeeds.
    scraper.browser.ensure_authenticated = AsyncMock()
    scraper.scrape_and_enrich = AsyncMock(return_value=[_job("j1")])

    count = await scheduler.run_search()
    assert count == 1
    scraper.browser.ensure_authenticated.assert_awaited_once()


async def test_auth_expired_during_scrape_pauses_scheduler():
    """If scraping (not authentication) raises LinkedInAuthExpiredError,
    the scheduler still transitions to paused_auth_required."""
    scheduler, scraper = _make_scheduler(
        users=[_user("alice"), _user("bob")],
        scrape_raises=LinkedInAuthExpiredError("redirected to /login mid-scrape"),
    )

    count = await scheduler.run_search()

    assert count == 0
    assert scheduler.state == "paused_auth_required"

    # We break out of the per-user loop once auth dies, so bob shouldn't
    # have its own entry recorded.
    assert scheduler.get_last_run_for_user("alice").reason == "auth_expired"
    assert scheduler.get_last_run_for_user("bob") is None


async def test_generic_auth_failure_does_not_pause():
    """A non-LinkedInAuthExpiredError from ensure_authenticated should not
    flip the scheduler into the paused state — operators should keep retrying
    rather than need to take action."""
    scheduler, _ = _make_scheduler(
        users=[_user("alice")],
        auth_raises=RuntimeError("network blip"),
    )

    count = await scheduler.run_search()

    assert count == 0
    assert scheduler.state == "active"
    assert scheduler.last_auth_error_at is None

    run = scheduler.get_last_run_for_user("alice")
    assert run is not None
    assert run.reason == "auth_failed"
