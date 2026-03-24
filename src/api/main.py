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

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.agents.preparation_workflow import (
    load_master_cv,
)
from src.config.settings import get_settings
from src.context import AppContext, create_app_context
from src.models.mvp import CVGenerationResponse, CVGenerationStatus, JobDescriptionInput
from src.models.unified import (
    ApplicationHistoryItem,
    HITLDecision,
    HITLDecisionResponse,
    JobStatusResponse,
    JobSubmitRequest,
    JobSubmitResponse,
    PendingApproval,
)
from src.services.job_queue import process_queue
from src.services.job_repository import RepositoryError
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
_queue_consumer_task: asyncio.Task | None = None
_linkedin_init_lock = asyncio.Lock()

def _get_ctx(request: Request) -> AppContext:
    """Helper to retrieve AppContext from request."""
    return request.app.state.ctx


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: create AppContext, initialize, yield, cleanup."""
    global _queue_consumer_task

    ctx = create_app_context(settings)
    app.state.ctx = ctx

    _shutting_down = False
    _consumer_restart_count = 0

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
            _queue_consumer_task = _start_queue_consumer(ctx)
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

            _queue_consumer_task = _start_queue_consumer(ctx)

            logger.info("LinkedIn search scheduler started")
        except Exception:
            logger.exception("Failed to start LinkedIn search scheduler")

    yield

    # Shutdown
    _shutting_down = True

    if _consumer_restart_handle is not None:
        _consumer_restart_handle.cancel()

    if ctx.scheduler:
        ctx.scheduler.stop()

    if _queue_consumer_task:
        _queue_consumer_task.cancel()
        try:
            await _queue_consumer_task
        except asyncio.CancelledError:
            pass
        _queue_consumer_task = None

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


_consumer_restart_count = 0
_MAX_CONSUMER_RESTARTS = 5
_CONSUMER_BACKOFF_BASE = 2.0  # seconds
_CONSUMER_STABLE_THRESHOLD = 60.0  # seconds: reset counter if consumer ran this long
_consumer_restart_handle: asyncio.TimerHandle | None = None
_shutting_down = False


def _start_queue_consumer(ctx: AppContext) -> asyncio.Task:
    """Create a queue consumer task with automatic restart on failure (with backoff)."""
    started_at = time.monotonic()

    def _on_consumer_done(task: asyncio.Task) -> None:
        global _queue_consumer_task, _consumer_restart_count, _consumer_restart_handle
        if task.cancelled() or _shutting_down:
            return
        exc = task.exception()
        if exc is not None:
            # If consumer ran long enough, treat as intermittent — reset counter
            uptime = time.monotonic() - started_at
            if uptime >= _CONSUMER_STABLE_THRESHOLD:
                _consumer_restart_count = 0

            _consumer_restart_count += 1
            if _consumer_restart_count > _MAX_CONSUMER_RESTARTS:
                logger.error(
                    "Queue consumer crashed %d times, giving up: %s",
                    _consumer_restart_count, exc,
                )
                return
            delay = _CONSUMER_BACKOFF_BASE * (2 ** (_consumer_restart_count - 1))
            logger.error(
                "Queue consumer crashed (%d/%d): %s — restarting in %.1fs",
                _consumer_restart_count, _MAX_CONSUMER_RESTARTS, exc, delay,
            )
            loop = asyncio.get_running_loop()
            if _consumer_restart_handle is not None:
                _consumer_restart_handle.cancel()
            _consumer_restart_handle = loop.call_later(delay, _restart_consumer, ctx)
        else:
            # Successful exit resets the counter
            _consumer_restart_count = 0

    def _register_linkedin_job(job_id: str, thread_id: str) -> None:
        """Register a LinkedIn-sourced job for HITL tracking."""
        asyncio.create_task(
            ctx.register_workflow(job_id, thread_id, "preparation")
        )

    task = asyncio.create_task(
        process_queue(
            ctx.job_queue,
            job_repository=ctx.repository,
            delay_between_jobs=2.0,
            on_job_processed=_register_linkedin_job,
        )
    )
    task.add_done_callback(_on_consumer_done)
    return task


def _restart_consumer(ctx: AppContext) -> None:
    """Restart the queue consumer (called from call_later)."""
    global _queue_consumer_task, _consumer_restart_handle, _consumer_restart_count
    _consumer_restart_handle = None
    if _shutting_down:
        return
    if _queue_consumer_task is not None and not _queue_consumer_task.done():
        logger.info("Consumer already running, skipping restart")
        _consumer_restart_count = 0
        return
    _queue_consumer_task = _start_queue_consumer(ctx)


def run_workflow_async(job_id: str, thread_id: str, initial_state: dict, ctx: AppContext):
    """Execute LangGraph workflow in background thread (legacy endpoint support)."""
    try:
        config = {"configurable": {"thread_id": thread_id, "repository": ctx.repository}}

        # Invoke preparation workflow with MVP mode (blocking call in background thread)
        result = ctx.prep_workflow.invoke(initial_state, config)

        logger.info(f"Job {job_id} completed with status: {result.get('current_step')}")

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        # Error is stored in workflow state, no need to re-raise


@app.get("/api/health")
async def health():
    """Health check endpoint"""
    return {"status": "running", "message": "LinkedIn Job Application Agent API"}


# MVP CV Generation Endpoints


@app.post("/api/cv/generate", response_model=CVGenerationResponse)
async def generate_cv(
    request: Request, job_input: JobDescriptionInput, background_tasks: BackgroundTasks
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
            "current_step": "queued",
            "retry_count": 0,
            "user_feedback": None,
            "error_message": None,
        }

        # Track in workflow threads for status queries
        await ctx.register_workflow(job_id, thread_id, "preparation")

        # Run workflow in background
        background_tasks.add_task(
            run_workflow_async, job_id=job_id, thread_id=thread_id, initial_state=initial_state, ctx=ctx
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


def run_preparation_workflow_async(job_id: str, thread_id: str, initial_state: dict, ctx: AppContext):
    """Execute Preparation Workflow in background thread."""
    try:
        config = {"configurable": {"thread_id": thread_id, "repository": ctx.repository}}
        result = ctx.prep_workflow.invoke(initial_state, config)
        logger.info(
            f"Preparation workflow for job {job_id} completed: {result.get('current_step')}"
        )
    except Exception as e:
        logger.error(f"Preparation workflow for job {job_id} failed: {e}", exc_info=True)


def run_retry_workflow_async(job_id: str, thread_id: str, initial_state: dict, ctx: AppContext):
    """Execute Retry Workflow in background thread."""
    try:
        config = {"configurable": {"thread_id": thread_id, "repository": ctx.repository}}
        result = ctx.retry_workflow.invoke(initial_state, config)
        logger.info(f"Retry workflow for job {job_id} completed: {result.get('current_step')}")
    except Exception as e:
        logger.error(f"Retry workflow for job {job_id} failed: {e}", exc_info=True)


@app.options("/api/jobs/submit")
async def submit_job_options():
    """Handle CORS preflight for job submission."""
    return {}


@app.post("/api/jobs/submit", response_model=JobSubmitResponse)
async def submit_job(
    job_request: JobSubmitRequest, http_request: Request, background_tasks: BackgroundTasks
) -> JobSubmitResponse:
    """
    Submit a job for CV generation.

    Supports:
    - URL source: Provide job posting URL (lever.co, greenhouse.io, etc.)
    - Manual source: Provide job description directly

    Modes:
    - mvp: Generate CV PDF only (ready for download when complete)
    - full: Generate CV PDF + queue for HITL review + apply
    """
    try:
        ctx = _get_ctx(http_request)

        # Generate unique IDs
        job_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())

        # Validate request
        if job_request.source == "url" and not job_request.url:
            raise HTTPException(400, "URL is required for source='url'")
        if job_request.source == "manual" and not job_request.job_description:
            raise HTTPException(400, "job_description is required for source='manual'")

        # Build raw_input based on source
        if job_request.source == "url":
            raw_input = {"url": job_request.url}
            # If job_description provided, use it as fallback data
            if job_request.job_description:
                raw_input.update(
                    {
                        "title": job_request.job_description.title,
                        "company": job_request.job_description.company,
                        "description": job_request.job_description.description,
                        "requirements": job_request.job_description.requirements,
                    }
                )
        else:  # manual
            raw_input = {
                "title": job_request.job_description.title,
                "company": job_request.job_description.company,
                "description": job_request.job_description.description,
                "requirements": job_request.job_description.requirements,
                "template_name": job_request.job_description.template_name,
                "llm_provider": job_request.job_description.llm_provider,
                "llm_model": job_request.job_description.llm_model,
            }
            logger.info(
                f"API received template_name: {job_request.job_description.template_name}, llm_provider: {job_request.job_description.llm_provider}, llm_model: {job_request.job_description.llm_model}"
            )

        # Load master CV
        from src.agents.preparation_workflow import load_master_cv

        master_cv = load_master_cv()

        # Build initial state
        initial_state = {
            "job_id": job_id,
            "source": job_request.source,
            "mode": job_request.mode,
            "raw_input": raw_input,
            "master_cv": master_cv,
            "current_step": "queued",
            "retry_count": 0,
            "user_feedback": None,
            "error_message": None,
        }

        # Track thread
        await ctx.register_workflow(job_id, thread_id, "preparation")

        # Run workflow in background
        background_tasks.add_task(
            run_preparation_workflow_async,
            job_id=job_id,
            thread_id=thread_id,
            initial_state=initial_state,
            ctx=ctx,
        )

        logger.info(f"Job {job_id} submitted: source={job_request.source}, mode={job_request.mode}")

        return JobSubmitResponse(
            job_id=job_id,
            status="queued",
            message=f"Job submitted successfully. Mode: {job_request.mode}",
        )

    except HTTPException:
        raise
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
    global _queue_consumer_task
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
        if _queue_consumer_task is None or _queue_consumer_task.done():
            global _consumer_restart_handle, _consumer_restart_count
            if _consumer_restart_handle is not None:
                _consumer_restart_handle.cancel()
                _consumer_restart_handle = None
            _consumer_restart_count = 0
            _queue_consumer_task = _start_queue_consumer(ctx)

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
    global _queue_consumer_task
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
    if result["enqueued"] > 0 and (_queue_consumer_task is None or _queue_consumer_task.done()):
        _queue_consumer_task = _start_queue_consumer(ctx)

    return {
        "status": "ok",
        **result,
        "source": str(path),
    }


@app.get("/api/jobs/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(job_id: str, request: Request) -> JobStatusResponse:
    """
    Get status of a submitted job.

    Poll this endpoint every 2-3 seconds until status is 'completed', 'pending', or 'failed'.
    - completed: Ready for download (MVP mode)
    - pending: Awaiting HITL review (Full mode)
    - failed: Job failed with error
    """
    try:
        ctx = _get_ctx(request)

        # Check repository first — it has authoritative state for completed jobs
        # (LangGraph in-memory checkpoints can be stale after server reload)
        try:
            job_record = await ctx.repository.get(job_id)
            if job_record:
                return JobStatusResponse(
                    job_id=job_id,
                    status=job_record.status,
                    source=job_record.source,
                    mode=job_record.mode,
                    job_posting=job_record.job_posting,
                    cv_json=job_record.cv_json,
                    pdf_path=job_record.pdf_path,
                    retry_count=job_record.retry_count,
                    error_message=job_record.error_message,
                    created_at=job_record.created_at,
                    updated_at=job_record.updated_at,
                )
        except Exception:
            pass

        # Fall back to workflow threads (for in-progress jobs not yet saved)
        thread_info = await ctx.get_workflow_thread(job_id)
        if thread_info is not None:
            thread_id = thread_info["thread_id"]
            config = {"configurable": {"thread_id": thread_id}}

            # Get state from appropriate workflow
            if thread_info["workflow_type"] == "preparation":
                state_snapshot = ctx.prep_workflow.get_state(config)
            elif thread_info["workflow_type"] == "retry":
                state_snapshot = ctx.retry_workflow.get_state(config)
            else:
                raise HTTPException(500, f"Unknown workflow type: {thread_info['workflow_type']}")

            state_values = state_snapshot.values

            return JobStatusResponse(
                job_id=job_id,
                status=state_values.get("current_step", "queued"),
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

        raise HTTPException(404, f"Job {job_id} not found")

    except HTTPException:
        raise
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
        if status.status == "failed":
            raise HTTPException(400, f"Job failed: {status.error_message}")

        if status.status not in ["completed", "pending", "pdf_generated"]:
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
        if status.status == "failed":
            raise HTTPException(400, f"Job failed: {status.error_message}")

        if status.status not in ["completed", "pending", "pdf_generated"]:
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
    """
    Get all jobs pending HITL review.

    Returns list of jobs with status='pending' for batch review.
    Frontend can display these in a Tinder-like UI for approve/decline/retry.
    """
    try:
        ctx = _get_ctx(request)

        # Get pending jobs from repository
        pending_jobs = await ctx.repository.get_pending()

        return [
            PendingApproval(
                job_id=job.job_id,
                job_posting=job.job_posting or {},
                cv_json=job.cv_json or {},
                pdf_path=job.pdf_path,
                retry_count=job.retry_count,
                created_at=job.created_at,
                source=job.source,
                application_url=job.application_url or (job.job_posting or {}).get("url"),
            )
            for job in pending_jobs
        ]

    except NotImplementedError:
        # Repository not implemented - return jobs from in-memory tracking
        logger.warning("Repository not implemented, scanning workflow states for pending jobs")
        ctx = _get_ctx(request)
        pending = []

        all_threads = await ctx.get_all_workflow_threads()
        for job_id, thread_info in all_threads.items():
            if thread_info["workflow_type"] != "preparation":
                continue

            thread_id = thread_info["thread_id"]
            config = {"configurable": {"thread_id": thread_id}}

            try:
                state_snapshot = ctx.prep_workflow.get_state(config)
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
                            application_url=state_values.get("job_posting", {}).get("url"),
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to get state for job {job_id}: {e}")

        return pending

    except Exception as e:
        logger.error(f"Failed to get pending jobs: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get pending jobs") from None


@app.post("/api/hitl/{job_id}/decide", response_model=HITLDecisionResponse)
async def submit_hitl_decision(
    job_id: str, decision: HITLDecision, request: Request, background_tasks: BackgroundTasks
) -> HITLDecisionResponse:
    """
    Submit HITL decision for a pending job.

    Decisions:
    - approved: Queue job for application (triggers Application Workflow)
    - declined: Mark job as declined (no further action)
    - retry: Regenerate CV with feedback (triggers Retry Workflow)

    For retry, feedback is required.
    """
    try:
        ctx = _get_ctx(request)

        # Validate retry has feedback
        if decision.decision == "retry" and not decision.feedback:
            raise HTTPException(400, "Feedback is required for retry decision")

        # Get current job state from repository (authoritative source)
        job_record = await ctx.repository.get(job_id)
        if job_record is None:
            raise HTTPException(404, f"Job {job_id} not found")

        if job_record.status != "pending":
            raise HTTPException(
                400,
                f"Job {job_id} is not pending review (status: {job_record.status})",
            )

        if decision.decision == "approved":
            # TODO: Trigger Application Workflow (not implemented yet)
            # For now, just update status to "approved"
            logger.info(f"Job {job_id} approved for application (workflow not implemented)")

            # Update status in repository
            try:
                await ctx.repository.update(job_id, {"status": "approved"})
            except RepositoryError as e:
                logger.warning(f"Failed to update repository for job {job_id}: {e}")

            return HITLDecisionResponse(
                job_id=job_id,
                status="approved",
                message="Job approved. Application workflow not yet implemented.",
            )

        elif decision.decision == "declined":
            logger.info(f"Job {job_id} declined by user")

            # Update status in repository
            try:
                await ctx.repository.update(job_id, {"status": "declined"})
            except RepositoryError as e:
                logger.warning(f"Failed to update repository for job {job_id}: {e}")

            return HITLDecisionResponse(
                job_id=job_id,
                status="declined",
                message="Job declined. No further action will be taken.",
            )

        elif decision.decision == "retry":
            logger.info(f"Job {job_id} queued for retry with user feedback")

            # Update repository status to "retrying" so job leaves pending queue
            try:
                await ctx.repository.update(job_id, {"status": "retrying"})
            except RepositoryError as e:
                logger.warning(f"Failed to update repository for job {job_id}: {e}")

            # Create new thread for retry workflow
            retry_thread_id = str(uuid.uuid4())

            # Build retry state from repository record (authoritative source)
            from src.agents.preparation_workflow import load_master_cv
            retry_state = {
                "job_id": job_id,
                "user_feedback": decision.feedback,
                "job_posting": job_record.job_posting,
                "master_cv": load_master_cv(),
                "retry_count": job_record.retry_count,
                "current_step": "queued",
                "error_message": None,
            }

            # Update tracking
            await ctx.register_workflow(job_id, retry_thread_id, "retry")

            # Run retry workflow in background
            background_tasks.add_task(
                run_retry_workflow_async,
                job_id=job_id,
                thread_id=retry_thread_id,
                initial_state=retry_state,
                ctx=ctx,
            )

            return HITLDecisionResponse(
                job_id=job_id,
                status="retrying",
                message="CV regeneration started with your feedback.",
            )

        else:
            raise HTTPException(400, f"Invalid decision: {decision.decision}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to process HITL decision for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to process decision") from None


@app.get("/api/hitl/history", response_model=list[ApplicationHistoryItem])
async def get_application_history(
    request: Request, limit: int = 50, status: str | None = None
) -> list[ApplicationHistoryItem]:
    """
    Get application history.

    Optional filtering by status: approved, declined, applied, failed.
    """
    try:
        ctx = _get_ctx(request)
        statuses = [status] if status else None
        jobs = await ctx.repository.get_history(limit=limit, statuses=statuses)

        return [
            ApplicationHistoryItem(
                job_id=job.job_id,
                job_title=job.job_posting.get("title") if job.job_posting else None,
                company=job.job_posting.get("company") if job.job_posting else None,
                status=job.status,
                applied_at=job.updated_at if job.status == "applied" else None,
                created_at=job.created_at,
            )
            for job in jobs
        ]

    except NotImplementedError:
        logger.warning("Repository not implemented, returning empty history")
        return []

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
    deletable_statuses = {"declined", "failed", "completed"}
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
