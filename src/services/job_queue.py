"""Async job queue for feeding scraped LinkedIn jobs into the preparation workflow.

Provides a thin wrapper around asyncio.Queue with batch operations,
a ConsumerManager for resilient consumer lifecycle management, and
a process_queue consumer function.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.models.job import ScrapedJob
from src.models.state_machine import ALLOWED_TRANSITIONS, BusinessState
from src.models.unified import JobRecord

if TYPE_CHECKING:
    from src.context import AppContext

logger = logging.getLogger(__name__)


def _scoped_job_id(raw_job_id: str, user_id: str | None) -> str:
    """Return a user-scoped job_id for multi-user isolation.

    For LinkedIn jobs the raw job_id is the global LinkedIn posting ID.
    Without scoping, two users who find the same posting would collide on
    the primary key.  When a user_id is present we append it in full to
    form a deterministic composite key (the job_id column is VARCHAR(80)
    to accommodate this).
    """
    if user_id:
        return f"{raw_job_id}:{user_id}"
    return raw_job_id


@dataclass
class QueueItem:
    """A scraped job with its owning user context."""

    job: ScrapedJob
    user_id: str | None = None


class JobQueue:
    """Async queue for ScrapedJob instances awaiting workflow processing."""

    def __init__(self, max_size: int = 100) -> None:
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=max_size)

    async def put(self, job: ScrapedJob, *, user_id: str | None = None) -> None:
        """Enqueue a single scraped job."""
        await self._queue.put(QueueItem(job=job, user_id=user_id))

    async def get(self) -> QueueItem:
        """Dequeue next item (blocks until one is available)."""
        return await self._queue.get()

    async def put_batch(
        self, jobs: list[ScrapedJob], *, user_id: str | None = None
    ) -> int:
        """Enqueue multiple jobs. Returns the number actually enqueued."""
        count = 0
        for job in jobs:
            try:
                self._queue.put_nowait(QueueItem(job=job, user_id=user_id))
                count += 1
            except asyncio.QueueFull:
                dropped = jobs[count:]
                dropped_details = ", ".join(
                    f"{j.job_id} ({j.title or 'untitled'})" for j in dropped
                )
                logger.warning(
                    "Job queue full (%d), dropping %d jobs: %s",
                    self._queue.maxsize,
                    len(dropped),
                    dropped_details,
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
    user_repository: Any | None = None,
    job_repository: Any,
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
        Callable returning a master-CV dict. Used as fallback when no user_id
        is attached to the queue item.
    user_repository:
        UserRepository for loading per-user master CVs. When a queue item
        has a user_id, the master CV is loaded from the user's DB record.
    job_repository:
        Repository for job persistence and cross-cycle dedup. Required because
        workflow nodes depend on it for state persistence.
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
    if job_repository is None:
        raise ValueError(
            "job_repository is required: workflow nodes depend on it for state persistence"
        )
    if workflow is None or master_cv_loader is None:
        from ..agents._shared import load_master_cv
        from ..agents.preparation_workflow import create_preparation_workflow
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

        # Try to get a queue item (with timeout so we can re-check stop_event)
        try:
            item = await asyncio.wait_for(queue.get(), timeout=1.0)
        except TimeoutError:
            continue

        job = item.job
        user_id = item.user_id

        # Build a user-scoped job_id so the same LinkedIn posting can exist
        # independently for different users.  The original LinkedIn ID is
        # preserved in raw_input for reference.
        scoped_job_id = _scoped_job_id(job.job_id, user_id)

        logger.info("Processing queued job %s (user=%s)", scoped_job_id, user_id or "global")

        # Cross-cycle dedup: skip jobs already in the repository
        if job_repository is not None:
            try:
                existing = await job_repository.get(scoped_job_id)
                if existing is not None:
                    logger.info("Skipping already-processed job %s (status: %s)", scoped_job_id, existing.status)
                    continue
            except Exception:
                logger.warning("Dedup check failed for job %s, proceeding with processing", scoped_job_id)

        try:
            # Load master CV: from user's DB record if user_id is present, else fallback
            master_cv = None
            if user_id and user_repository:
                try:
                    user = await user_repository.get_by_id(user_id)
                    if user and user.master_cv_json:
                        master_cv = user.master_cv_json
                except Exception:
                    logger.warning("Failed to load master CV for user %s, using fallback", user_id)

            if master_cv is None:
                master_cv = master_cv_loader()

            initial_state = {
                "job_id": scoped_job_id,
                "source": "linkedin",
                "mode": "full",
                "raw_input": job.model_dump(),
                "job_posting": {},
                "master_cv": master_cv,
                "tailored_cv_json": {},
                "tailored_cv_pdf_path": "",
                "user_feedback": None,
                "retry_count": 0,
                "current_step": BusinessState.QUEUED,
                "error_message": None,
                "user_id": user_id or "",
            }

            config = {"configurable": {"thread_id": f"linkedin-{scoped_job_id}", "repository": job_repository, "user_repository": user_repository}}
            await workflow.ainvoke(initial_state, config=config)
            logger.info("Workflow completed for job %s", scoped_job_id)
            processed += 1

            if on_job_processed is not None:
                try:
                    on_job_processed(scoped_job_id, config["configurable"]["thread_id"], user_id or "")
                except Exception:
                    logger.warning("on_job_processed callback failed for %s", scoped_job_id, exc_info=True)

        except Exception:
            logger.exception("Workflow failed for queued job %s", job.job_id)
            # Persist a failure record so the failure is traceable
            try:
                existing = await job_repository.get(scoped_job_id)
                if existing is not None:
                    if BusinessState.FAILED in ALLOWED_TRANSITIONS.get(existing.status, set()):
                        await job_repository.update(
                            scoped_job_id,
                            {"status": BusinessState.FAILED, "error_message": "Workflow failed unexpectedly"},
                        )
                else:
                    now = datetime.now(tz=timezone.utc)
                    await job_repository.create(
                        JobRecord(
                            job_id=scoped_job_id,
                            user_id=user_id or "",
                            source="linkedin",
                            mode="full",
                            status=BusinessState.FAILED,
                            raw_input=job.model_dump(),
                            error_message="Workflow failed unexpectedly",
                            created_at=now,
                            updated_at=now,
                        )
                    )
            except Exception:
                logger.warning("Failed to persist failure record for job %s", scoped_job_id, exc_info=True)

        if delay_between_jobs > 0:
            await asyncio.sleep(delay_between_jobs)

    return processed


# ---------------------------------------------------------------------------
# ConsumerManager — resilient consumer lifecycle with restart and health
# ---------------------------------------------------------------------------


class ConsumerManager:
    """Manages the queue consumer task with automatic restart on failure.

    Tracks restart count, applies exponential backoff, and exposes health
    status so monitoring (e.g. /api/health) can detect silent death.
    """

    def __init__(
        self,
        *,
        max_restarts: int = 5,
        backoff_base: float = 2.0,
        stable_threshold: float = 60.0,
    ) -> None:
        self.max_restarts = max_restarts
        self.backoff_base = backoff_base
        self.stable_threshold = stable_threshold

        self.restart_count: int = 0
        self.is_healthy: bool = True
        self._task: asyncio.Task | None = None
        self._restart_handle: asyncio.TimerHandle | None = None
        self._shutting_down: bool = False
        self._ctx: AppContext | None = None
        self._on_job_processed: Any | None = None

    def start(
        self,
        ctx: AppContext,
        *,
        on_job_processed: Any | None = None,
    ) -> asyncio.Task:
        """Start (or restart) the queue consumer task."""
        self._ctx = ctx
        self._on_job_processed = on_job_processed
        self._shutting_down = False
        self._task = self._create_consumer_task(ctx)
        return self._task

    def stop(self) -> None:
        """Signal shutdown and cancel any pending restart."""
        self._shutting_down = True
        if self._restart_handle is not None:
            self._restart_handle.cancel()
            self._restart_handle = None
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def wait_stopped(self) -> None:
        """Await cancellation of the consumer task (call after stop())."""
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def reset(self) -> None:
        """Reset restart counter and health flag (e.g. for manual restart)."""
        self.restart_count = 0
        self.is_healthy = True
        if self._restart_handle is not None:
            self._restart_handle.cancel()
            self._restart_handle = None

    def health_check(self) -> dict:
        """Return consumer health status for monitoring endpoints."""
        return {
            "queue_consumer_healthy": self.is_healthy,
            "consumer_restart_count": self.restart_count,
            "consumer_max_restarts": self.max_restarts,
            "consumer_running": self._task is not None and not self._task.done(),
        }

    @property
    def task(self) -> asyncio.Task | None:
        return self._task

    def _create_consumer_task(self, ctx: AppContext) -> asyncio.Task:
        started_at = time.monotonic()

        def _on_done(task: asyncio.Task) -> None:
            if task.cancelled() or self._shutting_down:
                return
            exc = task.exception()
            if exc is not None:
                uptime = time.monotonic() - started_at
                if uptime >= self.stable_threshold:
                    self.restart_count = 0

                self.restart_count += 1
                if self.restart_count > self.max_restarts:
                    self.is_healthy = False
                    logger.critical(
                        "Queue consumer crashed %d times (max %d), giving up: %s. "
                        "To restart manually, call POST /api/jobs/linkedin-search "
                        "or restart the server.",
                        self.restart_count,
                        self.max_restarts,
                        exc,
                    )
                    return
                delay = self.backoff_base * (2 ** (self.restart_count - 1))
                logger.error(
                    "Queue consumer crashed (%d/%d): %s — restarting in %.1fs",
                    self.restart_count, self.max_restarts, exc, delay,
                )
                loop = asyncio.get_running_loop()
                if self._restart_handle is not None:
                    self._restart_handle.cancel()
                self._restart_handle = loop.call_later(
                    delay, self._do_restart, ctx,
                )
            else:
                self.restart_count = 0

        def _register_linkedin_job(job_id: str, thread_id: str, user_id: str = "") -> None:
            ctx.create_background_task(
                ctx.register_workflow(job_id, thread_id, "preparation", user_id=user_id)
            )

        from ..agents._shared import load_master_cv

        task = asyncio.create_task(
            process_queue(
                ctx.job_queue,
                workflow=ctx.prep_workflow,
                master_cv_loader=load_master_cv,
                user_repository=ctx.user_repository,
                job_repository=ctx.repository,
                delay_between_jobs=2.0,
                on_job_processed=self._on_job_processed or _register_linkedin_job,
            )
        )
        task.add_done_callback(_on_done)
        return task

    def _do_restart(self, ctx: AppContext) -> None:
        self._restart_handle = None
        if self._shutting_down:
            return
        if self._task is not None and not self._task.done():
            logger.info("Consumer already running, skipping restart")
            self.restart_count = 0
            return
        self._task = self._create_consumer_task(ctx)
