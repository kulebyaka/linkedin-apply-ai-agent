"""Job lifecycle state machine.

Defines the business states and workflow steps for job processing,
along with transition validation to prevent illegal state changes.
"""

from enum import StrEnum


class WorkflowStep(StrEnum):
    """Tracks which workflow step is currently executing.

    These are transient values used in workflow state (``current_step`` field),
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
    String values are kept stable for backward compatibility with existing
    data and frontend code — historic mismatches between the Python member
    name and the stored value (notably ``COMPLETED = "completed"`` which
    represents "CV is ready / MVP mode complete", and ``PENDING = "pending"``
    which means "pending HITL review") are preserved by design.
    """

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"  # MVP mode: CV PDF ready for download
    PENDING = "pending"  # Full mode: awaiting HITL review
    APPROVED = "approved"
    DECLINED = "declined"
    RETRYING = "retrying"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    FILTERED_OUT = "filtered_out"
    SCRAPE_FAILED = "scrape_failed"  # Description missing/empty — retry-eligible
    MANUAL_REQUIRED = "manual_required"  # Apply aborted (unknown field) — finish by hand
    NEEDS_EXTENSION = "needs_extension"  # No connected extension — recoverable, user re-triggers

    def is_terminal(self) -> bool:
        """True if no further transitions are allowed from this state."""
        return self in TERMINAL_STATES


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
        BusinessState.COMPLETED,
        BusinessState.PENDING,
        BusinessState.APPROVED,  # auto_apply: skip HITL, go straight to apply
        BusinessState.FAILED,
        BusinessState.FILTERED_OUT,
        BusinessState.SCRAPE_FAILED,
    },
    BusinessState.PROCESSING: {
        BusinessState.COMPLETED,
        BusinessState.PENDING,
        BusinessState.APPROVED,  # auto_apply: skip HITL, go straight to apply
        BusinessState.FAILED,
        BusinessState.FILTERED_OUT,
        BusinessState.SCRAPE_FAILED,
    },
    BusinessState.COMPLETED: set(),  # Terminal (MVP mode)
    BusinessState.PENDING: {
        BusinessState.APPROVED,
        BusinessState.DECLINED,
        BusinessState.RETRYING,
    },
    BusinessState.APPROVED: {
        BusinessState.APPLYING,
        BusinessState.APPLIED,
        BusinessState.FAILED,
        BusinessState.NEEDS_EXTENSION,
        BusinessState.MANUAL_REQUIRED,
    },
    BusinessState.DECLINED: set(),  # Terminal
    BusinessState.RETRYING: {
        BusinessState.PENDING,
        BusinessState.FAILED,
    },
    BusinessState.APPLYING: {
        BusinessState.APPLIED,
        BusinessState.FAILED,
        BusinessState.MANUAL_REQUIRED,
    },
    BusinessState.APPLIED: set(),  # Terminal
    BusinessState.FAILED: {
        BusinessState.RETRYING,
        BusinessState.QUEUED,  # Admin-initiated retry re-enqueues the job
    },
    BusinessState.FILTERED_OUT: {
        # User override: "Proceed Anyway" re-enters CV generation, skipping the filter.
        BusinessState.PROCESSING,
    },
    BusinessState.SCRAPE_FAILED: {
        BusinessState.QUEUED,
        BusinessState.PROCESSING,
        BusinessState.SCRAPE_FAILED,  # Idempotent re-attempts increment counter
        BusinessState.COMPLETED,
        BusinessState.PENDING,
        BusinessState.FAILED,  # Cap exhausted
        BusinessState.FILTERED_OUT,
    },
    BusinessState.NEEDS_EXTENSION: {
        # Recoverable: once the extension connects, the user re-triggers apply.
        BusinessState.APPLYING,
        BusinessState.FAILED,
    },
    BusinessState.MANUAL_REQUIRED: set(),  # Terminal — finished by hand on LinkedIn
}


TERMINAL_STATES: frozenset[BusinessState] = frozenset(
    state for state, targets in ALLOWED_TRANSITIONS.items() if not targets
)


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
