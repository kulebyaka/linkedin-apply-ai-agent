"""CV Composition Attempt model for tracking retry history.

Each time a CV is composed (initial or retry), a CVCompositionAttempt
record is created. This allows tracking the full history of CV
compositions for a job, including user feedback for each retry.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class CVCompositionAttempt(BaseModel):
    """A single CV composition attempt for a job.

    Tracks the CV JSON, PDF path, optional user feedback,
    and attempt number for retry history.
    """

    job_id: str
    attempt_number: int = Field(ge=1)
    user_feedback: str | None = None
    cv_json: dict
    pdf_path: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
