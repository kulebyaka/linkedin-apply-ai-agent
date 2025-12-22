"""FastAPI application for HITL UI"""

import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config.settings import get_settings
from src.models.application import UserApproval, ApplicationStatus
from src.models.job import JobPosting
from src.models.cv import TailoredCV
from src.models.mvp import JobDescriptionInput, CVGenerationResponse, CVGenerationStatus
from src.agents.mvp_workflow import create_mvp_workflow, load_master_cv

settings = get_settings()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize LangGraph workflow (singleton)
mvp_workflow = create_mvp_workflow()

# In-memory thread tracking (maps job_id -> thread_id)
# For MVP - would use Redis/PostgreSQL in production
workflow_threads: Dict[str, str] = {}
workflow_created_at: Dict[str, datetime] = {}  # Track creation time

app = FastAPI(
    title="LinkedIn Job Application Agent API",
    description="API for Human-in-the-Loop job application review",
    version="0.1.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def run_workflow_async(job_id: str, thread_id: str, initial_state: dict):
    """Execute LangGraph workflow in background thread"""
    try:
        config = {"configurable": {"thread_id": thread_id}}

        # Invoke workflow (blocking call in background thread)
        result = mvp_workflow.invoke(initial_state, config)

        logger.info(f"Job {job_id} completed with status: {result.get('current_step')}")

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        # Error is stored in workflow state, no need to re-raise


@app.get("/")
async def root():
    """Health check endpoint"""
    return {"status": "running", "message": "LinkedIn Job Application Agent API"}


@app.get("/api/pending-approvals", response_model=List[dict])
async def get_pending_approvals():
    """
    Get list of job applications pending user approval

    Returns list of jobs with their tailored CVs awaiting review
    """
    # TODO: Implement fetching pending approvals from workflow state
    raise HTTPException(status_code=501, detail="Not implemented")


@app.get("/api/jobs/{job_id}")
async def get_job_details(job_id: str):
    """Get detailed information about a specific job"""
    # TODO: Implement job details retrieval
    raise HTTPException(status_code=501, detail="Not implemented")


@app.get("/api/cv/{job_id}")
async def get_tailored_cv(job_id: str):
    """Get the tailored CV for a specific job"""
    # TODO: Implement CV retrieval
    raise HTTPException(status_code=501, detail="Not implemented")


@app.post("/api/approve/{job_id}")
async def approve_application(job_id: str, approval: UserApproval):
    """
    Submit user approval decision for a job application

    Supports: approved, declined, retry
    """
    # TODO: Implement approval submission to workflow
    # This should resume the paused LangGraph workflow
    raise HTTPException(status_code=501, detail="Not implemented")


@app.get("/api/applications/history")
async def get_application_history():
    """Get history of all job applications"""
    # TODO: Implement application history retrieval
    raise HTTPException(status_code=501, detail="Not implemented")


@app.get("/api/stats")
async def get_statistics():
    """Get application statistics"""
    # TODO: Implement statistics
    # - Total jobs fetched
    # - Filtered out
    # - Pending approval
    # - Approved
    # - Declined
    # - Successfully submitted
    raise HTTPException(status_code=501, detail="Not implemented")


# MVP CV Generation Endpoints

@app.post("/api/cv/generate", response_model=CVGenerationResponse)
async def generate_cv(
    job_input: JobDescriptionInput,
    background_tasks: BackgroundTasks
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

        # Initialize workflow state
        initial_state = {
            "job_id": job_id,
            "job_posting": {
                "title": job_input.title,
                "company": job_input.company,
                "description": job_input.description,
                "requirements": job_input.requirements or ""
            },
            "master_cv": master_cv,
            "current_step": "queued"
        }

        # Run workflow in background
        background_tasks.add_task(
            run_workflow_async,
            job_id=job_id,
            thread_id=thread_id,
            initial_state=initial_state
        )

        logger.info(f"Job {job_id} submitted: {job_input.title} at {job_input.company}")

        return CVGenerationResponse(
            job_id=job_id,
            status="queued",
            message="CV generation job submitted successfully"
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
        # Check if job exists
        if job_id not in workflow_threads:
            raise HTTPException(404, f"Job {job_id} not found")

        thread_id = workflow_threads[job_id]
        config = {"configurable": {"thread_id": thread_id}}

        # Get current state from LangGraph checkpointer
        state_snapshot = mvp_workflow.get_state(config)
        state_values = state_snapshot.values

        return CVGenerationStatus(
            job_id=job_id,
            status=state_values.get("current_step", "queued"),
            created_at=workflow_created_at.get(job_id, datetime.now()),
            completed_at=datetime.now() if state_values.get("current_step") in ["completed", "failed"] else None,
            error_message=state_values.get("error_message"),
            pdf_path=state_values.get("tailored_cv_pdf_path")
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
            headers={
                "Content-Disposition": f'attachment; filename="{pdf_path.name}"'
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download CV for job {job_id}: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to download CV: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
