"""Unified Pydantic models for the two-workflow pipeline architecture.

This module contains models for:
- Job submission (URL/manual sources, MVP/full modes)
- HITL (Human-in-the-Loop) decisions and pending approvals
- Job records for database persistence
- Status responses for API endpoints
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from .state_machine import BusinessState, WorkflowStep


class JobDescriptionInput(BaseModel):
    """User input for CV generation."""

    title: str = Field(..., description="Job title")
    company: str = Field(..., description="Company name")
    description: str = Field(..., description="Full job description")
    requirements: str | None = Field(None, description="Job requirements section")
    template_name: str | None = Field(
        None, description="CV template: modern, compact, classic, minimal, profile-card"
    )
    llm_provider: Literal["openai", "anthropic"] | None = Field(
        None, description="LLM provider: openai, anthropic"
    )
    llm_model: str | None = Field(
        None, description="LLM model name (e.g., gpt-4.1-nano, claude-haiku-4.5)"
    )


# =============================================================================
# Job Submission Models
# =============================================================================


class JobSubmitRequest(BaseModel):
    """Unified job submission request.

    Supports multiple sources (URL or manual input) and modes (MVP or full).
    """

    source: Literal["url", "manual"] = Field(
        ..., description="Job source: 'url' for external URLs, 'manual' for textarea input"
    )
    mode: Literal["mvp", "full"] = Field(
        ..., description="Workflow mode: 'mvp' (just PDF) or 'full' (PDF + HITL + apply)"
    )
    url: str | None = Field(None, description="Job posting URL (required if source='url')")
    job_description: JobDescriptionInput | None = Field(
        None, description="Manual job description (required if source='manual')"
    )
    application_url: str | None = Field(
        None, description="URL to apply (defaults to job URL if not provided)"
    )


class JobSubmitResponse(BaseModel):
    """Response after job submission."""

    job_id: str
    status: str = BusinessState.QUEUED
    message: str = "Job submitted successfully"


# =============================================================================
# HITL (Human-in-the-Loop) Models
# =============================================================================


class HITLDecision(BaseModel):
    """User decision from HITL review."""

    decision: Literal["approved", "declined", "retry"] = Field(
        ..., description="User's decision: approve, decline, or retry with feedback"
    )
    feedback: str | None = Field(None, description="User feedback (required if decision='retry')")
    decided_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="Timestamp of the decision",
    )
    reasoning: str | None = Field(None, description="Optional reasoning for the decision")


class HITLDecisionResponse(BaseModel):
    """Response after HITL decision submission."""

    job_id: str
    status: str
    message: str


class PendingApproval(BaseModel):
    """Job pending HITL approval (or any in-flight state the caller asks for).

    Returned by GET /api/hitl/pending endpoint. By default this returns only
    rows whose status == PENDING; callers can pass ?states=... to include
    in-flight statuses (QUEUED, PROCESSING, RETRYING) for the dashboard's
    read-only progress cards.
    """

    job_id: str
    status: str = Field(BusinessState.PENDING, description="Current business state")
    workflow_step: str | None = Field(None, description="Current workflow step (in-flight only)")
    job_posting: dict = Field(..., description="Normalized job posting data")
    cv_json: dict = Field(
        default_factory=dict, description="Generated tailored CV as JSON (empty when in-flight)"
    )
    pdf_path: str | None = Field(None, description="Path to generated PDF file")
    filter_result: dict | None = Field(None, description="LLM filter evaluation result")
    attempt_count: int = Field(0, description="Number of CV composition attempts")
    created_at: datetime
    source: Literal["url", "manual", "linkedin"]
    application_url: str | None = None
    reject_threshold: int = Field(30, description="User's reject threshold for filter score badge")
    warning_threshold: int = Field(
        70, description="User's warning threshold for filter score badge"
    )


# =============================================================================
# Job Record Models (for DB persistence)
# =============================================================================


class PendingQuestion(BaseModel):
    """An Easy Apply field that aborted the apply to ``manual_required``.

    Captured when the deterministic classifier can't fill a field, so the user
    can answer it in-app (the answer is then stored on their ``ApplyProfile``
    and reused on future applications). ``kind`` is set when the field is a
    recognized profile kind whose value is missing (routes the answer to the
    typed ``ApplyProfile`` attribute); ``None`` means the label was unrecognized
    (routes to the generic custom-answers store).
    """

    selector: str
    label: str
    field_type: str = "text"
    options: list[str] = Field(default_factory=list)
    required: bool = False
    kind: str | None = None


class QuestionAnswer(BaseModel):
    """One user-supplied answer to a parked ``PendingQuestion``."""

    label: str
    field_type: str = "text"
    value: str
    options: list[str] = Field(default_factory=list)
    kind: str | None = None  # echoed from the PendingQuestion; routes typed answers


class AnswerQuestionsRequest(BaseModel):
    """Answers to the questions that parked a job in ``manual_required``."""

    answers: list[QuestionAnswer]


class JobRecord(BaseModel):
    """Job record for database persistence.

    This is the main data structure stored in the repository.
    CV composition history is tracked separately via CVCompositionAttempt.
    The current_cv_json and current_pdf_path fields are denormalized
    quick-access copies of the latest CV attempt data.
    """

    job_id: str
    user_id: str = ""
    source: Literal["url", "manual", "linkedin"]
    mode: Literal["mvp", "full"]
    status: BusinessState = Field(default=BusinessState.QUEUED)
    workflow_step: WorkflowStep | None = None

    # Job data
    job_posting: dict | None = None
    raw_input: dict | None = None  # Original URL or manual input

    # Denormalized quick-access to latest CV attempt
    current_cv_json: dict | None = None
    current_pdf_path: str | None = None

    # Application data
    application_url: str | None = None

    # Filter result (from LLM job evaluation)
    filter_result: dict | None = None

    # Auto-refinement signal capture. Populated when the user declines a job
    # (with a reason) at HITL or overrides a filtered-out job via "Proceed
    # Anyway" (with a reason). refine_signal_state tracks once-only consumption
    # by the refiner: pending -> proposed -> consumed.
    decline_reason: str | None = None
    override_reason: str | None = None
    refine_signal_state: str | None = None

    # Error tracking
    error_message: str | None = None

    # Easy Apply fields the classifier couldn't fill (populated on manual_required).
    # The user answers these in-app; answers are saved to their ApplyProfile.
    pending_questions: list[PendingQuestion] | None = None

    # Scrape failure tracking (for retry-eligible records in SCRAPE_FAILED state)
    scrape_attempts: int = 0
    last_scrape_error: str | None = None
    last_scrape_attempt_at: datetime | None = None

    # Observability: whether the LinkedIn session was authenticated when this
    # job was scraped (True/False), or None if unknown/not enriched. The admin
    # dashboard derives the current global session state from the most recently
    # scraped job's value.
    session_authenticated: bool | None = None

    # Startup-recovery tracking — bounded so a poison row can't loop forever.
    recovery_attempts: int = 0
    last_recovery_attempt_at: datetime | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


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
    error_message: str | None = None
    pending_questions: list[PendingQuestion] | None = None
    attempt_count: int = 0
    created_at: datetime
    updated_at: datetime


class ApplicationHistoryItem(BaseModel):
    """Item in application history list."""

    job_id: str
    job_title: str | None = None
    company: str | None = None
    status: str
    created_at: datetime
