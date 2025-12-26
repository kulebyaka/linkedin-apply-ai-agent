"""Unified Pydantic models for the two-workflow pipeline architecture.

This module contains models for:
- Job submission (URL/manual sources, MVP/full modes)
- HITL (Human-in-the-Loop) decisions and pending approvals
- Job records for database persistence
- Status responses for API endpoints
"""

from datetime import datetime
from typing import Optional, Literal, Any
from pydantic import BaseModel, Field

from .mvp import JobDescriptionInput


# =============================================================================
# Job Submission Models
# =============================================================================

class JobSubmitRequest(BaseModel):
    """Unified job submission request.

    Supports multiple sources (URL or manual input) and modes (MVP or full).
    """
    source: Literal["url", "manual"] = Field(
        ...,
        description="Job source: 'url' for external URLs, 'manual' for textarea input"
    )
    mode: Literal["mvp", "full"] = Field(
        ...,
        description="Workflow mode: 'mvp' (just PDF) or 'full' (PDF + HITL + apply)"
    )
    url: Optional[str] = Field(
        None,
        description="Job posting URL (required if source='url')"
    )
    job_description: Optional[JobDescriptionInput] = Field(
        None,
        description="Manual job description (required if source='manual')"
    )
    application_url: Optional[str] = Field(
        None,
        description="URL to apply (defaults to job URL if not provided)"
    )


class JobSubmitResponse(BaseModel):
    """Response after job submission."""
    job_id: str
    status: Literal["queued"] = "queued"
    message: str = "Job submitted successfully"


# =============================================================================
# HITL (Human-in-the-Loop) Models
# =============================================================================

class HITLDecision(BaseModel):
    """User decision from HITL review."""
    decision: Literal["approved", "declined", "retry"] = Field(
        ...,
        description="User's decision: approve, decline, or retry with feedback"
    )
    feedback: Optional[str] = Field(
        None,
        description="User feedback (required if decision='retry')"
    )


class HITLDecisionResponse(BaseModel):
    """Response after HITL decision submission."""
    job_id: str
    status: Literal["applying", "declined", "retrying"]
    message: str


class PendingApproval(BaseModel):
    """Job pending HITL approval.

    Returned by GET /api/hitl/pending endpoint for batch review.
    """
    job_id: str
    job_posting: dict = Field(..., description="Normalized job posting data")
    cv_json: dict = Field(..., description="Generated tailored CV as JSON")
    pdf_path: str = Field(..., description="Path to generated PDF file")
    retry_count: int = Field(0, description="Number of retry attempts")
    created_at: datetime
    source: Literal["url", "manual", "linkedin"]
    application_url: Optional[str] = None


# =============================================================================
# Job Record Models (for DB persistence)
# =============================================================================

class JobStatus(str):
    """Job status enum values."""
    QUEUED = "queued"
    EXTRACTING = "extracting"
    FILTERING = "filtering"
    COMPOSING_CV = "composing_cv"
    GENERATING_PDF = "generating_pdf"
    PENDING = "pending"  # Waiting for HITL
    APPROVED = "approved"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    DECLINED = "declined"
    COMPLETED = "completed"  # MVP mode - PDF ready for download


class JobRecord(BaseModel):
    """Job record for database persistence.

    This is the main data structure stored in the repository.
    """
    job_id: str
    source: Literal["url", "manual", "linkedin"]
    mode: Literal["mvp", "full"]
    status: str = Field(default="queued")

    # Job data
    job_posting: Optional[dict] = None
    raw_input: Optional[dict] = None  # Original URL or manual input

    # CV data
    cv_json: Optional[dict] = None
    pdf_path: Optional[str] = None

    # Application data
    application_url: Optional[str] = None
    application_type: Optional[Literal["deep_agent", "linkedin", "manual"]] = None
    application_result: Optional[dict] = None

    # HITL data
    user_feedback: Optional[str] = None
    retry_count: int = 0

    # Error tracking
    error_message: Optional[str] = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    applied_at: Optional[datetime] = None


# =============================================================================
# API Response Models
# =============================================================================

class JobStatusResponse(BaseModel):
    """Comprehensive job status response."""
    job_id: str
    source: Literal["url", "manual", "linkedin"]
    mode: Literal["mvp", "full"]
    status: str
    job_posting: Optional[dict] = None
    pdf_path: Optional[str] = None
    application_result: Optional[dict] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime
    applied_at: Optional[datetime] = None


class ApplicationHistoryItem(BaseModel):
    """Item in application history list."""
    job_id: str
    job_title: Optional[str] = None
    company: Optional[str] = None
    status: str
    application_type: Optional[str] = None
    applied_at: Optional[datetime] = None
    created_at: datetime
