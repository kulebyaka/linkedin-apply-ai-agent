"""Async job queue for feeding scraped LinkedIn jobs into the preparation workflow.

Provides a thin wrapper around asyncio.Queue with batch operations and a
singleton accessor so other modules can share a single queue instance.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.models.job import ScrapedJob

logger = logging.getLogger(__name__)


class JobQueue:
    """Async queue for ScrapedJob instances awaiting workflow processing."""

    def __init__(self, max_size: int = 100) -> None:
        self._queue: asyncio.Queue[ScrapedJob] = asyncio.Queue(maxsize=max_size)

    async def put(self, job: ScrapedJob) -> None:
        """Enqueue a single scraped job."""
        await self._queue.put(job)

    async def get(self) -> ScrapedJob:
        """Dequeue next job (blocks until one is available)."""
        return await self._queue.get()

    async def put_batch(self, jobs: list[ScrapedJob]) -> int:
        """Enqueue multiple jobs. Returns the number actually enqueued."""
        count = 0
        for job in jobs:
            try:
                self._queue.put_nowait(job)
                count += 1
            except asyncio.QueueFull:
                logger.warning(
                    "Job queue full (%d), dropping remaining %d jobs",
                    self._queue.maxsize,
                    len(jobs) - count,
                )
                break
        return count

    def size(self) -> int:
        """Current number of items in the queue."""
        return self._queue.qsize()

    def is_empty(self) -> bool:
        """Whether the queue has no items."""
        return self._queue.empty()


# ---------------------------------------------------------------------------
# Queue consumer — pulls jobs and runs the preparation workflow
# ---------------------------------------------------------------------------


async def process_queue(
    queue: JobQueue,
    *,
    workflow: Any | None = None,
    master_cv_loader: Any | None = None,
    job_repository: Any | None = None,
    delay_between_jobs: float = 2.0,
    stop_event: asyncio.Event | None = None,
    on_job_processed: Any | None = None,
) -> int:
    """Consume jobs from *queue* and run the preparation workflow for each.

    Parameters
    ----------
    queue:
        The JobQueue to pull from.
    workflow:
        Compiled LangGraph workflow. If *None*, ``create_preparation_workflow()``
        is called (requires WeasyPrint system libs).
    master_cv_loader:
        Callable returning a master-CV dict. Defaults to ``load_master_cv``.
    job_repository:
        Optional repository for cross-cycle dedup. If provided, jobs that already
        exist in the repository are skipped.
    delay_between_jobs:
        Seconds to wait between successive workflow runs (avoids LLM rate-limits).
    stop_event:
        If provided, the consumer exits when this event is set and the queue is
        empty. Otherwise it runs until cancelled.

    Returns
    -------
    int
        Number of jobs successfully processed.
    """
    if workflow is None or master_cv_loader is None:
        from ..agents.preparation_workflow import (
            create_preparation_workflow,
            load_master_cv,
        )
        if workflow is None:
            workflow = create_preparation_workflow()
        if master_cv_loader is None:
            master_cv_loader = load_master_cv

    processed = 0

    while True:
        # Check stop condition
        if stop_event is not None and stop_event.is_set() and queue.is_empty():
            logger.info("Stop event set and queue empty — consumer exiting.")
            break

        # Try to get a job (with timeout so we can re-check stop_event)
        try:
            job = await asyncio.wait_for(queue.get(), timeout=1.0)
        except TimeoutError:
            continue

        logger.info("Processing queued job %s", job.job_id)

        # Cross-cycle dedup: skip jobs already in the repository
        if job_repository is not None:
            try:
                existing = await job_repository.get(job.job_id)
                if existing is not None:
                    logger.info("Skipping already-processed job %s (status: %s)", job.job_id, existing.status)
                    continue
            except Exception:
                logger.warning("Dedup check failed for job %s, proceeding with processing", job.job_id)

        try:
            master_cv = master_cv_loader()

            initial_state = {
                "job_id": job.job_id,
                "source": "linkedin",
                "mode": "full",
                "raw_input": job.model_dump(),
                "job_posting": {},
                "master_cv": master_cv,
                "tailored_cv_json": {},
                "tailored_cv_pdf_path": "",
                "user_feedback": None,
                "retry_count": 0,
                "current_step": "queued",
                "error_message": None,
            }

            config = {"configurable": {"thread_id": f"linkedin-{job.job_id}", "repository": job_repository}}
            result = await workflow.ainvoke(initial_state, config=config)
            logger.info("Workflow completed for job %s", job.job_id)
            processed += 1

            if on_job_processed is not None:
                try:
                    on_job_processed(job.job_id, config["configurable"]["thread_id"])
                except Exception:
                    logger.debug("on_job_processed callback failed for %s", job.job_id)

        except Exception:
            logger.exception("Workflow failed for queued job %s", job.job_id)

        if delay_between_jobs > 0:
            await asyncio.sleep(delay_between_jobs)

    return processed
