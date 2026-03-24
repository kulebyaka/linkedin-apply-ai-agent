"""FastAPI application for HITL UI and job submission.

Provides two sets of endpoints:
1. Legacy MVP endpoints (/api/cv/*) - for backward compatibility
2. Unified endpoints (/api/jobs/*, /api/hitl/*) - new two-workflow pipeline
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.agents._shared import load_master_cv
from src.config.settings import get_settings
from src.context import AppContext, create_app_context
from src.models.mvp import CVGenerationResponse, CVGenerationStatus, JobDescriptionInput
from src.models.state_machine import BusinessState
from src.models.unified import (
    ApplicationHistoryItem,
    HITLDecision,
    HITLDecisionResponse,
    JobStatusResponse,
    JobSubmitRequest,
    JobSubmitResponse,
    PendingApproval,
)
from src.services.hitl_processor import HITLProcessor
from src.services.job_orchestrator import JobOrchestrator
from src.services.job_queue import ConsumerManager
from src.utils.logger import setup_api_logger

settings = get_settings()
logger = setup_api_logger(level="INFO")

# Ensure all src.* loggers propagate to a handler (scheduler, scraper, etc.)
_src_logger = logging.getLogger("src")
if not _src_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _src_logger.addHandler(_handler)
    _src_logger.setLevel(logging.INFO)

# Module-level state for consumer management (not business state)
_consumer_manager = ConsumerManager()
_linkedin_init_lock = asyncio.Lock()

def _get_ctx(request: Request) -> AppContext:
    """Helper to retrieve AppContext from request."""
    return request.app.state.ctx


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: create AppContext, initialize, yield, cleanup."""
    ctx = create_app_context(settings)
    app.state.ctx = ctx
    app.state.consumer_manager = _consumer_manager

    logger.info(f"Starting up with repository type: {settings.repo_type}")
    await ctx.repository.initialize()
    logger.info("Repository initialized successfully")

    # Fixture replay mode: seed jobs from file, skip LinkedIn entirely
    if settings.seed_jobs_from_file:
        logger.info(
            "Fixture replay mode enabled — LinkedIn scraping disabled. "
            "Loading jobs from %s", settings.scraped_jobs_path,
        )
        from src.services.job_fixtures import enqueue_from_fixtures

        result = await enqueue_from_fixtures(
            settings.scraped_jobs_path,
            ctx.job_queue,
            repository=ctx.repository,
            limit=settings.seed_jobs_limit,
        )
        logger.info(
            "Fixture replay: enqueued=%d, skipped=%d, total_in_file=%d",
            result["enqueued"], result["skipped"], result["total_in_file"],
        )
        if result["enqueued"] > 0:
            _consumer_manager.start(ctx)
    elif settings.linkedin_search_schedule_enabled:
        try:
            from src.services.browser_automation import LinkedInAutomation
            from src.services.linkedin_scraper import LinkedInJobScraper
            from src.services.scheduler import LinkedInSearchScheduler

            browser = LinkedInAutomation(settings)
            await browser.initialize()
            ctx.browser = browser

            scraper = LinkedInJobScraper(browser, settings)
            scheduler = LinkedInSearchScheduler(settings, scraper, ctx.job_queue)
            scheduler.start()
            ctx.scheduler = scheduler

            _consumer_manager.start(ctx)

            logger.info("LinkedIn search scheduler started")
        except Exception:
            logger.exception("Failed to start LinkedIn search scheduler")

    yield

    # Shutdown
    _consumer_manager.stop()
    await _consumer_manager.wait_stopped()

    if ctx.scheduler:
        ctx.scheduler.stop()

    if ctx.browser:
        await ctx.browser.close()

    logger.info("Shutting down repository...")
    await ctx.repository.close()
    logger.info("Repository closed")


