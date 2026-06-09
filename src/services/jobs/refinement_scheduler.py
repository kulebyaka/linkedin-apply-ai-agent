"""Periodic scheduler for the auto-refining job filter.

A standalone APScheduler interval job (default weekly) that runs the
refinement cycle for every opted-in user. Independent of the LinkedIn search
scheduler so refinement works even when scheduled scraping is disabled.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

if TYPE_CHECKING:
    from src.context import AppContext

logger = logging.getLogger(__name__)


class RefinementScheduler:
    """Runs the filter-refinement cycle on a fixed interval."""

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._scheduler = AsyncIOScheduler()
        self._lock = asyncio.Lock()
        self._running = False
        self._last_run_time: datetime | None = None

    async def run_cycle(self) -> int:
        """Run one refinement cycle for all opted-in users. Never raises."""
        if self._lock.locked():
            logger.warning("Refinement cycle already in progress, skipping")
            return 0
        async with self._lock:
            from src.services.jobs.refinement import run_refinement_for_all

            try:
                created = await run_refinement_for_all(self._ctx)
            except Exception:
                logger.exception("Refinement cycle raised")
                created = 0
            from datetime import timezone

            self._last_run_time = datetime.now(tz=timezone.utc)
            return created

    def start(self) -> None:
        if self._running:
            logger.warning("Refinement scheduler already running")
            return
        interval_hours = self._ctx.settings.auto_refine_interval_hours
        self._scheduler.add_job(
            self.run_cycle,
            "interval",
            hours=interval_hours,
            id="filter_refinement",
            replace_existing=True,
        )
        self._scheduler.start()
        self._running = True
        logger.info(
            "Refinement scheduler started (every %d hours)", interval_hours
        )

    def stop(self) -> None:
        if not self._running:
            return
        self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("Refinement scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_run_time(self) -> datetime | None:
        return self._last_run_time
