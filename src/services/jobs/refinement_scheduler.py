"""Periodic scheduler for the auto-refining job filter.

A standalone APScheduler interval job (default weekly) that runs the
refinement cycle for every opted-in user. Independent of the LinkedIn search
scheduler so refinement works even when scheduled scraping is disabled.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .interval_scheduler import IntervalScheduler

if TYPE_CHECKING:
    from src.context import AppContext

logger = logging.getLogger(__name__)


class RefinementScheduler(IntervalScheduler):
    """Runs the filter-refinement cycle on a fixed interval."""

    job_id = "filter_refinement"
    label = "Refinement scheduler"

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._lock = asyncio.Lock()

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

            self._last_run_time = datetime.now(tz=timezone.utc)
            return created

    def start(self) -> None:
        self.start_interval(
            self.run_cycle, hours=self._ctx.settings.auto_refine_interval_hours
        )
