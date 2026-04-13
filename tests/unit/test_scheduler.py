"""Tests for LinkedIn search scheduler and API endpoints."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.job import ScrapedJob
from src.models.user import User, UserSearchPreferences
from src.services.jobs.job_queue import JobQueue
from src.services.jobs.scheduler import LinkedInSearchScheduler

pytestmark = pytest.mark.asyncio


def _job(job_id: str) -> ScrapedJob:
    return ScrapedJob(job_id=job_id, title="", company="", location="", url="")


def _user(
    user_id: str,
    email: str = "test@example.com",
    prefs: UserSearchPreferences | None = None,
) -> User:
    """Create a User with optional search preferences."""
    return User(
        id=user_id,
        email=email,
        display_name=email.split("@")[0],
        search_preferences=prefs,
    )


# ---------------------------------------------------------------------------
# Helper: build a scheduler with mocked dependencies
# ---------------------------------------------------------------------------


def _default_user_repo() -> MagicMock:
    """Return a mock user_repository with a single user who has search preferences."""
    default_user = _user(
        "default-user",
        email="default@test.com",
        prefs=UserSearchPreferences(keywords="python", location="San Francisco"),
    )
    repo = MagicMock()
    repo.get_all_with_search_prefs = AsyncMock(return_value=[default_user])
    return repo


def _make_scheduler(
    *,
    scrape_result: list[ScrapedJob] | None = None,
    scrape_error: Exception | None = None,
    interval_hours: int = 1,
    user_repository: MagicMock | None | str = "default",
) -> tuple[LinkedInSearchScheduler, MagicMock, MagicMock, JobQueue]:
    """Return (scheduler, mock_settings, mock_scraper, queue).

    Pass ``user_repository=None`` to explicitly omit the user repo.
    Default provides a single user with search prefs matching the settings.
    """
    settings = MagicMock()
    settings.linkedin_search_keywords = "python"
    settings.linkedin_search_location = "San Francisco"
    settings.linkedin_search_remote_filter = None
    settings.linkedin_search_date_posted = None
    settings.linkedin_search_experience_level = None
    settings.linkedin_search_job_type = None
    settings.linkedin_search_easy_apply_only = False
    settings.linkedin_search_max_jobs = 50
    settings.linkedin_search_interval_hours = interval_hours

    scraper = MagicMock()
    scraper.browser = MagicMock()
    scraper.browser.ensure_authenticated = AsyncMock()

    if scrape_error:
        scraper.scrape_and_enrich = AsyncMock(side_effect=scrape_error)
    else:
        scraper.scrape_and_enrich = AsyncMock(return_value=scrape_result or [])

    if user_repository == "default":
        user_repository = _default_user_repo()

    queue = JobQueue(max_size=100)
    scheduler = LinkedInSearchScheduler(
        settings, scraper, queue, user_repository=user_repository
    )

    return scheduler, settings, scraper, queue


# ---------------------------------------------------------------------------
# LinkedInSearchScheduler.run_search
# ---------------------------------------------------------------------------


class TestRunSearch:
    async def test_run_search_enqueues_jobs(self):
        jobs = [_job("1"), _job("2"), _job("3")]
        scheduler, _, scraper, queue = _make_scheduler(scrape_result=jobs)

        count = await scheduler.run_search()

        assert count == 3
        assert queue.size() == 3
        scraper.browser.ensure_authenticated.assert_awaited_once()
        scraper.scrape_and_enrich.assert_awaited_once()

    async def test_run_search_returns_zero_on_empty(self):
        scheduler, _, _, queue = _make_scheduler(scrape_result=[])

        count = await scheduler.run_search()

        assert count == 0
        assert queue.is_empty()

    async def test_run_search_handles_exception(self):
        scheduler, _, _, queue = _make_scheduler(
            scrape_error=RuntimeError("Network error")
        )

        count = await scheduler.run_search()

        assert count == 0
        assert queue.is_empty()
        assert scheduler.last_run_time is not None

    async def test_run_search_updates_last_run_metadata(self):
        jobs = [_job("x")]
        scheduler, _, _, _ = _make_scheduler(scrape_result=jobs)

        assert scheduler.last_run_time is None
        assert scheduler.last_run_jobs == 0

        await scheduler.run_search()

        assert scheduler.last_run_time is not None
        assert scheduler.last_run_jobs == 1

    async def test_run_search_builds_params_from_user_prefs(self):
        scheduler, settings, scraper, _ = _make_scheduler(scrape_result=[])

        await scheduler.run_search()

        call_args = scraper.scrape_and_enrich.call_args[0][0]
        assert call_args.keywords == "python"
        assert call_args.location == "San Francisco"


# ---------------------------------------------------------------------------
# Scheduler start/stop lifecycle
# ---------------------------------------------------------------------------


class TestSchedulerLifecycle:
    async def test_start_stop(self):
        scheduler, _, _, _ = _make_scheduler()

        assert not scheduler.is_running

        scheduler.start()
        assert scheduler.is_running
        assert scheduler.next_run_time is not None

        scheduler.stop()
        assert not scheduler.is_running

    async def test_start_twice_is_safe(self):
        scheduler, _, _, _ = _make_scheduler()
        scheduler.start()
        scheduler.start()  # Should not raise
        assert scheduler.is_running
        scheduler.stop()

    async def test_stop_without_start_is_safe(self):
        scheduler, _, _, _ = _make_scheduler()
        scheduler.stop()  # Should not raise

    async def test_next_run_time_none_when_stopped(self):
        scheduler, _, _, _ = _make_scheduler()
        assert scheduler.next_run_time is None


# ---------------------------------------------------------------------------
# Per-user search
# ---------------------------------------------------------------------------


class TestPerUserSearch:
    """Test that the scheduler queries users and runs per-user searches."""

    async def test_per_user_search_two_users_different_prefs(self):
        """Two users with different search prefs produce separate scrape calls,
        each tagged with the correct user_id."""
        user_a = _user(
            "user-a",
            email="a@test.com",
            prefs=UserSearchPreferences(keywords="python", location="NYC"),
        )
        user_b = _user(
            "user-b",
            email="b@test.com",
            prefs=UserSearchPreferences(
                keywords="rust", location="Berlin", remote_filter="remote"
            ),
        )

        mock_user_repo = MagicMock()
        mock_user_repo.get_all_with_search_prefs = AsyncMock(
            return_value=[user_a, user_b]
        )

        jobs_a = [_job("j1"), _job("j2")]
        jobs_b = [_job("j3")]

        scraper = MagicMock()
        scraper.browser = MagicMock()
        scraper.browser.ensure_authenticated = AsyncMock()
        scraper.reset_seen = MagicMock()
        # Return different results for each call
        scraper.scrape_and_enrich = AsyncMock(side_effect=[jobs_a, jobs_b])

        settings = MagicMock()
        settings.linkedin_search_interval_hours = 1
        settings.scraped_jobs_path = None

        queue = JobQueue(max_size=100)
        scheduler = LinkedInSearchScheduler(
            settings, scraper, queue, user_repository=mock_user_repo
        )

        count = await scheduler.run_search()

        # Should have called scrape twice with different params
        assert scraper.scrape_and_enrich.call_count == 2
        first_params = scraper.scrape_and_enrich.call_args_list[0][0][0]
        assert first_params.keywords == "python"
        assert first_params.location == "NYC"
        second_params = scraper.scrape_and_enrich.call_args_list[1][0][0]
        assert second_params.keywords == "rust"
        assert second_params.location == "Berlin"
        assert second_params.remote_filter == "remote"

        # Total enqueued: 3 jobs
        assert count == 3
        assert queue.size() == 3

        # Verify user_id tags on queue items
        item1 = await queue.get()
        assert item1.user_id == "user-a"
        assert item1.job.job_id == "j1"
        item2 = await queue.get()
        assert item2.user_id == "user-a"
        assert item2.job.job_id == "j2"
        item3 = await queue.get()
        assert item3.user_id == "user-b"
        assert item3.job.job_id == "j3"

    async def test_skips_search_when_no_users_have_prefs(self):
        """When user_repository returns no users with prefs, search is skipped
        to avoid creating ownerless jobs that no authenticated user can access."""
        mock_user_repo = MagicMock()
        mock_user_repo.get_all_with_search_prefs = AsyncMock(return_value=[])

        scheduler, settings, scraper, queue = _make_scheduler(
            scrape_result=[_job("g1")],
            user_repository=mock_user_repo,
        )

        count = await scheduler.run_search()

        assert count == 0
        assert queue.is_empty()
        scraper.scrape_and_enrich.assert_not_awaited()

    async def test_skips_search_when_user_repo_is_none(self):
        """When user_repository is None, search is skipped."""
        scheduler, _, scraper, queue = _make_scheduler(
            scrape_result=[_job("g1")],
            user_repository=None,
        )

        count = await scheduler.run_search()

        assert count == 0
        assert queue.is_empty()

    async def test_skips_search_when_user_repo_raises(self):
        """When user_repository.get_all_with_search_prefs() raises, search is
        skipped gracefully (no ownerless jobs created)."""
        mock_user_repo = MagicMock()
        mock_user_repo.get_all_with_search_prefs = AsyncMock(
            side_effect=RuntimeError("DB error")
        )

        scheduler, _, scraper, queue = _make_scheduler(
            scrape_result=[_job("g1")],
            user_repository=mock_user_repo,
        )

        count = await scheduler.run_search()

        assert count == 0
        assert queue.is_empty()

    async def test_per_user_search_failure_continues_to_next_user(self):
        """If scraping fails for one user, the scheduler continues to the next."""
        user_a = _user(
            "user-a",
            prefs=UserSearchPreferences(keywords="fail"),
        )
        user_b = _user(
            "user-b",
            prefs=UserSearchPreferences(keywords="succeed"),
        )

        mock_user_repo = MagicMock()
        mock_user_repo.get_all_with_search_prefs = AsyncMock(
            return_value=[user_a, user_b]
        )

        scraper = MagicMock()
        scraper.browser = MagicMock()
        scraper.browser.ensure_authenticated = AsyncMock()
        scraper.reset_seen = MagicMock()
        scraper.scrape_and_enrich = AsyncMock(
            side_effect=[RuntimeError("Scrape failed"), [_job("j1")]]
        )

        settings = MagicMock()
        settings.linkedin_search_interval_hours = 1
        settings.scraped_jobs_path = None

        queue = JobQueue(max_size=100)
        scheduler = LinkedInSearchScheduler(
            settings, scraper, queue, user_repository=mock_user_repo
        )

        count = await scheduler.run_search()

        # Only user_b's job was enqueued
        assert count == 1
        item = await queue.get()
        assert item.user_id == "user-b"


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestLinkedInSearchAPI:
    """Test the LinkedIn search API endpoints via TestClient.

    These tests are skipped if WeasyPrint system libraries are not available,
    since importing src.api.main triggers the WeasyPrint import chain.
    """

    @pytest.fixture
    def client(self):
        """Create a test client with mocked AppContext.

        The real lifespan calls create_app_context() which requires
        JWT_SECRET and may trigger WeasyPrint imports. With
        raise_server_exceptions=False the lifespan failure is swallowed,
        leaving app.state.ctx as we set it beforehand.

        The module-level _consumer_manager is also mocked to prevent
        the trigger-search endpoint from importing the WeasyPrint chain
        (via job_queue -> _shared -> pdf_generator).
        """
        try:
            from fastapi.testclient import TestClient

            import src.api.main as main_module
            from src.config.settings import Settings
            from src.context import AppContext
            from src.models.user import User
            from src.services.jobs.job_queue import JobQueue

            # Create test settings that disable fixture replay mode
            test_settings = Settings(_env_file=None, seed_jobs_from_file=False)

            # Create a mock AppContext and attach to app.state
            mock_repo = MagicMock()
            mock_repo.initialize = AsyncMock()
            mock_repo.close = AsyncMock()
            ctx = AppContext(
                repository=mock_repo,
                settings=test_settings,
                prep_workflow=MagicMock(),
                retry_workflow=MagicMock(),
                job_queue=JobQueue(),
            )
            main_module.app.state.ctx = ctx

            # Override auth dependency to return a test user
            test_user = User(id="test-user", email="test@example.com", display_name="Test")
            main_module.app.dependency_overrides[main_module.get_current_user] = lambda: test_user

            # Override module-level settings for the trigger_search endpoint check
            original_settings = main_module.settings
            main_module.settings = test_settings

            # Mock the consumer manager to avoid WeasyPrint import chain
            original_cm = main_module._consumer_manager
            mock_cm = MagicMock()
            mock_cm.task = MagicMock()
            mock_cm.task.done.return_value = False  # pretend consumer is running
            main_module._consumer_manager = mock_cm

            yield TestClient(main_module.app, raise_server_exceptions=False), ctx

            main_module.settings = original_settings
            main_module._consumer_manager = original_cm
            main_module.app.dependency_overrides.pop(main_module.get_current_user, None)
        except OSError:
            pytest.skip("WeasyPrint system libraries not available")

    def test_search_status_no_scheduler(self, client):
        test_client, ctx = client
        original = ctx.scheduler
        ctx.scheduler = None
        try:
            response = test_client.get("/api/jobs/linkedin-search/status")
            assert response.status_code == 200
            data = response.json()
            assert data["running"] is False
            assert data["last_run_time"] is None
            assert data["last_run_jobs"] == 0
        finally:
            ctx.scheduler = original

    def test_search_status_with_scheduler(self, client):
        test_client, ctx = client
        mock_scheduler = MagicMock()
        mock_scheduler.is_running = True
        mock_scheduler.last_run_time = datetime(2026, 3, 6, 12, 0, 0)
        mock_scheduler.last_run_jobs = 5
        mock_scheduler.next_run_time = datetime(2026, 3, 6, 13, 0, 0)

        original = ctx.scheduler
        ctx.scheduler = mock_scheduler
        try:
            response = test_client.get("/api/jobs/linkedin-search/status")
            assert response.status_code == 200
            data = response.json()
            assert data["running"] is True
            assert data["last_run_jobs"] == 5
            assert "2026-03-06" in data["last_run_time"]
        finally:
            ctx.scheduler = original

    def test_trigger_search_returns_started(self, client):
        test_client, ctx = client

        mock_scheduler = MagicMock()
        mock_scheduler.run_search = AsyncMock(return_value=3)

        original = ctx.scheduler
        ctx.scheduler = mock_scheduler
        try:
            response = test_client.post("/api/jobs/linkedin-search")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "started"
            assert data["message"] == "LinkedIn search triggered"
        finally:
            ctx.scheduler = original
