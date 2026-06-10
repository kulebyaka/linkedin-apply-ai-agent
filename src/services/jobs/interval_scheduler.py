"""Shared APScheduler lifecycle for single-interval background schedulers.

Both the LinkedIn search scheduler and the filter-refinement scheduler run a
single periodic interval job and need the same start/stop/is_running/
last_run_time/next_run_time plumbing. That lifecycle lives here so each concrete
scheduler only has to supply its callback, interval, and job id/label.
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


class IntervalScheduler:
    """Base class wrapping APScheduler for one periodic interval job.

    Subclasses set :attr:`job_id` / :attr:`label` and call
    :meth:`start_interval` from their own ``start()`` with the resolved
    interval and callback. This base owns the ``AsyncIOScheduler`` instance and
    the shared lifecycle; domain-specific state (locks, run history) stays on
    the subclass.
    """

    #: Stable APScheduler job id; subclasses must override.
    job_id: str = "interval_job"
    #: Human label used in log lines; subclasses should override.
    label: str = "Interval scheduler"

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._running = False
        self._last_run_time: datetime | None = None

    def start_interval(self, func, *, hours: int) -> None:
        """Register ``func`` on a fixed hourly interval and start the scheduler."""
        if self._running:
            logger.warning("%s already running", self.label)
            return
        self._scheduler.add_job(
            func,
            "interval",
            hours=hours,
            id=self.job_id,
            replace_existing=True,
        )
        self._scheduler.start()
        self._running = True
        logger.info("%s started (every %d hours)", self.label, hours)

    def stop(self) -> None:
        """Shutdown the scheduler gracefully."""
        if not self._running:
            return
        self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("%s stopped", self.label)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_run_time(self) -> datetime | None:
        return self._last_run_time

    @property
    def next_run_time(self) -> datetime | None:
        """Return the next scheduled run time, if any."""
        if not self._running:
            return None
        job = self._scheduler.get_job(self.job_id)
        return job.next_run_time if job else None
