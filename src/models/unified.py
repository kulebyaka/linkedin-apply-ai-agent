"""Unified Pydantic models for the two-workflow pipeline architecture.

This module contains models for:
- Job submission (URL/manual sources, MVP/full modes)
- HITL (Human-in-the-Loop) decisions and pending approvals
- Job records for database persistence
- Status responses for API endpoints
"""

from datetime import datetime
from typing import Literal

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
    url: str | None = Field(
        None,
        description="Job posting URL (required if source='url')"
    )
    job_description: JobDescriptionInput | None = Field(
        None,
        description="Manual job description (required if source='manual')"
    )
    application_url: str | None = Field(
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
    feedback: str | None = Field(
        None,
        description="User feedback (required if decision='retry')"
    )


class HITLDecisionResponse(BaseModel):
    """Response after HITL decision submission."""
    job_id: str
    status: Literal["approved", "applying", "declined", "retrying"]
    message: str


class PendingApproval(BaseModel):
    """Job pending HITL approval.

    Returned by GET /api/hitl/pending endpoint for batch review.
    """
    job_id: str
    job_posting: dict = Field(..., description="Normalized job posting data")
    cv_json: dict = Field(..., description="Generated tailored CV as JSON")
    pdf_path: str | None = Field(None, description="Path to generated PDF file")
    retry_count: int = Field(0, description="Number of retry attempts")
    created_at: datetime
    source: Literal["url", "manual", "linkedin"]
    application_url: str | None = None


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
    job_posting: dict | None = None
    raw_input: dict | None = None  # Original URL or manual input

    # CV data
    cv_json: dict | None = None
    pdf_path: str | None = None

    # Application data
    application_url: str | None = None
    application_type: Literal["deep_agent", "linkedin", "manual"] | None = None
    application_result: dict | None = None

    # HITL data
    user_feedback: str | None = None
    retry_count: int = 0

    # Error tracking
    error_message: str | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    applied_at: datetime | None = None


# =============================================================================
# API Response Models
# =============================================================================

class JobStatusResponse(BaseModel):
    """Comprehensive job status response."""
    job_id: str
    source: Literal["url", "manual", "linkedin"] | None = None
    mode: Literal["mvp", "full"] | None = None
    status: str
    job_posting: dict | None = None
    cv_json: dict | None = None
    pdf_path: str | None = None
    application_result: dict | None = None
    error_message: str | None = None
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime
    applied_at: datetime | None = None


class ApplicationHistoryItem(BaseModel):
    """Item in application history list."""
    job_id: str
    job_title: str | None = None
    company: str | None = None
    status: str
    application_type: str | None = None
    applied_at: datetime | None = None
    created_at: datetime
