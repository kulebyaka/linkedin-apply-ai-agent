"""Async job queue for feeding scraped LinkedIn jobs into the preparation workflow.

Provides a thin wrapper around asyncio.Queue with batch operations and a
singleton accessor so other modules can share a single queue instance.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class JobQueue:
    """Async queue for scraped job dicts awaiting workflow processing."""

    def __init__(self, max_size: int = 100) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_size)

    async def put(self, job_data: dict) -> None:
        """Enqueue a single scraped job dict."""
        await self._queue.put(job_data)

    async def get(self) -> dict:
        """Dequeue next job dict (blocks until one is available)."""
        return await self._queue.get()

    async def put_batch(self, jobs: list[dict]) -> int:
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
# Module-level singleton
# ---------------------------------------------------------------------------

_job_queue: JobQueue | None = None


def get_job_queue() -> JobQueue:
    """Return the global JobQueue singleton, creating it if needed."""
    global _job_queue
    if _job_queue is None:
        _job_queue = JobQueue()
    return _job_queue


def set_job_queue(queue: JobQueue) -> None:
    """Replace the global JobQueue singleton (useful for testing)."""
    global _job_queue
    _job_queue = queue


# ---------------------------------------------------------------------------
# Queue consumer — pulls jobs and runs the preparation workflow
# ---------------------------------------------------------------------------


async def process_queue(
    queue: JobQueue,
    *,
    workflow: Any | None = None,
    master_cv_loader: Any | None = None,
    delay_between_jobs: float = 2.0,
    stop_event: asyncio.Event | None = None,
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
            job_data = await asyncio.wait_for(queue.get(), timeout=1.0)
        except TimeoutError:
            continue

        job_id = job_data.get("job_id") or job_data.get("id") or "unknown"
        logger.info("Processing queued job %s", job_id)

        try:
            master_cv = master_cv_loader()

            initial_state = {
                "job_id": job_id,
                "source": "linkedin",
                "mode": "full",
                "raw_input": job_data,
                "job_posting": {},
                "master_cv": master_cv,
                "tailored_cv_json": {},
                "tailored_cv_pdf_path": "",
                "user_feedback": None,
                "retry_count": 0,
                "current_step": "queued",
                "error_message": None,
            }

            config = {"configurable": {"thread_id": f"linkedin-{job_id}"}}
            result = await asyncio.to_thread(
                lambda s=initial_state, c=config: list(workflow.stream(s, config=c))
            )
            logger.info("Workflow completed for job %s (%d steps)", job_id, len(result))
            processed += 1

        except Exception:
            logger.exception("Workflow failed for queued job %s", job_id)

        if delay_between_jobs > 0:
            await asyncio.sleep(delay_between_jobs)

    return processed
