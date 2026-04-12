"""LinkedIn search scheduler — orchestrates periodic and on-demand job searches.

Wraps APScheduler's AsyncIOScheduler to run LinkedIn scraping at configurable
intervals, feeding results into the async job queue for workflow processing.

Supports per-user search: iterates users with configured search preferences
and tags scraped jobs with their user_id. Falls back to env-var-based global
search params when no users have preferences configured.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config.settings import Settings
from src.services.job_queue import JobQueue
from src.services.linkedin_search import LinkedInSearchParams

if TYPE_CHECKING:
    from src.services.linkedin_scraper import LinkedInJobScraper
    from src.services.user_repository import UserRepository

logger = logging.getLogger(__name__)


class LinkedInSearchScheduler:
    """Orchestrates periodic LinkedIn job searches via APScheduler."""

    def __init__(
        self,
        settings: Settings,
        scraper: LinkedInJobScraper,
        queue: JobQueue,
        user_repository: UserRepository | None = None,
    ) -> None:
        self.settings = settings
        self.scraper = scraper
        self.queue = queue
        self.user_repository = user_repository
        self._scheduler = AsyncIOScheduler()
        self._search_lock = asyncio.Lock()
        self._last_run_time: datetime | None = None
        self._last_run_jobs: int = 0
        self._running = False

    async def run_search(self) -> int:
        """Execute one search cycle: authenticate, scrape, enqueue.

        Returns the number of jobs enqueued. Never raises — all exceptions
        are caught and logged so the scheduler keeps running.

        The locked() pre-check is a fast-path optimisation: if a search is
        already running we skip immediately instead of queuing behind the lock.
        A narrow race exists where two callers both see locked()==False and
        then one waits on the lock, but the outcome is correct (second search
        runs after the first finishes rather than being skipped).
        """
        if self._search_lock.locked():
            logger.warning("Search already in progress, skipping")
            return 0

        async with self._search_lock:
            return await self._do_search()

    def _build_global_params(self) -> LinkedInSearchParams:
        """Build search params from environment-variable-based settings."""
        return LinkedInSearchParams(
            keywords=self.settings.linkedin_search_keywords,
            location=self.settings.linkedin_search_location,
            remote_filter=self.settings.linkedin_search_remote_filter,
            date_posted=self.settings.linkedin_search_date_posted,
            experience_level=self.settings.linkedin_search_experience_level,
            job_type=self.settings.linkedin_search_job_type,
            easy_apply_only=self.settings.linkedin_search_easy_apply_only,
            max_jobs=self.settings.linkedin_search_max_jobs,
        )

    async def _do_search(self) -> int:
        """Internal search implementation.

        If a user_repository is available, queries all users with configured
        search preferences and runs a separate scrape per user. Falls back
        to global env-var settings when no users have preferences.
        """
        try:
            logger.info("Starting LinkedIn search cycle")

            # Reset dedup state so returning jobs from previous cycles are not skipped
            self.scraper.reset_seen()

            # Ensure authenticated
            await self.scraper.browser.ensure_authenticated()

            # Build per-user search plans, or fall back to global params
            search_plans: list[tuple[str | None, LinkedInSearchParams]] = []

            if self.user_repository is not None:
                try:
                    users = await self.user_repository.get_all_with_search_prefs()
                    for user in users:
                        prefs = user.search_preferences
                        if prefs is None:
                            continue
                        params = LinkedInSearchParams(
                            keywords=prefs.keywords,
                            location=prefs.location,
                            remote_filter=prefs.remote_filter,
                            date_posted=prefs.date_posted,
                            experience_level=prefs.experience_level,
                            job_type=prefs.job_type,
                            easy_apply_only=prefs.easy_apply_only,
                            max_jobs=prefs.max_jobs,
                        )
                        search_plans.append((user.id, params))
                    if search_plans:
                        logger.info(
                            "Running per-user search for %d user(s)", len(search_plans)
                        )
                except Exception:
                    logger.warning(
                        "Failed to load users with search prefs, falling back to global params",
                        exc_info=True,
                    )

            # Fallback: no users with prefs → use global env-var settings
            if not search_plans:
                logger.info("No per-user search prefs found, using global settings")
                search_plans.append((None, self._build_global_params()))

            total_enqueued = 0

            for user_id, params in search_plans:
                try:
                    jobs = await self.scraper.scrape_and_enrich(params)
                    logger.info(
                        "Scraped %d jobs for user=%s", len(jobs), user_id or "global"
                    )

                    # Auto-record scraped jobs to fixture file
                    fixture_path = getattr(self.settings, "scraped_jobs_path", None)
                    if jobs and isinstance(fixture_path, str):
                        try:
                            from src.services.job_fixtures import save_scraped_jobs
                            save_scraped_jobs(jobs, fixture_path)
                        except Exception:
                            logger.warning(
                                "Failed to save scraped jobs to fixture file",
                                exc_info=True,
                            )

                    # Enqueue with user_id tag
                    enqueued = await self.queue.put_batch(jobs, user_id=user_id)
                    logger.info(
                        "Enqueued %d jobs for user=%s (queue size: %d)",
                        enqueued,
                        user_id or "global",
                        self.queue.size(),
                    )
                    total_enqueued += enqueued
                except Exception:
                    logger.exception(
                        "Search failed for user=%s, continuing to next",
                        user_id or "global",
                    )

            self._last_run_time = datetime.now(tz=timezone.utc)
            self._last_run_jobs = total_enqueued
            return total_enqueued

        except Exception:
            logger.exception("LinkedIn search cycle failed")
            self._last_run_time = datetime.now(tz=timezone.utc)
            self._last_run_jobs = 0
            return 0

    def start(self) -> None:
        """Add interval job and start the APScheduler."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        interval_hours = self.settings.linkedin_search_interval_hours
        self._scheduler.add_job(
            self.run_search,
            "interval",
            hours=interval_hours,
            id="linkedin_search",
            replace_existing=True,
        )
        self._scheduler.start()
        self._running = True
        logger.info("LinkedIn search scheduler started (every %d hours)", interval_hours)

    def stop(self) -> None:
        """Shutdown the scheduler gracefully."""
        if not self._running:
            return
        self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("LinkedIn search scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_run_time(self) -> datetime | None:
        return self._last_run_time

    @property
    def last_run_jobs(self) -> int:
        return self._last_run_jobs

    @property
    def next_run_time(self) -> datetime | None:
        """Return the next scheduled run time, if any."""
        if not self._running:
            return None
        job = self._scheduler.get_job("linkedin_search")
        if job:
            return job.next_run_time
        return None
