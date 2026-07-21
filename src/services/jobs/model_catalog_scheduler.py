"""Daily refresh of the dynamic model catalog (LiteLLM pricing JSON).

Reuses the shared :class:`IntervalScheduler` (APScheduler) lifecycle. Runs
independently of the LinkedIn search scheduler so the catalog stays current
even when scheduled scraping is disabled.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .interval_scheduler import IntervalScheduler

if TYPE_CHECKING:
    from src.context import AppContext

logger = logging.getLogger(__name__)


class ModelCatalogScheduler(IntervalScheduler):
    """Runs ``ctx.refresh_model_catalog`` on a fixed daily interval."""

    job_id = "model_catalog_refresh"
    label = "Model catalog refresh"

    def __init__(self, ctx: "AppContext") -> None:
        super().__init__()
        self._ctx = ctx

    def start(self) -> None:
        hours = max(1, self._ctx.settings.model_catalog_refresh_hours)
        self.start_interval(self._refresh, hours=hours)

    async def _refresh(self) -> None:
        await self._ctx.refresh_model_catalog()