app = FastAPI(
    title="LinkedIn Job Application Agent API",
    description="API for Human-in-the-Loop job application review",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all API requests with method, path, status, and duration."""
    start_time = time.time()

    # Process request
    response = await call_next(request)

    # Calculate duration
    duration_ms = (time.time() - start_time) * 1000

    # Log request details
    logger.info(
        f"{request.method} {request.url.path} - {response.status_code} ({duration_ms:.1f}ms)"
    )

    return response




async def run_workflow_async(job_id: str, thread_id: str, initial_state: dict, ctx: AppContext):
    """Execute LangGraph workflow asynchronously (legacy endpoint support)."""
    try:
        config = {"configurable": {"thread_id": thread_id, "repository": ctx.repository}}

        result = await ctx.prep_workflow.ainvoke(initial_state, config)

        logger.info(f"Job {job_id} completed with status: {result.get('current_step')}")

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)


@app.get("/api/health")
async def health():
    """Health check endpoint with consumer health status."""
    return {
        "status": "running",
        "message": "LinkedIn Job Application Agent API",
        **_consumer_manager.health_check(),
    }


# MVP CV Generation Endpoints


@app.post("/api/cv/generate", response_model=CVGenerationResponse)
async def generate_cv(
    request: Request, job_input: JobDescriptionInput
) -> CVGenerationResponse:
    """
    Submit CV generation request

    Returns job_id immediately. Client should poll /api/cv/status/{job_id}
    """
    try:
        ctx = _get_ctx(request)

        # Generate unique IDs
        job_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())

        # Load master CV
        master_cv = load_master_cv()

        # Build raw_input for preparation workflow
        raw_input = {
            "title": job_input.title,
            "company": job_input.company,
            "description": job_input.description,
            "requirements": job_input.requirements or "",
        }

        # Initialize workflow state (preparation workflow with MVP mode)
        initial_state = {
            "job_id": job_id,
            "source": "manual",
            "mode": "mvp",
            "raw_input": raw_input,
            "master_cv": master_cv,
            "current_step": BusinessState.QUEUED,
            "retry_count": 0,
            "user_feedback": None,
            "error_message": None,
        }

        # Track in workflow threads for status queries
        await ctx.register_workflow(job_id, thread_id, "preparation")

        # Run workflow in background
        asyncio.create_task(
            run_workflow_async(job_id=job_id, thread_id=thread_id, initial_state=initial_state, ctx=ctx)
        )

        logger.info(f"Job {job_id} submitted: {job_input.title} at {job_input.company}")

        return CVGenerationResponse(
            job_id=job_id, status="queued", message="CV generation job submitted successfully"
        )

    except Exception as e:
        logger.error(f"Failed to submit job: {e}", exc_info=True)
        raise HTTPException(500, "Failed to submit job") from None


@app.get("/api/cv/status/{job_id}", response_model=CVGenerationStatus)
async def get_cv_status(job_id: str, request: Request) -> CVGenerationStatus:
    """
    Get status of CV generation job

    Poll this endpoint every 2-3 seconds until status is 'completed' or 'failed'
    """
    try:
        ctx = _get_ctx(request)

        # Check workflow tracking
        thread_info = await ctx.get_workflow_thread(job_id)
        if thread_info is None:
            raise HTTPException(404, f"Job {job_id} not found")

        thread_id = thread_info["thread_id"]
        config = {"configurable": {"thread_id": thread_id}}

        # Get current state from LangGraph checkpointer (using preparation_workflow)
        state_snapshot = ctx.prep_workflow.get_state(config)
        state_values = state_snapshot.values

        return CVGenerationStatus(
            job_id=job_id,
            status=state_values.get("current_step", "queued"),
            created_at=thread_info.get("created_at", datetime.now(tz=timezone.utc)),
            completed_at=datetime.now(tz=timezone.utc)
            if state_values.get("current_step") in ["completed", "failed"]
            else None,
            error_message=state_values.get("error_message"),
            pdf_path=state_values.get("tailored_cv_pdf_path"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get status for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get job status") from None


@app.get("/api/cv/download/{job_id}")
async def download_cv(job_id: str, request: Request):
    """
    Download generated CV PDF

    Returns 400 if job not completed
    Returns 404 if PDF file missing
    """
    try:
        # Get status first
        status = await get_cv_status(job_id, request)

        # Check if job is completed
        if status.status == "failed":
            raise HTTPException(400, f"Job failed: {status.error_message}")

        if status.status != "completed":
            raise HTTPException(400, f"Job not ready yet (status: {status.status})")

        # Check PDF exists
        if not status.pdf_path:
            raise HTTPException(404, "PDF path not set in job state")

        pdf_path = Path(status.pdf_path).resolve()
        allowed_dir = Path(settings.generated_cvs_dir).resolve()
        if not pdf_path.is_relative_to(allowed_dir):
            raise HTTPException(403, "Access denied")

        if not pdf_path.exists():
            raise HTTPException(404, "PDF file not found")

        # Return file
        return FileResponse(
            path=str(pdf_path),
            media_type="application/pdf",
            filename=pdf_path.name,
            headers={"Content-Disposition": f'attachment; filename="{pdf_path.name}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download CV for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to download CV") from None


# =============================================================================
# Unified Job Submission Endpoints
# =============================================================================


def _get_orchestrator(request: Request) -> JobOrchestrator:
    """Helper to retrieve JobOrchestrator from request."""
    ctx = _get_ctx(request)
    assert ctx.orchestrator is not None, "JobOrchestrator not initialized"
    return ctx.orchestrator


def _get_hitl(request: Request) -> HITLProcessor:
    """Helper to retrieve HITLProcessor from request."""
    ctx = _get_ctx(request)
    assert ctx.hitl_processor is not None, "HITLProcessor not initialized"
    return ctx.hitl_processor


@app.options("/api/jobs/submit")
async def submit_job_options():
    """Handle CORS preflight for job submission."""
    return {}


@app.post("/api/jobs/submit", response_model=JobSubmitResponse)
async def submit_job(
    job_request: JobSubmitRequest, http_request: Request
) -> JobSubmitResponse:
    """Submit a job for CV generation."""
    try:
        orchestrator = _get_orchestrator(http_request)
        return await orchestrator.submit_job(job_request)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        logger.error(f"Failed to submit job: {e}", exc_info=True)
        raise HTTPException(500, "Failed to submit job") from None


# =============================================================================
# LinkedIn Search Endpoints
# (must be defined BEFORE /api/jobs/{job_id} wildcard routes)
# =============================================================================


@app.post("/api/jobs/linkedin-search")
async def trigger_linkedin_search(request: Request):
    """Trigger a LinkedIn search manually.

    Runs search in background and returns immediately.
    """
    ctx = _get_ctx(request)

    if settings.seed_jobs_from_file:
        raise HTTPException(
            409,
            "LinkedIn scraping is disabled in fixture replay mode "
            "(SEED_JOBS_FROM_FILE=true). Use POST /api/jobs/replay-fixtures instead.",
        )

    async with _linkedin_init_lock:
        if ctx.scheduler is None:
            # Create a temporary scheduler for one-off search
            try:
                from src.services.browser_automation import LinkedInAutomation
                from src.services.linkedin_scraper import LinkedInJobScraper
                from src.services.scheduler import LinkedInSearchScheduler

                if ctx.browser is None:
                    browser = LinkedInAutomation(settings)
                    await browser.initialize()
                    ctx.browser = browser

                scraper = LinkedInJobScraper(ctx.browser, settings)
                ctx.scheduler = LinkedInSearchScheduler(settings, scraper, ctx.job_queue)
            except Exception:
                logger.exception("Failed to initialize LinkedIn search components")
                raise HTTPException(500, "Failed to initialize LinkedIn search components") from None

        # Ensure a queue consumer is running
        cm = _consumer_manager
        if cm.task is None or cm.task.done():
            cm.reset()
            cm.start(ctx)

    async def _run_search():
        try:
            count = await ctx.scheduler.run_search()
            logger.info("Manual LinkedIn search completed: %d jobs found", count)
        except Exception:
            logger.exception("Manual LinkedIn search failed")

    asyncio.create_task(_run_search())

    return {"status": "started", "message": "LinkedIn search triggered"}


@app.get("/api/jobs/linkedin-search/status")
async def get_linkedin_search_status(request: Request):
    """Return current scheduler state."""
    ctx = _get_ctx(request)
    queue_size = ctx.job_queue.size() if ctx.job_queue else 0

    if ctx.scheduler is None:
        return {
            "enabled": settings.linkedin_search_schedule_enabled,
            "running": False,
            "last_run_time": None,
            "last_run_jobs": 0,
            "next_run_time": None,
            "queue_size": queue_size,
        }

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
    }


@app.post("/api/jobs/replay-fixtures")
async def replay_fixtures(request: Request, limit: Annotated[int, Query(ge=0)] = 0):
    """Load scraped jobs from fixture file and enqueue for processing.

    Useful for HITL testing and demos. Jobs already in the repository are skipped.
    """
    ctx = _get_ctx(request)

    from src.services.job_fixtures import enqueue_from_fixtures

    path = settings.scraped_jobs_path
    result = await enqueue_from_fixtures(
        path,
        ctx.job_queue,
        repository=ctx.repository,
        limit=limit,
    )

    if result["total_in_file"] == 0:
        raise HTTPException(404, f"Fixture file not found or empty: {path}")

    # Ensure queue consumer is running to process the enqueued jobs
    cm = _consumer_manager
    if result["enqueued"] > 0 and (cm.task is None or cm.task.done()):
        cm.reset()
        cm.start(ctx)

    return {
        "status": "ok",
        **result,
        "source": str(path),
    }


@app.get("/api/jobs/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(job_id: str, request: Request) -> JobStatusResponse:
    """Get status of a submitted job."""
    try:
        orchestrator = _get_orchestrator(request)
        return await orchestrator.get_status(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found") from None
    except Exception as e:
        logger.error(f"Failed to get status for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get job status") from None


@app.get("/api/jobs/{job_id}/pdf")
async def download_job_pdf(job_id: str, request: Request):
    """
    Download generated CV PDF for a job.

    Returns 400 if job not completed or pending.
    Returns 404 if PDF file missing.
    """
    try:
        # Get job status
        status = await get_job_status(job_id, request)

        # Check if job is ready
        if status.status == BusinessState.FAILED:
            raise HTTPException(400, f"Job failed: {status.error_message}")

        pdf_ready_statuses = {
            BusinessState.CV_READY,
            BusinessState.PENDING_REVIEW,
            "pdf_generated",  # Workflow-internal step from in-progress query
        }
        if status.status not in pdf_ready_statuses:
            raise HTTPException(400, f"PDF not ready yet (status: {status.status})")

        # Check PDF exists
        if not status.pdf_path:
            raise HTTPException(404, "PDF path not set in job state")

        pdf_path = Path(status.pdf_path).resolve()
        allowed_dir = Path(settings.generated_cvs_dir).resolve()
        if not pdf_path.is_relative_to(allowed_dir):
            raise HTTPException(403, "Access denied")

        if not pdf_path.exists():
            raise HTTPException(404, "PDF file not found")

        # Return file
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


@app.get("/api/jobs/{job_id}/html", response_class=HTMLResponse)
async def get_job_cv_html(job_id: str, request: Request) -> HTMLResponse:
    """
    Return rendered HTML CV for a job.

    Uses the same Jinja2 template as PDF generation but returns HTML directly.
    Useful for frontend preview without downloading PDF.
    """
    try:
        ctx = _get_ctx(request)

        # Get job status
        status = await get_job_status(job_id, request)

        # Check if job is ready
        if status.status == BusinessState.FAILED:
            raise HTTPException(400, f"Job failed: {status.error_message}")

        cv_ready_statuses = {
            BusinessState.CV_READY,
            BusinessState.PENDING_REVIEW,
            "pdf_generated",
        }
        if status.status not in cv_ready_statuses:
            raise HTTPException(400, f"CV not ready yet (status: {status.status})")

        # Check CV JSON exists
        if not status.cv_json:
            raise HTTPException(404, "CV JSON not found for this job")

        # Render HTML using existing template system
        from src.services.pdf_generator import PDFGenerator

        # Get template name from job state
        template_name = "compact"  # Default template
        thread_info = await ctx.get_workflow_thread(job_id)
        if thread_info is not None:
            thread_id = thread_info["thread_id"]
            config = {"configurable": {"thread_id": thread_id}}
            state = ctx.prep_workflow.get_state(config).values
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


# =============================================================================
# HITL (Human-in-the-Loop) Endpoints
# =============================================================================


@app.get("/api/hitl/pending", response_model=list[PendingApproval])
async def get_hitl_pending(request: Request) -> list[PendingApproval]:
    """Get all jobs pending HITL review."""
    try:
        hitl = _get_hitl(request)
        return await hitl.get_pending()
    except Exception as e:
        logger.error(f"Failed to get pending jobs: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get pending jobs") from None


@app.post("/api/hitl/{job_id}/decide", response_model=HITLDecisionResponse)
async def submit_hitl_decision(
    job_id: str, decision: HITLDecision, request: Request
) -> HITLDecisionResponse:
    """Submit HITL decision for a pending job."""
    try:
        hitl = _get_hitl(request)
        return await hitl.process_decision(job_id, decision)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found") from None
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        logger.error(f"Failed to process HITL decision for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to process decision") from None


@app.get("/api/hitl/history", response_model=list[ApplicationHistoryItem])
async def get_application_history(
    request: Request, limit: int = 50, status: str | None = None
) -> list[ApplicationHistoryItem]:
    """Get application history."""
    try:
        hitl = _get_hitl(request)
        return await hitl.get_history(limit=limit, status=status)
    except Exception as e:
        logger.error(f"Failed to get application history: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get history") from None



# =============================================================================
# Data Cleanup Endpoints
# =============================================================================


@app.delete("/api/jobs/cleanup")
async def cleanup_jobs(
    request: Request,
    older_than_days: Annotated[int, Query(ge=1, description="Delete jobs older than this many days")] = 90,
    statuses: Annotated[
        list[str], Query(description="Only delete jobs with these statuses")
    ] = None,
) -> dict:
    """Delete old jobs to prevent database bloat.

    This endpoint removes job records that are older than the specified number of days
    and have one of the specified statuses. Useful for data retention and cleanup.

    Args:
        older_than_days: Delete jobs older than this many days (default: 90, min: 1)
        statuses: Only delete jobs with these statuses (default: ["declined", "failed"])

    Returns:
        {"deleted": int, "message": str}
    """
    deletable_statuses = {
        BusinessState.DECLINED,
        BusinessState.FAILED,
        BusinessState.CV_READY,
    }
    try:
        if statuses is None:
            statuses = ["declined", "failed"]
        if not statuses:
            raise HTTPException(400, "At least one status must be provided")

        invalid = set(statuses) - deletable_statuses
        if invalid:
            raise HTTPException(
                400,
                f"Cannot delete jobs with status: {', '.join(sorted(invalid))}. "
                f"Allowed: {', '.join(sorted(deletable_statuses))}",
            )

        ctx = _get_ctx(request)
        deleted = await ctx.repository.cleanup(older_than_days, statuses)
        return {"deleted": deleted, "message": f"Deleted {deleted} jobs"}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        logger.error(f"Failed to cleanup jobs: {e}", exc_info=True)
        raise HTTPException(500, "Failed to cleanup jobs") from None


# =============================================================================
# Static File Serving for UI
# =============================================================================

# Mount static files for UI (SvelteKit build output)
# IMPORTANT: This must be the LAST route definition to avoid shadowing API routes
UI_BUILD_PATH = Path(__file__).parent.parent.parent / "ui" / "build"
if UI_BUILD_PATH.exists():
    app.mount("/", StaticFiles(directory=str(UI_BUILD_PATH), html=True), name="ui")
    logger.info(f"Mounted UI at / from {UI_BUILD_PATH}")
else:
    logger.warning(
        f"UI build directory not found at {UI_BUILD_PATH}. "
        "Run 'cd ui && npm run build' to build the UI."
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
