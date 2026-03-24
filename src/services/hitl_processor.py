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

from src.models.unified import (
    ApplicationHistoryItem,
    HITLDecision,
    HITLDecisionResponse,
    PendingApproval,
)
from src.services.job_repository import RepositoryError

logger = logging.getLogger(__name__)


class HITLProcessor:
    """Processes Human-in-the-Loop review decisions.

    Owns the business logic for:
    - Validating and applying HITL decisions (approve/decline/retry)
    - Querying pending jobs for review
    - Querying application history
    """

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx

    async def process_decision(
        self, job_id: str, decision: HITLDecision
    ) -> HITLDecisionResponse:
        """Process a HITL decision for a pending job.

        Args:
            job_id: The job to decide on.
            decision: The user's decision (approve/decline/retry).

        Returns:
            HITLDecisionResponse with the new status.

        Raises:
            ValueError: If validation fails (e.g., retry without feedback).
            KeyError: If job not found.
            RuntimeError: If job is not in pending status.
        """
        # Validate retry has feedback
        if decision.decision == "retry" and not decision.feedback:
            raise ValueError("Feedback is required for retry decision")

        # Get current job state from repository
        job_record = await self._ctx.repository.get(job_id)
        if job_record is None:
            raise KeyError(f"Job {job_id} not found")

        if job_record.status != "pending":
            raise RuntimeError(
                f"Job {job_id} is not pending review (status: {job_record.status})"
            )

        if decision.decision == "approved":
            return await self._handle_approve(job_id)
        elif decision.decision == "declined":
            return await self._handle_decline(job_id)
        elif decision.decision == "retry":
            return await self._handle_retry(job_id, job_record, decision.feedback)
        else:
            raise ValueError(f"Invalid decision: {decision.decision}")

    async def get_pending(self) -> list[PendingApproval]:
        """Get all jobs pending HITL review.

        Falls back to scanning workflow threads if repository raises NotImplementedError.
        """
        try:
            pending_jobs = await self._ctx.repository.get_pending()
            return [
                PendingApproval(
                    job_id=job.job_id,
                    job_posting=job.job_posting or {},
                    cv_json=job.cv_json or {},
                    pdf_path=job.pdf_path,
                    retry_count=job.retry_count,
                    created_at=job.created_at,
                    source=job.source,
                    application_url=job.application_url
                    or (job.job_posting or {}).get("url"),
                )
                for job in pending_jobs
            ]
        except NotImplementedError:
            logger.warning(
                "Repository not implemented, scanning workflow states for pending jobs"
            )
            return await self._get_pending_from_threads()

    async def get_history(
        self, limit: int = 50, status: str | None = None
    ) -> list[ApplicationHistoryItem]:
        """Get application history with optional status filter."""
        try:
            statuses = [status] if status else None
            jobs = await self._ctx.repository.get_history(
                limit=limit, statuses=statuses
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
                    applied_at=job.updated_at if job.status == "applied" else None,
                    created_at=job.created_at,
                )
                for job in jobs
            ]
        except NotImplementedError:
            logger.warning("Repository not implemented, returning empty history")
            return []

    # =========================================================================
    # Private helpers
    # =========================================================================

    async def _handle_approve(self, job_id: str) -> HITLDecisionResponse:
        logger.info("Job %s approved for application (workflow not implemented)", job_id)
        try:
            await self._ctx.repository.update(job_id, {"status": "approved"})
        except RepositoryError as e:
            logger.warning("Failed to update repository for job %s: %s", job_id, e)

        return HITLDecisionResponse(
            job_id=job_id,
            status="approved",
            message="Job approved. Application workflow not yet implemented.",
        )

    async def _handle_decline(self, job_id: str) -> HITLDecisionResponse:
        logger.info("Job %s declined by user", job_id)
        try:
            await self._ctx.repository.update(job_id, {"status": "declined"})
        except RepositoryError as e:
            logger.warning("Failed to update repository for job %s: %s", job_id, e)

        return HITLDecisionResponse(
            job_id=job_id,
            status="declined",
            message="Job declined. No further action will be taken.",
        )

    async def _handle_retry(
        self, job_id: str, job_record, feedback: str
    ) -> HITLDecisionResponse:
        logger.info("Job %s queued for retry with user feedback", job_id)

        try:
            await self._ctx.repository.update(job_id, {"status": "retrying"})
        except RepositoryError as e:
            logger.warning("Failed to update repository for job %s: %s", job_id, e)

        retry_thread_id = str(uuid.uuid4())

        from src.agents.preparation_workflow import load_master_cv

        retry_state = {
            "job_id": job_id,
            "user_feedback": feedback,
            "job_posting": job_record.job_posting,
            "master_cv": load_master_cv(),
            "retry_count": job_record.retry_count,
            "current_step": "queued",
            "error_message": None,
        }

        await self._ctx.register_workflow(job_id, retry_thread_id, "retry")

        asyncio.create_task(
            self._run_retry_workflow(job_id, retry_thread_id, retry_state)
        )

        return HITLDecisionResponse(
            job_id=job_id,
            status="retrying",
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

    async def _get_pending_from_threads(self) -> list[PendingApproval]:
        """Fallback: scan workflow threads for pending jobs."""
        pending = []
        all_threads = await self._ctx.get_all_workflow_threads()

        for job_id, thread_info in all_threads.items():
            if thread_info["workflow_type"] != "preparation":
                continue
            thread_id = thread_info["thread_id"]
            config = {"configurable": {"thread_id": thread_id}}
            try:
                state_snapshot = self._ctx.prep_workflow.get_state(config)
                state_values = state_snapshot.values
                if state_values.get("current_step") == "pending":
                    pending.append(
                        PendingApproval(
                            job_id=job_id,
                            job_posting=state_values.get("job_posting", {}),
                            cv_json=state_values.get("tailored_cv_json", {}),
                            pdf_path=state_values.get("tailored_cv_pdf_path"),
                            retry_count=state_values.get("retry_count", 0),
                            created_at=thread_info["created_at"],
                            source=state_values.get("source", "manual"),
                            application_url=state_values.get("job_posting", {}).get(
                                "url"
                            ),
                        )
                    )
            except Exception as e:
                logger.warning("Failed to get state for job %s: %s", job_id, e)

        return pending
