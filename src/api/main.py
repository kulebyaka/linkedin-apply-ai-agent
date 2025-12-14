"""FastAPI application for HITL UI"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import List
from src.config.settings import get_settings
from src.models.application import UserApproval, ApplicationStatus
from src.models.job import JobPosting
from src.models.cv import TailoredCV

settings = get_settings()

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
