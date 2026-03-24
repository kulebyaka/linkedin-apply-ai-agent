"""Job Orchestrator - Domain service for job submission and status queries.

Extracted from api/main.py to keep API handlers thin. Handles:
- Job submission (URL/manual sources, MVP/full modes)
- Job status queries (dual-source: repository + workflow threads)
- Workflow dispatch (preparation workflow)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.context import AppContext

from src.models.state_machine import BusinessState
from src.models.unified import (
    JobStatusResponse,
    JobSubmitRequest,
    JobSubmitResponse,
)

logger = logging.getLogger(__name__)


class JobOrchestrator:
    """Orchestrates job submission and status tracking.

    Owns the business logic for:
    - Validating and processing job submission requests
    - Dispatching preparation workflows
    - Querying job status from repository and workflow threads
    """

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx

    async def submit_job(self, request: JobSubmitRequest) -> JobSubmitResponse:
        """Validate request, build workflow state, and dispatch preparation workflow.

        Returns:
            JobSubmitResponse with the new job_id.

        Raises:
            ValueError: If request validation fails.
        """
        # Validate request
        if request.source == "url" and not request.url:
            raise ValueError("URL is required for source='url'")
        if request.source == "manual" and not request.job_description:
            raise ValueError("job_description is required for source='manual'")

        job_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())

        # Build raw_input based on source
        if request.source == "url":
            raw_input: dict = {"url": request.url}
            if request.job_description:
                raw_input.update(
                    {
                        "title": request.job_description.title,
                        "company": request.job_description.company,
                        "description": request.job_description.description,
                        "requirements": request.job_description.requirements,
                    }
                )
        else:  # manual
            raw_input = {
                "title": request.job_description.title,
                "company": request.job_description.company,
                "description": request.job_description.description,
                "requirements": request.job_description.requirements,
                "template_name": request.job_description.template_name,
                "llm_provider": request.job_description.llm_provider,
                "llm_model": request.job_description.llm_model,
            }
            logger.info(
                "Job %s: template_name=%s, llm_provider=%s, llm_model=%s",
                job_id,
                request.job_description.template_name,
                request.job_description.llm_provider,
                request.job_description.llm_model,
            )

        # Load master CV
        from src.agents._shared import load_master_cv

        master_cv = load_master_cv()

        # Build initial state
        initial_state = {
            "job_id": job_id,
            "source": request.source,
            "mode": request.mode,
            "raw_input": raw_input,
            "master_cv": master_cv,
            "current_step": BusinessState.QUEUED,
            "retry_count": 0,
            "user_feedback": None,
            "error_message": None,
        }

        # Track thread
        await self._ctx.register_workflow(job_id, thread_id, "preparation")

        # Dispatch workflow in background
        self._ctx.create_background_task(
            self._run_preparation_workflow(job_id, thread_id, initial_state)
        )

        logger.info("Job %s submitted: source=%s, mode=%s", job_id, request.source, request.mode)

        return JobSubmitResponse(
            job_id=job_id,
            status=BusinessState.QUEUED,
            message=f"Job submitted successfully. Mode: {request.mode}",
        )

    async def get_status(self, job_id: str) -> JobStatusResponse:
        """Get job status from repository (authoritative) or workflow threads (in-progress).

        Raises:
            KeyError: If job_id not found in any source.
        """
        # Check repository first — authoritative for completed jobs
        job_record = await self._ctx.repository.get(job_id)
        if job_record:
            attempts = await self._ctx.repository.get_cv_attempts(job_id)
            return JobStatusResponse(
                job_id=job_id,
                status=job_record.status,
                source=job_record.source,
                mode=job_record.mode,
                job_posting=job_record.job_posting,
                cv_json=job_record.current_cv_json,
                pdf_path=job_record.current_pdf_path,
                retry_count=len(attempts),
                error_message=job_record.error_message,
                created_at=job_record.created_at,
                updated_at=job_record.updated_at,
            )

        # Fall back to workflow threads for in-progress jobs
        thread_info = await self._ctx.get_workflow_thread(job_id)
        if thread_info is not None:
            thread_id = thread_info["thread_id"]
            config = {"configurable": {"thread_id": thread_id}}

            if thread_info["workflow_type"] == "preparation":
                state_snapshot = self._ctx.prep_workflow.get_state(config)
            elif thread_info["workflow_type"] == "retry":
                state_snapshot = self._ctx.retry_workflow.get_state(config)
            else:
                raise RuntimeError(f"Unknown workflow type: {thread_info['workflow_type']}")

            state_values = state_snapshot.values

            return JobStatusResponse(
                job_id=job_id,
                status=state_values.get("current_step", BusinessState.QUEUED),
                source=state_values.get("source"),
                mode=state_values.get("mode"),
                job_posting=state_values.get("job_posting"),
                cv_json=state_values.get("tailored_cv_json"),
                pdf_path=state_values.get("tailored_cv_pdf_path"),
                retry_count=state_values.get("retry_count", 0),
                error_message=state_values.get("error_message"),
                created_at=thread_info["created_at"],
                updated_at=datetime.now(tz=timezone.utc),
            )

        raise KeyError(f"Job {job_id} not found")

    async def _run_preparation_workflow(
        self, job_id: str, thread_id: str, initial_state: dict
    ) -> None:
        """Execute preparation workflow asynchronously."""
        try:
            config = {
                "configurable": {
                    "thread_id": thread_id,
                    "repository": self._ctx.repository,
                }
            }
            result = await self._ctx.prep_workflow.ainvoke(initial_state, config)
            logger.info(
                "Preparation workflow for job %s completed: %s",
                job_id,
                result.get("current_step"),
            )
        except Exception as e:
            logger.error("Preparation workflow for job %s failed: %s", job_id, e, exc_info=True)
            try:
                await self._ctx.repository.update(
                    job_id, {"status": BusinessState.FAILED, "error_message": str(e)}
                )
            except Exception:
                logger.warning("Failed to mark job %s as FAILED in repository", job_id)
