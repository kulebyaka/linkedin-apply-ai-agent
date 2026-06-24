"""Job lifecycle endpoints: submission, status, PDF/HTML download, LinkedIn search, cleanup."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from src.api.deps import (
    CurrentUser,
    get_ctx,
    get_orchestrator,
    normalize_query_datetime,
)
from src.config.settings import get_settings
from src.models.state_machine import BusinessState, WorkflowStep
from src.models.unified import (
    JobStatusResponse,
    JobSubmitRequest,
    JobSubmitResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.options("/api/jobs/submit")
async def submit_job_options():
    """Handle CORS preflight for job submission."""
    return {}


@router.post("/api/jobs/submit", response_model=JobSubmitResponse)
async def submit_job(
    job_request: JobSubmitRequest, http_request: Request, user: CurrentUser
) -> JobSubmitResponse:
    """Submit a job for CV generation."""
    try:
        master_cv = user.master_cv_json
        if not master_cv:
            from src.agents._shared import load_master_cv
            master_cv = load_master_cv()

        orchestrator = get_orchestrator(http_request)
        return await orchestrator.submit_job(
            job_request, user.id, master_cv, model_preferences=user.model_preferences
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        logger.error(f"Failed to submit job: {e}", exc_info=True)
        raise HTTPException(500, "Failed to submit job") from None


# LinkedIn search routes must precede /api/jobs/{job_id}/* wildcards.


@router.post("/api/jobs/linkedin-search")
async def trigger_linkedin_search(request: Request, user: CurrentUser):
    """Trigger a LinkedIn search manually. Runs search in background."""
    settings = get_settings()
    ctx = get_ctx(request)

    if settings.seed_jobs_from_file:
        raise HTTPException(
            409,
            "LinkedIn scraping is disabled in fixture replay mode "
            "(SEED_JOBS_FROM_FILE=true). Use POST /api/jobs/replay-fixtures instead.",
        )

    async with ctx.linkedin_init_lock:
        # Drop a dead browser before reusing it.
        if ctx.browser is not None and not ctx.browser.is_alive():
            logger.warning("Cached LinkedIn browser context is dead, reinitializing")
            try:
                await ctx.browser.close()
            except Exception:
                logger.debug("Error closing dead browser", exc_info=True)
            ctx.browser = None
            ctx.scheduler = None

        if ctx.scheduler is None:
            try:
                from src.services.jobs.scheduler import LinkedInSearchScheduler
                from src.services.linkedin.browser_automation import LinkedInAutomation
                from src.services.linkedin.linkedin_scraper import LinkedInJobScraper

                if ctx.browser is None:
                    browser = LinkedInAutomation(settings)
                    await browser.initialize()
                    ctx.browser = browser

                scraper = LinkedInJobScraper(ctx.browser, settings)
                ctx.scheduler = LinkedInSearchScheduler(
                    settings, scraper, ctx.job_queue,
                    user_repository=ctx.user_repository,
                    admin_alert_service=ctx.admin_alert_service,
                    job_repository=ctx.repository,
                )
            except Exception:
                logger.exception("Failed to initialize LinkedIn search components")
                raise HTTPException(500, "Failed to initialize LinkedIn search components") from None

        cm = ctx.consumer_manager
        if cm is not None and (cm.task is None or cm.task.done()):
            cm.reset()
            cm.start(ctx)

    requesting_user_id = user.id

    async def _run_search():
        try:
            count = await ctx.scheduler.run_search(user_id=requesting_user_id)
            logger.info("Manual LinkedIn search completed: %d jobs found", count)
        except Exception:
            logger.exception("Manual LinkedIn search failed")

    ctx.create_background_task(_run_search())

    return {"status": "started", "message": "LinkedIn search triggered"}


@router.get("/api/jobs/linkedin-search/status")
async def get_linkedin_search_status(request: Request, user: CurrentUser):
    """Return current scheduler state."""
    settings = get_settings()
    ctx = get_ctx(request)
    queue_size = ctx.job_queue.size() if ctx.job_queue else 0

    if ctx.scheduler is None:
        return {
            "enabled": settings.linkedin_search_schedule_enabled,
            "running": False,
            "last_run_time": None,
            "last_run_jobs": 0,
            "next_run_time": None,
            "queue_size": queue_size,
            "user_last_run": None,
        }

    user_run = ctx.scheduler.get_last_run_for_user(user.id)
    user_last_run = (
        {
            "time": user_run.time.isoformat(),
            "jobs_found": user_run.jobs_found,
            "enqueued": user_run.enqueued,
            "deduped": user_run.deduped,
            "reason": user_run.reason,
            "search_url": user_run.search_url,
            "message": user_run.message,
        }
        if user_run is not None
        else None
    )

    return {
        "enabled": settings.linkedin_search_schedule_enabled,
        "running": ctx.scheduler.is_running,
        "last_run_time": ctx.scheduler.last_run_time.isoformat()
        if ctx.scheduler.last_run_time
        else None,
        "last_run_jobs": ctx.scheduler.last_run_jobs,
        "next_run_time": ctx.scheduler.next_run_time.isoformat()
        if ctx.scheduler.next_run_time
        else None,
        "queue_size": queue_size,
        "user_last_run": user_last_run,
    }


@router.post("/api/jobs/replay-fixtures")
async def replay_fixtures(
    request: Request, user: CurrentUser, limit: Annotated[int, Query(ge=0)] = 0
):
    """Load scraped jobs from fixture file and enqueue for processing."""
    settings = get_settings()
    ctx = get_ctx(request)

    from src.services.jobs.job_fixtures import enqueue_from_fixtures

    path = settings.scraped_jobs_path
    result = await enqueue_from_fixtures(
        path,
        ctx.job_queue,
        repository=ctx.repository,
        limit=limit,
        user_id=user.id,
    )

    if result["total_in_file"] == 0:
        raise HTTPException(404, f"Fixture file not found or empty: {path}")

    cm = ctx.consumer_manager
    if cm is not None and result["enqueued"] > 0 and (cm.task is None or cm.task.done()):
        cm.reset()
        cm.start(ctx)

    return {
        "status": "ok",
        **result,
        "source": str(path),
    }


@router.get("/api/jobs/stats")
async def get_job_stats(request: Request, user: CurrentUser) -> dict[str, int]:
    """Return per-status job counts for the authenticated user."""
    try:
        ctx = get_ctx(request)
        return await ctx.repository.get_status_counts(user.id)
    except Exception as e:
        logger.error(f"Failed to get job stats: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get job stats") from None


# Must precede /api/jobs/{job_id}/* wildcards or FastAPI will shadow it.
@router.get("/api/jobs")
async def list_jobs(
    request: Request,
    user: CurrentUser,
    status: Annotated[list[str] | None, Query()] = None,
    source: Annotated[list[str] | None, Query()] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """List the authenticated user's jobs with optional filters."""
    # Validate status tokens before hitting the repository.
    if status:
        for token in status:
            try:
                BusinessState(token)
            except ValueError as e:
                raise HTTPException(400, f"Unknown status: {e}") from None

    try:
        orchestrator = get_orchestrator(request)
        items, total = await orchestrator.list_jobs(
            user.id,
            statuses=status,
            sources=source,
            created_from=normalize_query_datetime(created_from),
            created_to=normalize_query_datetime(created_to),
            search=search,
            limit=limit,
            offset=offset,
        )
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list jobs: {e}", exc_info=True)
        raise HTTPException(500, "Failed to list jobs") from None


