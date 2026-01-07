"""FastAPI application for HITL UI and job submission.

Provides two sets of endpoints:
1. Legacy MVP endpoints (/api/cv/*) - for backward compatibility
2. Unified endpoints (/api/jobs/*, /api/hitl/*) - new two-workflow pipeline
"""

import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.agents.preparation_workflow import (
    create_preparation_workflow,
    load_master_cv,
)
from src.agents.preparation_workflow import (
    set_repository as set_prep_repository,
)
from src.agents.retry_workflow import (
    create_retry_workflow,
)
from src.agents.retry_workflow import (
    set_repository as set_retry_repository,
)
from src.config.settings import get_settings
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
from src.services.job_repository import get_repository, JobRepository

settings = get_settings()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize LangGraph workflows (singletons)
preparation_workflow = create_preparation_workflow()
retry_workflow = create_retry_workflow()

# Initialize shared repository using factory (environment-based selection)
job_repository: JobRepository = get_repository(
    repo_type=settings.repo_type,
    db_path=settings.db_path,
)
set_prep_repository(job_repository)
set_retry_repository(job_repository)

# In-memory thread tracking (maps job_id -> thread_id)
# For MVP - would use Redis/PostgreSQL in production
workflow_threads: dict[str, str] = {}
workflow_created_at: dict[str, datetime] = {}  # Track creation time

# Unified workflow thread tracking
unified_threads: dict[str, dict] = {}  # job_id -> {thread_id, workflow_type, created_at}

app = FastAPI(
    title="LinkedIn Job Application Agent API",
    description="API for Human-in-the-Loop job application review",
    version="0.1.0",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    """Initialize repository on startup."""
    logger.info(f"Starting up with repository type: {settings.repo_type}")
    await job_repository.initialize()
    logger.info("Repository initialized successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Close repository on shutdown."""
    logger.info("Shutting down repository...")
    await job_repository.close()
    logger.info("Repository closed")


def run_workflow_async(job_id: str, thread_id: str, initial_state: dict):
    """Execute LangGraph workflow in background thread (legacy endpoint support)."""
    try:
        config = {"configurable": {"thread_id": thread_id}}

        # Invoke preparation workflow with MVP mode (blocking call in background thread)
        result = preparation_workflow.invoke(initial_state, config)

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
    job_input: JobDescriptionInput, background_tasks: BackgroundTasks
) -> CVGenerationResponse:
    """
    Submit CV generation request

    Returns job_id immediately. Client should poll /api/cv/status/{job_id}
    """
    try:
        # Generate unique IDs
        job_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())

        # Store thread mapping
        workflow_threads[job_id] = thread_id
        workflow_created_at[job_id] = datetime.now()

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

        # Track in unified_threads for status queries
        unified_threads[job_id] = {
            "thread_id": thread_id,
            "workflow_type": "preparation",
            "created_at": workflow_created_at[job_id],
        }

        # Run workflow in background
        background_tasks.add_task(
            run_workflow_async, job_id=job_id, thread_id=thread_id, initial_state=initial_state
        )

        logger.info(f"Job {job_id} submitted: {job_input.title} at {job_input.company}")

        return CVGenerationResponse(
            job_id=job_id, status="queued", message="CV generation job submitted successfully"
        )

    except Exception as e:
        logger.error(f"Failed to submit job: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to submit job: {str(e)}")


