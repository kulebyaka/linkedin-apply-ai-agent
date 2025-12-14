"""Job application data models"""

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel


class ApplicationStatus(BaseModel):
    """Job application status"""
    job_id: str
    status: Literal[
        "pending_filter",
        "filtered_out",
        "cv_generation",
        "pending_approval",
        "approved",
        "declined",
        "applying",
        "submitted",
        "failed"
    ]
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()
    error_message: Optional[str] = None


class UserApproval(BaseModel):
    """User approval for job application"""
    job_id: str
    decision: Literal["approved", "declined", "retry"]
    feedback: Optional[str] = None
    timestamp: datetime = datetime.now()


class ApplicationResult(BaseModel):
    """Result of job application attempt"""
    job_id: str
    success: bool
    message: str
    applied_at: Optional[datetime] = None
    error_details: Optional[dict] = None