@router.delete("/api/jobs/cleanup")
async def cleanup_jobs(
    request: Request,
    user: CurrentUser,
    older_than_days: Annotated[int, Query(ge=1)] = 90,
    statuses: Annotated[list[str] | None, Query()] = None,
) -> dict:
    """Delete old jobs to prevent database bloat."""
    deletable_statuses = {
        BusinessState.DECLINED,
        BusinessState.FAILED,
        BusinessState.COMPLETED,
        BusinessState.FILTERED_OUT,
    }
    try:
        if statuses is None:
            statuses = ["declined", "failed"]
        if not statuses:
            raise HTTPException(400, "At least one status must be provided")

        deletable_values = {s.value for s in deletable_statuses}
        invalid = set(statuses) - deletable_values
        if invalid:
            raise HTTPException(
                400,
                f"Cannot delete jobs with status: {', '.join(sorted(invalid))}. "
                f"Allowed: {', '.join(sorted(deletable_values))}",
            )

        ctx = get_ctx(request)
        deleted = await ctx.repository.cleanup(older_than_days, statuses, user_id=user.id)
        return {"deleted": deleted, "message": f"Deleted {deleted} jobs"}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        logger.error(f"Failed to cleanup jobs: {e}", exc_info=True)
        raise HTTPException(500, "Failed to cleanup jobs") from None


