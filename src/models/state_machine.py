"""Job lifecycle state machine.

Defines the business states and workflow steps for job processing,
along with transition validation to prevent illegal state changes.
"""

from enum import StrEnum


class WorkflowStep(StrEnum):
    """Tracks which workflow step is currently executing.

    These are transient values used in workflow state (current_step field),
    NOT stored in the database status field.
    """

    EXTRACTING = "extracting"
    JOB_EXTRACTED = "job_extracted"
    FILTERING = "filtering"
    JOB_FILTERED = "job_filtered"
    COMPOSING_CV = "composing_cv"
    CV_COMPOSED = "cv_composed"
    GENERATING_PDF = "generating_pdf"
    PDF_GENERATED = "pdf_generated"
    SAVING = "saving"
    LOADING = "loading"
    LOADED = "loaded"
    APPLYING_DEEP_AGENT = "applying_deep_agent"
    APPLYING_LINKEDIN = "applying_linkedin"
    MANUAL_REQUIRED = "manual_required"


class BusinessState(StrEnum):
    """Job lifecycle business states.

    These are the canonical states stored in the database status field.
    String values are backward-compatible with existing data and UI.
    """

    QUEUED = "queued"
    PROCESSING = "processing"
    CV_READY = "completed"  # MVP mode: PDF ready for download
    PENDING_REVIEW = "pending"  # Full mode: awaiting HITL review
    APPROVED = "approved"
    DECLINED = "declined"
    RETRYING = "retrying"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    FILTERED_OUT = "filtered_out"


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(
        self,
        current: BusinessState,
        target: BusinessState,
        job_id: str | None = None,
    ):
        self.current = current
        self.target = target
        self.job_id = job_id
        msg = f"Invalid state transition: {current.value} → {target.value}"
        if job_id:
            msg = f"Job {job_id}: {msg}"
        super().__init__(msg)


ALLOWED_TRANSITIONS: dict[BusinessState, set[BusinessState]] = {
    BusinessState.QUEUED: {
        BusinessState.PROCESSING,
        BusinessState.CV_READY,
        BusinessState.PENDING_REVIEW,
        BusinessState.FAILED,
        BusinessState.FILTERED_OUT,
    },
    BusinessState.PROCESSING: {
        BusinessState.CV_READY,
        BusinessState.PENDING_REVIEW,
        BusinessState.FAILED,
        BusinessState.FILTERED_OUT,
    },
    BusinessState.CV_READY: set(),  # Terminal (MVP mode)
    BusinessState.PENDING_REVIEW: {
        BusinessState.APPROVED,
        BusinessState.DECLINED,
        BusinessState.RETRYING,
    },
    BusinessState.APPROVED: {
        BusinessState.APPLYING,
        BusinessState.APPLIED,
        BusinessState.FAILED,
    },
    BusinessState.DECLINED: set(),  # Terminal
    BusinessState.RETRYING: {
        BusinessState.PENDING_REVIEW,
        BusinessState.FAILED,
    },
    BusinessState.APPLYING: {
        BusinessState.APPLIED,
        BusinessState.FAILED,
    },
    BusinessState.APPLIED: set(),  # Terminal
    BusinessState.FAILED: {
        BusinessState.RETRYING,
    },
    BusinessState.FILTERED_OUT: set(),  # Terminal
}


def validate_transition(
    current: BusinessState,
    target: BusinessState,
    job_id: str | None = None,
) -> bool:
    """Validate a state transition.

    Self-transitions (current == target) are always allowed for idempotent updates.

    Args:
        current: Current business state.
        target: Desired target state.
        job_id: Optional job ID for error messages.

    Returns:
        True if transition is valid.

    Raises:
        InvalidStateTransitionError: If the transition is not allowed.
    """
    if current == target:
        return True

    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStateTransitionError(current, target, job_id)
    return True
