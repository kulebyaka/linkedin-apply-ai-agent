"""LinkedIn search scheduler — orchestrates periodic and on-demand job searches.

Wraps APScheduler's AsyncIOScheduler to run LinkedIn scraping at configurable
intervals, feeding results into the async job queue for workflow processing.

Supports per-user search: iterates users with configured search preferences
and tags scraped jobs with their user_id. Skips the search cycle when no
users have preferences configured.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from src.config.settings import Settings
from src.models.state_machine import BusinessState
from src.models.unified import JobRecord
from src.services.linkedin.linkedin_search import LinkedInSearchParams, LinkedInSearchURLBuilder

from .interval_scheduler import IntervalScheduler
from .job_queue import JobQueue, _scoped_job_id, _should_retry_scrape

if TYPE_CHECKING:
    from src.services.alerts import AdminAlertService
    from src.services.auth.user_repository import UserRepository
    from src.services.db.job_repository import JobRepository
    from src.services.linkedin.linkedin_scraper import LinkedInJobScraper

logger = logging.getLogger(__name__)

RunReason = Literal[
    "ok",
    "no_results",
    "no_users",
    "scrape_failed",
    "auth_failed",
]


@dataclass
class UserLastRun:
    """Outcome of the most recent search run for one user.

    search_url lets the user open the exact LinkedIn query in a browser
    to verify whether LinkedIn itself actually returns anything.

    ``jobs_found`` is the raw count scraped from LinkedIn (pre-dedup).
    ``enqueued`` is how many were actually new and queued for processing;
    ``deduped`` is how many were skipped because we already had them. So
    ``jobs_found == enqueued + deduped`` on a successful run.
    """
    time: datetime
    jobs_found: int
    reason: RunReason
    search_url: str | None
    message: str | None = None
    enqueued: int = 0
    deduped: int = 0


# Cap on retained run-history entries. Searches run hourly per user, so 500
# entries is ~20 days for a single active user. History is in-memory only and
# resets on process restart (e.g. on deploy).
_RUN_HISTORY_MAXLEN = 500


@dataclass
class SearchRun:
    """One recorded search run, tagged with the user it ran for.

    Backs the admin run-history view (every run, not just the latest per user).
    """
    user_id: str
    run: UserLastRun


class LinkedInSearchScheduler(IntervalScheduler):
    """Orchestrates periodic LinkedIn job searches via APScheduler."""

    job_id = "linkedin_search"
    label = "LinkedIn search scheduler"

    def __init__(
        self,
        settings: Settings,
        scraper: LinkedInJobScraper,
        queue: JobQueue,
        user_repository: UserRepository | None = None,
        admin_alert_service: AdminAlertService | None = None,
        job_repository: JobRepository | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.scraper = scraper
        self.queue = queue
        self.user_repository = user_repository
        self.admin_alert_service = admin_alert_service
        self.job_repository = job_repository
        self._last_run_jobs: int = 0
        self._last_run_per_user: dict[str, UserLastRun] = {}
        self._run_history: deque[SearchRun] = deque(maxlen=_RUN_HISTORY_MAXLEN)
        # User ids whose search is actively executing right now. Drives the
        # per-user "search running" status so a user only sees the spinner for
        # their own in-flight search, not any global scheduler activity.
        self._in_progress_users: set[str] = set()
        # Per-user locks dedupe concurrent searches for the SAME user (a manual
        # click landing while that user's scheduled search runs). Searches for
        # DIFFERENT users are never blocked by each other here.
        self._user_search_locks: dict[str | None, asyncio.Lock] = {}
        # The scraper drives a single shared Playwright page, so actual browser
        # work (auth + scrape) must be serialized. This lock does that fairly
        # (asyncio.Lock is FIFO): a manual search queues for the browser between
        # scheduled users instead of being dropped or stuck behind the whole
        # cycle.
        self._browser_lock = asyncio.Lock()

    @property
    def search_in_progress(self) -> bool:
        """True if any user's search is actively executing.

        Global across all users — used by admin views. For the per-user
        spinner use :meth:`search_in_progress_for` instead.
        """
        return bool(self._in_progress_users)

    def search_in_progress_for(self, user_id: str) -> bool:
        """True if a search is actively executing for this specific user."""
        return user_id in self._in_progress_users

    def _user_lock(self, user_id: str | None) -> asyncio.Lock:
        """Return (creating if needed) the dedupe lock for one user."""
        lock = self._user_search_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._user_search_locks[user_id] = lock
        return lock

    async def run_search(self, user_id: str | None = None) -> int:
        """Execute one search cycle: authenticate, scrape, enqueue.

        Args:
            user_id: If provided, only search for this user's preferences.
                     If None, search for all users (scheduled runs).

        Returns the number of jobs enqueued. Never raises — all exceptions
        are caught and logged so the scheduler keeps running.

        Concurrency is now per-user, not global: a manual search for one user
        is not dropped just because another user's scheduled search is mid
        cycle. Deduping (skip a duplicate search for the *same* user) and
        serialization of the shared browser happen inside
        :meth:`_search_for_user`.
        """
        return await self._do_search(user_id=user_id)

    async def _do_search(self, user_id: str | None = None) -> int:
        """Internal search implementation.

        Args:
            user_id: If provided, only search for this user's preferences.
                     If None, search for all users (scheduled runs).

        Queries users with configured search preferences and runs a
        separate scrape per user. Skips the cycle when no users have
        preferences configured.
        """
        try:
            logger.info("Starting LinkedIn search cycle")

            # Build per-user search plans.
            search_plans: list[tuple[str | None, LinkedInSearchParams]] = []

            if self.user_repository is not None:
                try:
                    users = await self.user_repository.get_all_with_search_prefs()
                    for user in users:
                        # When user_id filter is set, only search for that user
                        if user_id is not None and user.id != user_id:
                            continue
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
                        "Failed to load users with search prefs, skipping search",
                        exc_info=True,
                    )

            # Skip search if no users have configured search preferences.
            # Checked before authenticate so we avoid browser/auth work when
            # there is nothing to search.
            if not search_plans:
                logger.info(
                    "No users with search preferences found — skipping LinkedIn search. "
                    "Configure search preferences in Settings to enable scheduled searches."
                )
                now = datetime.now(tz=timezone.utc)
                self._last_run_time = now
                self._last_run_jobs = 0
                self._record_run(
                    user_id=user_id,
                    time=now,
                    jobs_found=0,
                    reason="no_users",
                    search_url=None,
                    message="Search preferences not configured.",
                )
                return 0

            # Run each user's search. Different users are independent — one
            # user's browser turn or failure does not block or drop another's.
            # Each ``_search_for_user`` serializes its own browser access via
            # ``_browser_lock`` (single shared Playwright page) and dedupes a
            # concurrent duplicate for the same user.
            total_enqueued = 0
            for plan_user_id, params in search_plans:
                total_enqueued += await self._search_for_user(plan_user_id, params)

            self._last_run_time = datetime.now(tz=timezone.utc)
            self._last_run_jobs = total_enqueued
            return total_enqueued

        except Exception:
            logger.exception("LinkedIn search cycle failed")
            self._last_run_time = datetime.now(tz=timezone.utc)
            self._last_run_jobs = 0
            return 0

    async def _search_for_user(
        self, plan_user_id: str | None, params: LinkedInSearchParams
    ) -> int:
        """Run one user's search end-to-end. Never raises; returns enqueued.

        Dedupes duplicate concurrent searches for the same user, marks that
        user in-progress for the per-user status, and serializes the shared
        browser via ``_browser_lock`` so overlapping searches never drive one
        Playwright page from two coroutines.
        """
        lock = self._user_lock(plan_user_id)
        if lock.locked():
            logger.info(
                "Search already in progress for user=%s, skipping duplicate",
                plan_user_id or "global",
            )
            return 0

        async with lock:
            if plan_user_id is not None:
                self._in_progress_users.add(plan_user_id)
            try:
                return await self._run_user_plan(plan_user_id, params)
            finally:
                if plan_user_id is not None:
                    self._in_progress_users.discard(plan_user_id)

    async def _run_user_plan(
        self, plan_user_id: str | None, params: LinkedInSearchParams
    ) -> int:
        """Authenticate + scrape (browser-locked) then persist/enqueue."""
        search_url = LinkedInSearchURLBuilder.build_url(params)

        # Serialize all shared-browser work. DB/queue work below runs outside
        # the lock so a slow persist for one user doesn't hold the browser from
        # the next waiter.
        async with self._browser_lock:
            # Reset scraper dedup state before each user's search so the same
            # LinkedIn posting can be found independently by different users.
            self.scraper.reset_seen()

            # Scraping uses LinkedIn's public Jobs Search page which serves
            # results without auth — skip /feed validation so we don't trip
            # anti-bot detection. Cookies are still loaded in case they help
            # widen results, but they're treated as best-effort.
            try:
                await self.scraper.browser.ensure_authenticated(validate_session=False)
            except Exception as exc:
                logger.exception("LinkedIn authentication failed")
                self._record_run(
                    user_id=plan_user_id,
                    time=datetime.now(tz=timezone.utc),
                    jobs_found=0,
                    reason="auth_failed",
                    search_url=None,
                    message=str(exc),
                )
                return 0

            try:
                jobs = await self.scraper.scrape_and_enrich(params)
            except Exception as exc:
                logger.exception(
                    "Search failed for user=%s", plan_user_id or "global"
                )
                self._record_run(
                    user_id=plan_user_id,
                    time=datetime.now(tz=timezone.utc),
                    jobs_found=0,
                    reason="scrape_failed",
                    search_url=search_url,
                    message=str(exc),
                )
                return 0

        # --- browser lock released; the rest is DB/queue-bound ---
        logger.info(
            "Scraped %d jobs for user=%s", len(jobs), plan_user_id or "global"
        )

        if self.admin_alert_service is not None and jobs:
            empty = sum(1 for j in jobs if not (j.description or "").strip())
            try:
                await self.admin_alert_service.maybe_alert_unauthenticated_session(
                    total_jobs=len(jobs),
                    empty_descriptions=empty,
                    user_id=plan_user_id,
                    search_url=search_url,
                )
            except Exception:
                logger.exception("Admin alert dispatch failed")

        # Auto-record scraped jobs to fixture file
        fixture_path = getattr(self.settings, "scraped_jobs_path", None)
        if jobs and isinstance(fixture_path, str):
            try:
                from .job_fixtures import save_scraped_jobs
                save_scraped_jobs(jobs, fixture_path)
            except Exception:
                logger.warning(
                    "Failed to save scraped jobs to fixture file",
                    exc_info=True,
                )

        try:
            # Persist a QUEUED row per discovered job before enqueueing so the
            # work is durable across restarts and visible in the UI
            # immediately. Deduped jobs already in a terminal/in-progress state
            # are not re-enqueued.
            enqueued, deduped = await self._persist_and_enqueue(jobs, plan_user_id)
        except Exception as exc:
            logger.exception(
                "Persist/enqueue failed for user=%s", plan_user_id or "global"
            )
            self._record_run(
                user_id=plan_user_id,
                time=datetime.now(tz=timezone.utc),
                jobs_found=len(jobs),
                reason="scrape_failed",
                search_url=search_url,
                message=str(exc),
            )
            return 0

        logger.info(
            "jobs_discovered total=%d enqueued=%d deduped=%d "
            "user=%s queue_size=%d",
            len(jobs),
            enqueued,
            deduped,
            plan_user_id or "global",
            self.queue.size(),
        )
        self._record_run(
            user_id=plan_user_id,
            time=datetime.now(tz=timezone.utc),
            jobs_found=len(jobs),
            reason="ok" if jobs else "no_results",
            search_url=search_url,
            enqueued=enqueued,
            deduped=deduped,
        )
        return enqueued

    async def _persist_and_enqueue(
        self, jobs: list, plan_user_id: str | None
    ) -> tuple[int, int]:
        """Create a QUEUED JobRecord per discovered job, then enqueue.

        Returns (enqueued, deduped) counts. A job is "deduped" when an
        existing record is present and `_should_retry_scrape` returns False —
        we leave the row untouched and skip the enqueue.

        If the repository is unavailable (legacy wiring), falls back to a
        plain put_batch so the consumer still picks the jobs up.
        """
        if self.job_repository is None:
            enqueued = await self.queue.put_batch(jobs, user_id=plan_user_id)
            return enqueued, 0

        enqueued = 0
        deduped = 0
        now = datetime.now(tz=timezone.utc)

        for scraped_job in jobs:
            scoped_id = _scoped_job_id(scraped_job.job_id, plan_user_id)
            try:
                existing = await self.job_repository.get(scoped_id)
            except Exception:
                logger.warning(
                    "Pre-enqueue dedup lookup failed for %s; enqueueing without dedup",
                    scoped_id,
                    exc_info=True,
                )
                existing = None

            if existing is not None and not _should_retry_scrape(existing):
                deduped += 1
                continue

            if existing is None:
                try:
                    await self.job_repository.create(
                        JobRecord(
                            job_id=scoped_id,
                            user_id=plan_user_id or "",
                            source="linkedin",
                            mode="full",
                            status=BusinessState.QUEUED,
                            job_posting={
                                "title": scraped_job.title,
                                "company": scraped_job.company,
                                "url": scraped_job.url,
                                "location": scraped_job.location,
                            },
                            raw_input=scraped_job.model_dump(),
                            session_authenticated=scraped_job.session_authenticated,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                except Exception:
                    logger.warning(
                        "Failed to persist QUEUED row for %s; skipping enqueue",
                        scoped_id,
                        exc_info=True,
                    )
                    continue

            try:
                await self.queue.put(scraped_job, user_id=plan_user_id)
                enqueued += 1
            except Exception:
                logger.warning(
                    "Failed to enqueue %s after persisting; will be picked up by recovery",
                    scoped_id,
                    exc_info=True,
                )

        return enqueued, deduped

    def start(self) -> None:
        """Add interval job and start the APScheduler."""
        self.start_interval(
            self.run_search, hours=self.settings.linkedin_search_interval_hours
        )

    @property
    def last_run_jobs(self) -> int:
        return self._last_run_jobs

    def _record_run(
        self,
        *,
        user_id: str | None,
        time: datetime,
        jobs_found: int,
        reason: RunReason,
        search_url: str | None,
        message: str | None = None,
        enqueued: int = 0,
        deduped: int = 0,
    ) -> None:
        """Record a run outcome as the per-user latest and append to history.

        Runs without a user_id (e.g. a scheduled cycle that found no users
        with preferences) are not recorded — there is no user to attribute
        them to and they carry no actionable signal.
        """
        if user_id is None:
            return
        run = UserLastRun(
            time=time,
            jobs_found=jobs_found,
            reason=reason,
            search_url=search_url,
            message=message,
            enqueued=enqueued,
            deduped=deduped,
        )
        self._last_run_per_user[user_id] = run
        self._run_history.append(SearchRun(user_id=user_id, run=run))

    def get_last_run_for_user(self, user_id: str) -> UserLastRun | None:
        """Return the most recent run outcome for a specific user, if any."""
        return self._last_run_per_user.get(user_id)

    def get_jobs_state(self) -> list[dict]:
        """Return per-user scheduler state for the admin dashboard.

        For each user with a recorded run, returns the user_id, last run time,
        the global next scheduled run, and the last status reason.
        """
        next_run = self.next_run_time
        out: list[dict] = []
        for user_id, run in self._last_run_per_user.items():
            out.append(
                {
                    "user_id": user_id,
                    "last_run_at": run.time.isoformat() if run.time else None,
                    "next_run_at": next_run.isoformat() if next_run else None,
                    "last_status": run.reason,
                    "jobs_found": run.jobs_found,
                    "enqueued": run.enqueued,
                    "deduped": run.deduped,
                    "message": run.message,
                    "search_url": run.search_url,
                }
            )
        return out

    def get_run_history(self, limit: int = 100) -> list[dict]:
        """Return recorded search runs, newest first, for the admin dashboard.

        Unlike ``get_jobs_state`` (latest run per user), this returns every
        recorded run so admins can see the full scheduling timeline. History
        is in-memory and resets on restart.
        """
        items = list(self._run_history)[-limit:] if limit > 0 else list(self._run_history)
        out: list[dict] = []
        for entry in reversed(items):
            run = entry.run
            out.append(
                {
                    "user_id": entry.user_id,
                    "run_at": run.time.isoformat() if run.time else None,
                    "status": run.reason,
                    "jobs_found": run.jobs_found,
                    "enqueued": run.enqueued,
                    "deduped": run.deduped,
                    "message": run.message,
                    "search_url": run.search_url,
                }
            )
        return out
