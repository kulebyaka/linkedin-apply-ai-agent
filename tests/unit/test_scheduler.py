"""Tests for LinkedIn search scheduler and API endpoints."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.job import ScrapedJob
from src.services.job_queue import JobQueue
from src.services.scheduler import LinkedInSearchScheduler

pytestmark = pytest.mark.asyncio


def _job(job_id: str) -> ScrapedJob:
    return ScrapedJob(job_id=job_id, title="", company="", location="", url="")


# ---------------------------------------------------------------------------
# Helper: build a scheduler with mocked dependencies
# ---------------------------------------------------------------------------


def _make_scheduler(
    *,
    scrape_result: list[ScrapedJob] | None = None,
    scrape_error: Exception | None = None,
    interval_hours: int = 1,
) -> tuple[LinkedInSearchScheduler, MagicMock, MagicMock, JobQueue]:
    """Return (scheduler, mock_settings, mock_scraper, queue)."""
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

    queue = JobQueue(max_size=100)
    scheduler = LinkedInSearchScheduler(settings, scraper, queue)

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

    async def test_run_search_builds_params_from_settings(self):
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
# API endpoint tests
# ---------------------------------------------------------------------------


class TestLinkedInSearchAPI:
    """Test the LinkedIn search API endpoints via TestClient.

    These tests are skipped if WeasyPrint system libraries are not available,
    since importing src.api.main triggers the WeasyPrint import chain.
    """

    @pytest.fixture
    def client(self):
        """Create a test client with mocked scheduler state."""
        try:
            from fastapi.testclient import TestClient

            import src.api.main as main_module

            return TestClient(main_module.app), main_module
        except OSError:
            pytest.skip("WeasyPrint system libraries not available")

    def test_search_status_no_scheduler(self, client):
        test_client, main_module = client
        original = main_module._linkedin_scheduler
        main_module._linkedin_scheduler = None
        try:
            response = test_client.get("/api/jobs/linkedin-search/status")
            assert response.status_code == 200
            data = response.json()
            assert data["running"] is False
            assert data["last_run_time"] is None
            assert data["last_run_jobs"] == 0
        finally:
            main_module._linkedin_scheduler = original

    def test_search_status_with_scheduler(self, client):
        test_client, main_module = client
        mock_scheduler = MagicMock()
        mock_scheduler.is_running = True
        mock_scheduler.last_run_time = datetime(2026, 3, 6, 12, 0, 0)
        mock_scheduler.last_run_jobs = 5
        mock_scheduler.next_run_time = datetime(2026, 3, 6, 13, 0, 0)

        original = main_module._linkedin_scheduler
        main_module._linkedin_scheduler = mock_scheduler
        try:
            response = test_client.get("/api/jobs/linkedin-search/status")
            assert response.status_code == 200
            data = response.json()
            assert data["running"] is True
            assert data["last_run_jobs"] == 5
            assert "2026-03-06" in data["last_run_time"]
        finally:
            main_module._linkedin_scheduler = original

    def test_trigger_search_returns_started(self, client):
        test_client, main_module = client

        mock_scheduler = MagicMock()
        mock_scheduler.run_search = AsyncMock(return_value=3)

        original = main_module._linkedin_scheduler
        main_module._linkedin_scheduler = mock_scheduler
        try:
            response = test_client.post("/api/jobs/linkedin-search")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "started"
            assert data["message"] == "LinkedIn search triggered"
        finally:
            main_module._linkedin_scheduler = original
