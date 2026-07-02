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
    JobRecord,
    JobStatusResponse,
    JobSubmitRequest,
    JobSubmitResponse,
)
from src.models.user import UserModelPreferences

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

    async def submit_job(
        self,
        request: JobSubmitRequest,
        user_id: str,
        master_cv: dict,
        model_preferences: UserModelPreferences | None = None,
    ) -> JobSubmitResponse:
        """Validate request, build workflow state, and dispatch preparation workflow.

        Args:
            request: Job submission request.
            user_id: Authenticated user's ID.
            master_cv: Master CV JSON from user's DB record.

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

        # Resolve CV-generation model preference:
        # 1. Per-request override (JobDescriptionInput.llm_provider/llm_model)
        # 2. User's saved model_preferences.cv_generation
        # 3. Global .env default (None → resolved by create_llm_client())
        default_provider: str | None = None
        default_model: str | None = None
        if model_preferences and model_preferences.cv_generation:
            default_provider = model_preferences.cv_generation.provider
            default_model = model_preferences.cv_generation.model

        # Build raw_input based on source
        if request.source == "url":
            raw_input: dict = {
                "url": request.url,
                "llm_provider": default_provider,
                "llm_model": default_model,
            }
            if request.job_description:
                raw_input.update(
                    {
                        "title": request.job_description.title,
                        "company": request.job_description.company,
                        "description": request.job_description.description,
                        "requirements": request.job_description.requirements,
                    }
                )
                if request.job_description.llm_provider:
                    raw_input["llm_provider"] = request.job_description.llm_provider
                if request.job_description.llm_model:
                    raw_input["llm_model"] = request.job_description.llm_model
        else:  # manual
            raw_input = {
                "title": request.job_description.title,
                "company": request.job_description.company,
                "description": request.job_description.description,
                "requirements": request.job_description.requirements,
                "template_name": request.job_description.template_name,
                "llm_provider": request.job_description.llm_provider or default_provider,
                "llm_model": request.job_description.llm_model or default_model,
            }
            logger.info(
                "Job %s: template_name=%s, llm_provider=%s, llm_model=%s",
                job_id,
                request.job_description.template_name,
                raw_input["llm_provider"],
                raw_input["llm_model"],
            )

        # Build initial state
        initial_state = {
            "job_id": job_id,
            "user_id": user_id,
            "source": request.source,
            "mode": request.mode,
            "raw_input": raw_input,
            "master_cv": master_cv,
            "current_step": BusinessState.QUEUED,
            "retry_count": 0,
            "filter_result": None,
            "user_feedback": None,
            "error_message": None,
        }

        # Dispatch workflow in background via the shared WorkflowDispatcher.
        # Register synchronously so get_status() can find the job immediately
        # after submit_job returns; the dispatcher will re-register
        # (idempotent) and handle unregister + FAILED-recovery in the
        # background task.
        dispatcher = self._ctx.workflow_dispatcher
        if dispatcher is None:
            raise RuntimeError("workflow_dispatcher not initialized on AppContext")

        # Persist a QUEUED row before dispatching so the job survives a
        # restart and is visible in the UI immediately.
        now = datetime.now(tz=timezone.utc)
        job_posting_preview = self._build_job_posting_preview(request)
        try:
            await self._ctx.repository.create(
                JobRecord(
                    job_id=job_id,
                    user_id=user_id,
                    source=request.source,
                    mode=request.mode,
                    status=BusinessState.QUEUED,
                    job_posting=job_posting_preview,
                    raw_input=raw_input,
                    application_url=request.application_url or job_posting_preview.get("url"),
                    created_at=now,
                    updated_at=now,
                )
            )
        except Exception:
            logger.exception(
                "Failed to persist QUEUED row for job %s; aborting submission",
                job_id,
            )
            raise

        await self._ctx.register_workflow(
            job_id,
            thread_id,
            "preparation",
            user_id=user_id,
        )
        self._ctx.create_background_task(
            dispatcher.dispatch_preparation(
                job_id=job_id,
                thread_id=thread_id,
                initial_state=initial_state,
                user_id=user_id,
                create_failure_record=False,
            )
        )

        logger.info("Job %s submitted: source=%s, mode=%s", job_id, request.source, request.mode)

        return JobSubmitResponse(
            job_id=job_id,
            status=BusinessState.QUEUED,
            message=f"Job submitted successfully. Mode: {request.mode}",
        )

    async def proceed_filtered_out(
        self, job_id: str, user_id: str, override_reason: str | None = None
    ) -> JobSubmitResponse:
        """Override the job filter for a single filtered-out job.

        Re-enters the preparation workflow skipping extraction and filtering
        (the job_posting is already persisted), composes a tailored CV, and
        lands the job in ``pending_review`` for HITL review. The original
        ``filter_result`` is preserved so the reviewer sees why it was filtered.

        Args:
            job_id: The filtered-out job to proceed.
            user_id: Authenticated user's ID (for ownership verification).

        Returns:
            JobSubmitResponse with status PROCESSING.

        Raises:
            KeyError: If job not found or not owned by user.
            RuntimeError: If job is not in filtered_out status.
        """
        job_record = await self._ctx.repository.get_for_user(job_id, user_id)
        if job_record is None:
            raise KeyError(f"Job {job_id} not found")

        if job_record.status != BusinessState.FILTERED_OUT:
            raise RuntimeError(f"Job {job_id} is not filtered out (status: {job_record.status})")

        dispatcher = self._ctx.workflow_dispatcher
        if dispatcher is None:
            raise RuntimeError("workflow_dispatcher not initialized on AppContext")

        # FILTERED_OUT → PROCESSING; extract_job_node will keep it PROCESSING.
        # Capture a false-negative signal for the auto-refiner when the user
        # explains why the filter was wrong (only reasons become signals).
        proceed_updates: dict = {"status": BusinessState.PROCESSING}
        reason = (override_reason or "").strip()
        if reason:
            proceed_updates["override_reason"] = reason
            proceed_updates["refine_signal_state"] = "pending"
            logger.info("Captured override-with-reason refine signal for job %s", job_id)
        await self._ctx.repository.update(job_id, proceed_updates)

        try:
            thread_id = str(uuid.uuid4())

            # Load master CV and CV-generation model preference from user record.
            master_cv: dict | None = None
            cv_provider: str | None = None
            cv_model: str | None = None
            if self._ctx.user_repository:
                user = await self._ctx.user_repository.get_by_id(user_id)
                if user and user.master_cv_json:
                    master_cv = user.master_cv_json
                if user and user.model_preferences and user.model_preferences.cv_generation:
                    cv_provider = user.model_preferences.cv_generation.provider
                    cv_model = user.model_preferences.cv_generation.model
            if not master_cv:
                from src.agents._shared import load_master_cv

                master_cv = load_master_cv()

            raw_input = dict(job_record.raw_input or {})
            if cv_provider:
                raw_input["llm_provider"] = cv_provider
            if cv_model:
                raw_input["llm_model"] = cv_model

            initial_state = {
                "job_id": job_id,
                "user_id": user_id,
                "source": job_record.source,
                # Force full mode so the override always enters HITL review.
                "mode": "full",
                "raw_input": raw_input,
                "job_posting": job_record.job_posting,
                # Preserve the original verdict — not re-evaluated.
                "filter_result": job_record.filter_result,
                "master_cv": master_cv,
                "skip_filter": True,
                "current_step": BusinessState.QUEUED,
                "retry_count": 0,
                "user_feedback": None,
                "error_message": None,
            }

            await self._ctx.register_workflow(
                job_id,
                thread_id,
                "preparation",
                user_id=user_id,
            )
            self._ctx.create_background_task(
                dispatcher.dispatch_preparation(
                    job_id=job_id,
                    thread_id=thread_id,
                    initial_state=initial_state,
                    user_id=user_id,
                    create_failure_record=False,
                )
            )
        except Exception:
            # Dispatch setup failed — roll back to filtered_out so the button reappears.
            logger.error(
                "Failed to dispatch proceed-anyway workflow for job %s, rolling back to filtered_out",
                job_id,
                exc_info=True,
            )
            try:
                await self._ctx.repository.update(job_id, {"status": BusinessState.FILTERED_OUT})
            except Exception:
                logger.error("Failed to roll back job %s to filtered_out", job_id)
            raise

        logger.info("Job %s proceeded past filter (proceed anyway)", job_id)

        return JobSubmitResponse(
            job_id=job_id,
            status=BusinessState.PROCESSING,
            message="CV generation started — the job will appear in your review queue.",
        )

    @staticmethod
    def _build_job_posting_preview(request: JobSubmitRequest) -> dict:
        """Return a minimal job_posting dict for the QUEUED row.

        Manual submissions already carry title/company/description; URL
        submissions usually only have the URL until extract_job_node runs.
        """
        if request.job_description is not None:
            return {
                "title": request.job_description.title,
                "company": request.job_description.company,
                "url": request.url or "",
            }
        return {"url": request.url or ""}

    async def list_jobs(
        self,
        user_id: str,
        *,
        statuses: list[str] | None = None,
        sources: list[str] | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[JobRecord], int]:
        """List jobs owned by ``user_id`` with optional filters.

        Scopes every query to the caller by passing ``user_ids=[user_id]`` to
        the repository's admin-style list/count helpers.

        Returns:
            ``(items, total)`` where ``total`` is the filtered count ignoring
            pagination.
        """
        items = await self._ctx.repository.list_all_jobs(
            user_ids=[user_id],
            statuses=statuses,
            sources=sources,
            created_from=created_from,
            created_to=created_to,
            search=search,
            limit=limit,
            offset=offset,
        )
        total = await self._ctx.repository.count_all_jobs(
            user_ids=[user_id],
            statuses=statuses,
            sources=sources,
            created_from=created_from,
            created_to=created_to,
            search=search,
        )
        return items, total

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
                attempt_count=len(attempts),
                error_message=job_record.error_message,
                pending_questions=job_record.pending_questions,
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
                attempt_count=state_values.get("retry_count", 0),
                error_message=state_values.get("error_message"),
                created_at=thread_info["created_at"],
                updated_at=datetime.now(tz=timezone.utc),
            )

        raise KeyError(f"Job {job_id} not found")
