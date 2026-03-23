"""LinkedIn search scheduler — orchestrates periodic and on-demand job searches.

Wraps APScheduler's AsyncIOScheduler to run LinkedIn scraping at configurable
intervals, feeding results into the async job queue for workflow processing.
"""

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config.settings import Settings
from src.services.job_queue import JobQueue
from src.services.linkedin_scraper import LinkedInJobScraper
from src.services.linkedin_search import LinkedInSearchParams

logger = logging.getLogger(__name__)


class LinkedInSearchScheduler:
    """Orchestrates periodic LinkedIn job searches via APScheduler."""

    def __init__(
        self,
        settings: Settings,
        scraper: LinkedInJobScraper,
        queue: JobQueue,
    ) -> None:
        self.settings = settings
        self.scraper = scraper
        self.queue = queue
        self._scheduler = AsyncIOScheduler()
        self._search_lock = asyncio.Lock()
        self._last_run_time: datetime | None = None
        self._last_run_jobs: int = 0
        self._running = False

    async def run_search(self) -> int:
        """Execute one search cycle: authenticate, scrape, enqueue.

        Returns the number of jobs enqueued. Never raises — all exceptions
        are caught and logged so the scheduler keeps running.

        The locked() check and acquire are not separated by an await,
        so no other coroutine can interleave in the single-threaded event loop.
        """
        if self._search_lock.locked():
            logger.warning("Search already in progress, skipping")
            return 0

        async with self._search_lock:
            return await self._do_search()

    async def _do_search(self) -> int:
        """Internal search implementation."""
        try:
            logger.info("Starting LinkedIn search cycle")

            # Reset dedup state so returning jobs from previous cycles are not skipped
            self.scraper.reset_seen()

            # Ensure authenticated
            await self.scraper.browser.ensure_authenticated()

            # Build search params from settings
            params = LinkedInSearchParams(
                keywords=self.settings.linkedin_search_keywords,
                location=self.settings.linkedin_search_location,
                remote_filter=self.settings.linkedin_search_remote_filter,
                date_posted=self.settings.linkedin_search_date_posted,
                experience_level=self.settings.linkedin_search_experience_level,
                job_type=self.settings.linkedin_search_job_type,
                easy_apply_only=self.settings.linkedin_search_easy_apply_only,
                max_jobs=self.settings.linkedin_search_max_jobs,
            )

            # Scrape and enrich
            jobs = await self.scraper.scrape_and_enrich(params)
            logger.info("Scraped %d jobs from LinkedIn", len(jobs))

            # Auto-record scraped jobs to fixture file
            fixture_path = getattr(self.settings, "scraped_jobs_path", None)
            if jobs and isinstance(fixture_path, str):
                try:
                    from src.services.job_fixtures import save_scraped_jobs
                    save_scraped_jobs(jobs, fixture_path)
                except Exception:
                    logger.warning("Failed to save scraped jobs to fixture file", exc_info=True)

            # Enqueue for workflow processing
            enqueued = await self.queue.put_batch(jobs)
            logger.info("Enqueued %d jobs (queue size: %d)", enqueued, self.queue.size())

            self._last_run_time = datetime.now(tz=timezone.utc)
            self._last_run_jobs = enqueued
            return enqueued

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