@router.get("/api/jobs/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str, request: Request, user: CurrentUser
) -> JobStatusResponse:
    """Get status of a submitted job."""
    try:
        ctx = get_ctx(request)
        job_record = await ctx.repository.get_for_user(job_id, user.id)
        if job_record is None:
            thread_info = await ctx.get_workflow_thread(job_id)
            if thread_info is None or thread_info.get("user_id", "") != user.id:
                raise KeyError(f"Job {job_id} not found")

        orchestrator = get_orchestrator(request)
        return await orchestrator.get_status(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found") from None
    except Exception as e:
        logger.error(f"Failed to get status for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get job status") from None


class ProceedRequest(BaseModel):
    """Optional body for the 'Proceed Anyway' override.

    A reason (why the filter was wrong) is captured as a false-negative signal
    for the auto-refiner when present.
    """

    override_reason: str | None = Field(None, max_length=2000)


@router.post("/api/jobs/{job_id}/proceed", response_model=JobSubmitResponse)
async def proceed_filtered_out_job(
    job_id: str,
    request: Request,
    user: CurrentUser,
    body: ProceedRequest | None = None,
) -> JobSubmitResponse:
    """Override the filter for a filtered-out job ("Proceed Anyway").

    Re-enters CV generation (skipping extraction + filtering) so the job
    lands in the HITL review queue.
    """
    try:
        orchestrator = get_orchestrator(request)
        override_reason = body.override_reason if body else None
        return await orchestrator.proceed_filtered_out(
            job_id, user.id, override_reason=override_reason
        )
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found") from None
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from None
    except Exception as e:
        logger.error(f"Failed to proceed job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to proceed with job") from None


@router.post("/api/jobs/{job_id}/apply", response_model=JobSubmitResponse)
async def apply_job(
    job_id: str, request: Request, user: CurrentUser
) -> JobSubmitResponse:
    """Manually (re-)trigger an Easy Apply run for a job awaiting application.

    Used after the extension connects to recover a job parked in
    ``needs_extension`` (or re-run an ``approved`` job). Dispatches the
    deterministic application workflow when a session is connected, else parks
    the job back in ``needs_extension``.
    """
    from src.services.jobs.apply_trigger import NEEDS_EXTENSION_MESSAGE, trigger_apply

    ctx = get_ctx(request)
    job = await ctx.repository.get_for_user(job_id, user.id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found")

    retriable = {BusinessState.NEEDS_EXTENSION, BusinessState.APPROVED}
    if job.status not in retriable:
        raise HTTPException(
            409, f"Job is not awaiting application (status: {job.status})"
        )

    try:
        new_state = await trigger_apply(ctx, job_id, user.id)
    except Exception as e:
        logger.error(f"Failed to trigger apply for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to start application") from None

    message = (
        "Application started."
        if new_state == BusinessState.APPLYING
        else NEEDS_EXTENSION_MESSAGE
    )
    return JobSubmitResponse(job_id=job_id, status=new_state, message=message)


@router.get("/api/jobs/{job_id}/pdf")
async def download_job_pdf(job_id: str, request: Request, user: CurrentUser):
    """Download generated CV PDF for a job."""
    settings = get_settings()
    try:
        status = await get_job_status(job_id, request, user)

        if status.status == BusinessState.FAILED:
            raise HTTPException(400, f"Job failed: {status.error_message}")

        pdf_ready_statuses = {
            BusinessState.COMPLETED,
            BusinessState.PENDING,
            BusinessState.APPROVED,
            BusinessState.RETRYING,
            BusinessState.APPLIED,
            WorkflowStep.PDF_GENERATED,
        }
        if status.status not in pdf_ready_statuses:
            raise HTTPException(400, f"PDF not ready yet (status: {status.status})")

        if not status.pdf_path:
            raise HTTPException(404, "PDF path not set in job state")

        pdf_path = Path(status.pdf_path).resolve()
        allowed_dir = Path(settings.generated_cvs_dir).resolve()
        if not pdf_path.is_relative_to(allowed_dir):
            raise HTTPException(403, "Access denied")

        if not pdf_path.exists():
            raise HTTPException(404, "PDF file not found")

        return FileResponse(
            path=str(pdf_path),
            media_type="application/pdf",
            filename=pdf_path.name,
            headers={"Content-Disposition": f'attachment; filename="{pdf_path.name}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download PDF for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to download PDF") from None


@router.get("/api/jobs/{job_id}/html", response_class=HTMLResponse)
async def get_job_cv_html(
    job_id: str, request: Request, user: CurrentUser
) -> HTMLResponse:
    """Return rendered HTML CV for a job."""
    try:
        ctx = get_ctx(request)
        status = await get_job_status(job_id, request, user)

        if status.status == BusinessState.FAILED:
            raise HTTPException(400, f"Job failed: {status.error_message}")

        cv_ready_statuses = {
            BusinessState.COMPLETED,
            BusinessState.PENDING,
            BusinessState.APPROVED,
            BusinessState.RETRYING,
            BusinessState.APPLIED,
            WorkflowStep.PDF_GENERATED,
        }
        if status.status not in cv_ready_statuses:
            raise HTTPException(400, f"CV not ready yet (status: {status.status})")

        if not status.cv_json:
            raise HTTPException(404, "CV JSON not found for this job")

        from src.services.cv.pdf_generator import PDFGenerator

        template_name = "compact"
        thread_info = await ctx.get_workflow_thread(job_id)
        if thread_info is not None:
            thread_id = thread_info["thread_id"]
            config = {"configurable": {"thread_id": thread_id}}
            workflow_type = thread_info.get("workflow_type", "preparation")
            workflow = (
                ctx.retry_workflow if workflow_type == "retry" else ctx.prep_workflow
            )
            state = workflow.get_state(config).values
            raw_input = state.get("raw_input", {})
            template_name = raw_input.get("template_name") or "compact"

        generator = PDFGenerator(template_name=template_name)
        html = generator.render_html(status.cv_json)

        return HTMLResponse(content=html, media_type="text/html")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get CV HTML for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get CV HTML") from None


@router.delete("/api/jobs/{job_id}")
async def delete_job(
    job_id: str, request: Request, user: CurrentUser
) -> dict:
    """Cascade-delete a job owned by the current user."""
    try:
        ctx = get_ctx(request)
        deleted = await ctx.repository.delete_for_user(job_id, user.id)
        if not deleted:
            raise HTTPException(404, f"Job {job_id} not found")
        return {"deleted": True, "job_id": job_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to delete job") from None