@app.get("/api/cv/status/{job_id}", response_model=CVGenerationStatus)
async def get_cv_status(job_id: str) -> CVGenerationStatus:
    """
    Get status of CV generation job

    Poll this endpoint every 2-3 seconds until status is 'completed' or 'failed'
    """
    try:
        # Check if job exists (check both tracking dicts for backward compatibility)
        if job_id not in workflow_threads and job_id not in unified_threads:
            raise HTTPException(404, f"Job {job_id} not found")

        # Get thread_id from appropriate tracker
        if job_id in unified_threads:
            thread_id = unified_threads[job_id]["thread_id"]
        else:
            thread_id = workflow_threads[job_id]

        config = {"configurable": {"thread_id": thread_id}}

        # Get current state from LangGraph checkpointer (using preparation_workflow)
        state_snapshot = preparation_workflow.get_state(config)
        state_values = state_snapshot.values

        return CVGenerationStatus(
            job_id=job_id,
            status=state_values.get("current_step", "queued"),
            created_at=workflow_created_at.get(job_id, datetime.now()),
            completed_at=datetime.now()
            if state_values.get("current_step") in ["completed", "failed"]
            else None,
            error_message=state_values.get("error_message"),
            pdf_path=state_values.get("tailored_cv_pdf_path"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get status for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to get job status: {str(e)}")


@app.get("/api/cv/download/{job_id}")
async def download_cv(job_id: str):
    """
    Download generated CV PDF

    Returns 400 if job not completed
    Returns 404 if PDF file missing
    """
    try:
        # Get status first
        status = await get_cv_status(job_id)

        # Check if job is completed
        if status.status == "failed":
            raise HTTPException(400, f"Job failed: {status.error_message}")

        if status.status != "completed":
            raise HTTPException(400, f"Job not ready yet (status: {status.status})")

        # Check PDF exists
        if not status.pdf_path:
            raise HTTPException(404, "PDF path not set in job state")

        pdf_path = Path(status.pdf_path)
        if not pdf_path.exists():
            raise HTTPException(404, f"PDF file not found: {pdf_path}")

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
        raise HTTPException(500, f"Failed to download CV: {str(e)}")


# =============================================================================
# Unified Job Submission Endpoints
# =============================================================================


def run_preparation_workflow_async(job_id: str, thread_id: str, initial_state: dict):
    """Execute Preparation Workflow in background thread."""
    try:
        config = {"configurable": {"thread_id": thread_id}}
        result = preparation_workflow.invoke(initial_state, config)
        logger.info(
            f"Preparation workflow for job {job_id} completed: {result.get('current_step')}"
        )
    except Exception as e:
        logger.error(f"Preparation workflow for job {job_id} failed: {e}", exc_info=True)


def run_retry_workflow_async(job_id: str, thread_id: str, initial_state: dict):
    """Execute Retry Workflow in background thread."""
    try:
        config = {"configurable": {"thread_id": thread_id}}
        result = retry_workflow.invoke(initial_state, config)
        logger.info(f"Retry workflow for job {job_id} completed: {result.get('current_step')}")
    except Exception as e:
        logger.error(f"Retry workflow for job {job_id} failed: {e}", exc_info=True)


@app.options("/api/jobs/submit")
async def submit_job_options():
    """Handle CORS preflight for job submission."""
    return {}

@app.post("/api/jobs/submit", response_model=JobSubmitResponse)
async def submit_job(
    request: JobSubmitRequest, background_tasks: BackgroundTasks
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
        # Generate unique IDs
        job_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())

        # Validate request
        if request.source == "url" and not request.url:
            raise HTTPException(400, "URL is required for source='url'")
        if request.source == "manual" and not request.job_description:
            raise HTTPException(400, "job_description is required for source='manual'")

        # Build raw_input based on source
        if request.source == "url":
            raw_input = {"url": request.url}
            # If job_description provided, use it as fallback data
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
            }
            logger.info(f"API received template_name: {request.job_description.template_name}")

        # Load master CV
        from src.agents.preparation_workflow import load_master_cv

        master_cv = load_master_cv()

        # Build initial state
        initial_state = {
            "job_id": job_id,
            "source": request.source,
            "mode": request.mode,
            "raw_input": raw_input,
            "master_cv": master_cv,
            "current_step": "queued",
            "retry_count": 0,
            "user_feedback": None,
            "error_message": None,
        }

        # Track thread
        unified_threads[job_id] = {
            "thread_id": thread_id,
            "workflow_type": "preparation",
            "created_at": datetime.now(),
        }

        # Run workflow in background
        background_tasks.add_task(
            run_preparation_workflow_async,
            job_id=job_id,
            thread_id=thread_id,
            initial_state=initial_state,
        )

        logger.info(f"Job {job_id} submitted: source={request.source}, mode={request.mode}")

        return JobSubmitResponse(
            job_id=job_id,
            status="queued",
            message=f"Job submitted successfully. Mode: {request.mode}",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit job: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to submit job: {str(e)}")


@app.get("/api/jobs/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """
    Get status of a submitted job.

    Poll this endpoint every 2-3 seconds until status is 'completed', 'pending', or 'failed'.
    - completed: Ready for download (MVP mode)
    - pending: Awaiting HITL review (Full mode)
    - failed: Job failed with error
    """
    try:
        # Check unified threads first
        if job_id in unified_threads:
            thread_info = unified_threads[job_id]
            thread_id = thread_info["thread_id"]
            config = {"configurable": {"thread_id": thread_id}}

            # Get state from appropriate workflow
            if thread_info["workflow_type"] == "preparation":
                state_snapshot = preparation_workflow.get_state(config)
            elif thread_info["workflow_type"] == "retry":
                state_snapshot = retry_workflow.get_state(config)
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
                updated_at=datetime.now(),
            )

        # Fallback to legacy MVP threads (now also uses preparation_workflow)
        if job_id in workflow_threads:
            thread_id = workflow_threads[job_id]
            config = {"configurable": {"thread_id": thread_id}}
            state_snapshot = preparation_workflow.get_state(config)
            state_values = state_snapshot.values

            return JobStatusResponse(
                job_id=job_id,
                status=state_values.get("current_step", "queued"),
                source=state_values.get("source", "manual"),
                mode=state_values.get("mode", "mvp"),
                job_posting=state_values.get("job_posting"),
                cv_json=state_values.get("tailored_cv_json"),
                pdf_path=state_values.get("tailored_cv_pdf_path"),
                retry_count=state_values.get("retry_count", 0),
                error_message=state_values.get("error_message"),
                created_at=workflow_created_at.get(job_id, datetime.now()),
                updated_at=datetime.now(),
            )

        raise HTTPException(404, f"Job {job_id} not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get status for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to get job status: {str(e)}")


@app.get("/api/jobs/{job_id}/pdf")
async def download_job_pdf(job_id: str):
    """
    Download generated CV PDF for a job.

    Returns 400 if job not completed or pending.
    Returns 404 if PDF file missing.
    """
    try:
        # Get job status
        status = await get_job_status(job_id)

        # Check if job is ready
        if status.status == "failed":
            raise HTTPException(400, f"Job failed: {status.error_message}")

        if status.status not in ["completed", "pending", "pdf_generated"]:
            raise HTTPException(400, f"PDF not ready yet (status: {status.status})")

        # Check PDF exists
        if not status.pdf_path:
            raise HTTPException(404, "PDF path not set in job state")

        pdf_path = Path(status.pdf_path)
        if not pdf_path.exists():
            raise HTTPException(404, f"PDF file not found: {pdf_path}")

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
        raise HTTPException(500, f"Failed to download PDF: {str(e)}")


# =============================================================================
# HITL (Human-in-the-Loop) Endpoints
# =============================================================================


@app.get("/api/hitl/pending", response_model=list[PendingApproval])
async def get_hitl_pending() -> list[PendingApproval]:
    """
    Get all jobs pending HITL review.

    Returns list of jobs with status='pending' for batch review.
    Frontend can display these in a Tinder-like UI for approve/decline/retry.
    """
    try:
        # Get pending jobs from repository
        pending_jobs = await job_repository.get_pending()

        return [
            PendingApproval(
                job_id=job.job_id,
                job_posting=job.job_posting or {},
                cv_json=job.cv_json or {},
                pdf_path=job.pdf_path,
                retry_count=job.retry_count,
                created_at=job.created_at,
            )
            for job in pending_jobs
        ]

    except NotImplementedError:
        # Repository not implemented - return jobs from in-memory tracking
        logger.warning("Repository not implemented, scanning workflow states for pending jobs")
        pending = []

        for job_id, thread_info in unified_threads.items():
            if thread_info["workflow_type"] != "preparation":
                continue

            thread_id = thread_info["thread_id"]
            config = {"configurable": {"thread_id": thread_id}}

            try:
                state_snapshot = preparation_workflow.get_state(config)
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
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to get state for job {job_id}: {e}")

        return pending

    except Exception as e:
        logger.error(f"Failed to get pending jobs: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to get pending jobs: {str(e)}")


@app.post("/api/hitl/{job_id}/decide", response_model=HITLDecisionResponse)
async def submit_hitl_decision(
    job_id: str, decision: HITLDecision, background_tasks: BackgroundTasks
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
        # Validate retry has feedback
        if decision.decision == "retry" and not decision.feedback:
            raise HTTPException(400, "Feedback is required for retry decision")

        # Get current job state
        if job_id not in unified_threads:
            raise HTTPException(404, f"Job {job_id} not found")

        thread_info = unified_threads[job_id]
        thread_id = thread_info["thread_id"]
        config = {"configurable": {"thread_id": thread_id}}

        # Get current state
        state_snapshot = preparation_workflow.get_state(config)
        state_values = state_snapshot.values

        if state_values.get("current_step") != "pending":
            raise HTTPException(
                400,
                f"Job {job_id} is not pending review (status: {state_values.get('current_step')})",
            )

        if decision.decision == "approved":
            # TODO: Trigger Application Workflow (not implemented yet)
            # For now, just update status to "approved"
            logger.info(f"Job {job_id} approved for application (workflow not implemented)")

            # Update tracking
            unified_threads[job_id]["workflow_type"] = "application"

            return HITLDecisionResponse(
                job_id=job_id,
                status="approved",
                message="Job approved. Application workflow not yet implemented.",
            )

        elif decision.decision == "declined":
            logger.info(f"Job {job_id} declined by user")

            # Update status in repository (if implemented)
            try:
                await job_repository.update(job_id, {"status": "declined"})
            except NotImplementedError:
                pass

            return HITLDecisionResponse(
                job_id=job_id,
                status="declined",
                message="Job declined. No further action will be taken.",
            )

        elif decision.decision == "retry":
            logger.info(f"Job {job_id} queued for retry with feedback: {decision.feedback}")

            # Create new thread for retry workflow
            retry_thread_id = str(uuid.uuid4())

            # Build retry state from current state
            retry_state = {
                "job_id": job_id,
                "user_feedback": decision.feedback,
                "job_posting": state_values.get("job_posting"),
                "master_cv": state_values.get("master_cv"),
                "retry_count": state_values.get("retry_count", 0),
                "current_step": "queued",
                "error_message": None,
            }

            # Update tracking
            unified_threads[job_id] = {
                "thread_id": retry_thread_id,
                "workflow_type": "retry",
                "created_at": datetime.now(),
            }

            # Run retry workflow in background
            background_tasks.add_task(
                run_retry_workflow_async,
                job_id=job_id,
                thread_id=retry_thread_id,
                initial_state=retry_state,
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
        raise HTTPException(500, f"Failed to process decision: {str(e)}")


@app.get("/api/hitl/history", response_model=list[ApplicationHistoryItem])
async def get_application_history(
    limit: int = 50, status: str | None = None
) -> list[ApplicationHistoryItem]:
    """
    Get application history.

    Optional filtering by status: approved, declined, applied, failed.
    """
    try:
        statuses = [status] if status else None
        jobs = await job_repository.get_history(limit=limit, statuses=statuses)

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
        raise HTTPException(500, f"Failed to get history: {str(e)}")


# =============================================================================
# Data Cleanup Endpoints
# =============================================================================


@app.delete("/api/jobs/cleanup")
async def cleanup_jobs(
    older_than_days: int = Query(90, ge=1, description="Delete jobs older than this many days"),
    statuses: list[str] = Query(
        ["declined", "failed"], description="Only delete jobs with these statuses"
    ),
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
    try:
        if not statuses:
            raise HTTPException(400, "At least one status must be provided")

        deleted = await job_repository.cleanup(older_than_days, statuses)
        return {"deleted": deleted, "message": f"Deleted {deleted} jobs"}

    except ValueError as e:
        raise HTTPException(400, str(e))

    except Exception as e:
        logger.error(f"Failed to cleanup jobs: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to cleanup jobs: {str(e)}")


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
