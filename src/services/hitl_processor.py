"""HITL Processor - Domain service for Human-in-the-Loop decisions.

Extracted from api/main.py to keep API handlers thin. Handles:
- Processing HITL decisions (approve/decline/retry)
- Retrieving pending jobs for review
- Retrieving application history
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.context import AppContext

from src.models.state_machine import BusinessState
from src.models.unified import (
    ApplicationHistoryItem,
    HITLDecision,
    HITLDecisionResponse,
    PendingApproval,
)

logger = logging.getLogger(__name__)


class HITLProcessor:
    """Processes Human-in-the-Loop review decisions.

    Owns the business logic for:
    - Validating and applying HITL decisions (approve/decline/retry)
    - Querying pending jobs for review
    - Querying application history

    Uses per-job locks to prevent concurrent decisions on the same job
    (e.g., two retry requests racing past the PENDING_REVIEW check).
    """

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._job_locks: dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()

    async def _get_job_lock(self, job_id: str) -> asyncio.Lock:
        """Get or create a per-job lock for serializing decisions.

        Callers must verify the job exists before calling this method
        to prevent unbounded lock growth from nonexistent job IDs.

        Locks are retained for the lifetime of this processor instance.
        The number of locks is bounded by the number of jobs in the
        repository. Eager cleanup was removed because asyncio.Lock.release()
        schedules waiters rather than running them synchronously, creating
        a window where a released-but-not-yet-reacquired lock appears
        unlocked and gets deleted, allowing concurrent decisions on the
        same job.
        """
        async with self._locks_lock:
            if job_id not in self._job_locks:
                self._job_locks[job_id] = asyncio.Lock()
            return self._job_locks[job_id]

    async def process_decision(
        self, job_id: str, decision: HITLDecision, user_id: str
    ) -> HITLDecisionResponse:
        """Process a HITL decision for a pending job.

        Uses a per-job lock to prevent concurrent decisions on the same job.

        Args:
            job_id: The job to decide on.
            decision: The user's decision (approve/decline/retry).
            user_id: Authenticated user's ID (for ownership verification).

        Returns:
            HITLDecisionResponse with the new status.

        Raises:
            ValueError: If validation fails (e.g., retry without feedback).
            KeyError: If job not found or not owned by user.
            RuntimeError: If job is not in pending status.
        """
        # Validate retry has feedback (before acquiring lock)
        if decision.decision == "retry" and not decision.feedback:
            raise ValueError("Feedback is required for retry decision")

        # Check job exists and belongs to user before creating a per-job lock
        job_record = await self._ctx.repository.get_for_user(job_id, user_id)
        if job_record is None:
            raise KeyError(f"Job {job_id} not found")

        # Serialize decisions per job to prevent race conditions
        lock = await self._get_job_lock(job_id)
        async with lock:
            # Re-read under lock to ensure consistent state
            job_record = await self._ctx.repository.get_for_user(job_id, user_id)
            if job_record is None:
                raise KeyError(f"Job {job_id} not found")

            if job_record.status != BusinessState.PENDING_REVIEW:
                raise RuntimeError(
                    f"Job {job_id} is not pending review (status: {job_record.status})"
                )

            if decision.decision == "approved":
                return await self._handle_approve(job_id)
            elif decision.decision == "declined":
                return await self._handle_decline(job_id)
            elif decision.decision == "retry":
                return await self._handle_retry(
                    job_id, job_record, decision.feedback, user_id
                )
            else:
                raise ValueError(f"Invalid decision: {decision.decision}")

    async def get_pending(self, user_id: str) -> list[PendingApproval]:
        """Get all jobs pending HITL review for a user."""
        try:
            pending_jobs = await self._ctx.repository.get_pending(user_id)
            result = []
            for job in pending_jobs:
                attempts = await self._ctx.repository.get_cv_attempts(job.job_id)
                result.append(
                    PendingApproval(
                        job_id=job.job_id,
                        job_posting=job.job_posting or {},
                        cv_json=job.current_cv_json or {},
                        pdf_path=job.current_pdf_path,
                        attempt_count=len(attempts),
                        created_at=job.created_at,
                        source=job.source,
                        application_url=job.application_url
                        or (job.job_posting or {}).get("url"),
                    )
                )
            return result
        except Exception:
            logger.exception("Failed to get pending jobs from repository")
            raise

    async def get_history(
        self, user_id: str, limit: int = 50, status: str | None = None
    ) -> list[ApplicationHistoryItem]:
        """Get application history for a user with optional status filter."""
        try:
            statuses = [status] if status else None
            jobs = await self._ctx.repository.get_history(
                user_id=user_id, limit=limit, statuses=statuses
            )
            return [
                ApplicationHistoryItem(
                    job_id=job.job_id,
                    job_title=job.job_posting.get("title")
                    if job.job_posting
                    else None,
                    company=job.job_posting.get("company")
                    if job.job_posting
                    else None,
                    status=job.status,
                    created_at=job.created_at,
                )
                for job in jobs
            ]
        except Exception:
            logger.exception("Failed to get history from repository")
            raise

    # =========================================================================
    # Private helpers
    # =========================================================================

    async def _handle_approve(self, job_id: str) -> HITLDecisionResponse:
        logger.info("Job %s approved for application (workflow not implemented)", job_id)
        await self._ctx.repository.update(job_id, {"status": BusinessState.APPROVED})

        return HITLDecisionResponse(
            job_id=job_id,
            status=BusinessState.APPROVED,
            message="Job approved. Application workflow not yet implemented.",
        )

    async def _handle_decline(self, job_id: str) -> HITLDecisionResponse:
        logger.info("Job %s declined by user", job_id)
        await self._ctx.repository.update(job_id, {"status": BusinessState.DECLINED})

        return HITLDecisionResponse(
            job_id=job_id,
            status=BusinessState.DECLINED,
            message="Job declined. No further action will be taken.",
        )

    async def _handle_retry(
        self, job_id: str, job_record, feedback: str, user_id: str
    ) -> HITLDecisionResponse:
        logger.info("Job %s queued for retry with user feedback", job_id)

        await self._ctx.repository.update(job_id, {"status": BusinessState.RETRYING})

        try:
            retry_thread_id = str(uuid.uuid4())

            # Load master CV from user's DB record
            master_cv = None
            if self._ctx.user_repository:
                user = await self._ctx.user_repository.get_by_id(user_id)
                if user and user.master_cv_json:
                    master_cv = user.master_cv_json
            if not master_cv:
                from src.agents._shared import load_master_cv
                master_cv = load_master_cv()

            # Derive retry count from CV attempts
            attempts = await self._ctx.repository.get_cv_attempts(job_id)
            retry_count = len(attempts)

            retry_state = {
                "job_id": job_id,
                "user_id": user_id,
                "user_feedback": feedback,
                "job_posting": job_record.job_posting,
                "master_cv": master_cv,
                "retry_count": retry_count,
                "current_step": BusinessState.QUEUED,
                "error_message": None,
            }

            await self._ctx.register_workflow(job_id, retry_thread_id, "retry", user_id=user_id)

            self._ctx.create_background_task(
                self._run_retry_workflow(job_id, retry_thread_id, retry_state)
            )
        except Exception as e:
            # Rollback status to pending_review so the job isn't stuck in retrying
            logger.error(
                "Failed to dispatch retry workflow for job %s, rolling back to pending: %s",
                job_id, e, exc_info=True,
            )
            try:
                await self._ctx.repository.update(
                    job_id, {"status": BusinessState.PENDING_REVIEW}
                )
            except Exception:
                # Rollback to pending failed; try marking as failed so the job
                # isn't stuck in RETRYING with no workflow running.
                logger.error(
                    "Failed to rollback job %s to pending_review, attempting FAILED",
                    job_id,
                )
                try:
                    await self._ctx.repository.update(
                        job_id, {"status": BusinessState.FAILED}
                    )
                except Exception:
                    logger.error(
                        "Job %s is stuck in RETRYING — both rollback and fail transition failed",
                        job_id,
                    )
            raise

        return HITLDecisionResponse(
            job_id=job_id,
            status=BusinessState.RETRYING,
            message="CV regeneration started with your feedback.",
        )

    async def _run_retry_workflow(
        self, job_id: str, thread_id: str, initial_state: dict
    ) -> None:
        """Execute retry workflow asynchronously."""
        try:
            config = {
                "configurable": {
                    "thread_id": thread_id,
                    "repository": self._ctx.repository,
                }
            }
            result = await self._ctx.retry_workflow.ainvoke(initial_state, config)
            logger.info(
                "Retry workflow for job %s completed: %s",
                job_id,
                result.get("current_step"),
            )
        except Exception as e:
            logger.error(
                "Retry workflow for job %s failed: %s", job_id, e, exc_info=True
            )
            try:
                await self._ctx.repository.update(
                    job_id, {"status": BusinessState.FAILED, "error_message": str(e)}
                )
            except Exception:
                logger.warning("Failed to mark job %s as FAILED in repository", job_id)

