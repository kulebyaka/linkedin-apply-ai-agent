"""JobRepository abstract base class and shared helpers.

The repository pattern decouples workflow logic from storage implementation.
Concrete implementations live in `in_memory_repository.py` and
`sqlite_repository.py`; this module owns only the contract.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from src.models.cv_attempt import CVCompositionAttempt
from src.models.unified import JobRecord

logger = logging.getLogger(__name__)


def _unlink_pdfs(paths: list[str], job_id: str) -> None:
    """Best-effort PDF file removal. Logs and swallows FileNotFoundError;
    logs but does not raise on other OSError so DB cascade isn't reverted."""
    for raw in paths:
        if not raw:
            continue
        try:
            p = Path(raw)
            p.unlink(missing_ok=True)
            logger.debug("Unlinked PDF %s for job %s", raw, job_id)
        except OSError as exc:
            logger.warning("Failed to unlink PDF %s for job %s: %s", raw, job_id, exc)


UPDATABLE_FIELDS = frozenset({
    "status",
    "workflow_step",
    "job_posting",
    "raw_input",
    "current_cv_json",
    "current_pdf_path",
    "application_url",
    "filter_result",
    "decline_reason",
    "override_reason",
    "refine_signal_state",
    "error_message",
    "scrape_attempts",
    "last_scrape_error",
    "last_scrape_attempt_at",
    "session_authenticated",
    "recovery_attempts",
    "last_recovery_attempt_at",
    "updated_at",
})


class RepositoryError(Exception):
    """Exception raised for repository operations failures."""

    def __init__(self, message: str, job_id: str | None = None):
        self.message = message
        self.job_id = job_id
        super().__init__(self.message)


class JobRepository(ABC):
    """Abstract base class for job repositories.

    Lifecycle:
        1. Create instance: repo = InMemoryJobRepository()
        2. Initialize: await repo.initialize()
        3. Use CRUD/query methods
        4. Close: await repo.close()
    """

    # =========================================================================
    # Lifecycle Methods
    # =========================================================================

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize repository (create tables, connections)."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close repository and release resources."""
        pass

    # =========================================================================
    # CRUD Methods
    # =========================================================================

    @abstractmethod
    async def create(self, job: JobRecord) -> str:
        pass

    @abstractmethod
    async def get(self, job_id: str) -> JobRecord | None:
        pass

    @abstractmethod
    async def update(self, job_id: str, updates: dict) -> None:
        pass

    @abstractmethod
    async def delete(self, job_id: str) -> bool:
        pass

    @abstractmethod
    async def try_claim_failed_for_retry(self, job_id: str) -> "JobRecord | None":
        """Atomic FAILED → QUEUED claim for admin retry; multi-worker safe."""
        pass

    @abstractmethod
    async def try_claim_for_apply(self, job_id: str) -> "JobRecord | None":
        """Atomic {APPROVED, NEEDS_EXTENSION} → APPLYING claim before dispatch.

        Returns the claimed record (now APPLYING) on success, or ``None`` when
        the job is gone or not in a claimable state — including when a concurrent
        request already claimed it. Callers must only dispatch the application
        workflow when this returns non-``None``, so two racing
        ``POST /api/jobs/{id}/apply`` requests coalesce into a single run.
        """
        pass

    @abstractmethod
    async def delete_cascade(self, job_id: str) -> bool:
        """Cascade-delete a job record without ownership check (admin path)."""
        pass

    @abstractmethod
    async def delete_for_user(self, job_id: str, user_id: str) -> bool:
        """Cascade-delete a job record owned by the given user."""
        pass

    # =========================================================================
    # Query Methods
    # =========================================================================

    @abstractmethod
    async def get_for_user(self, job_id: str, user_id: str) -> JobRecord | None:
        pass

    @abstractmethod
    async def get_pending(self, user_id: str) -> list[JobRecord]:
        pass

    @abstractmethod
    async def get_by_status(
        self,
        user_id: str,
        status: str,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "created_at",
        order_desc: bool = True,
    ) -> list[JobRecord]:
        pass

    @abstractmethod
    async def get_all(self, user_id: str, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        pass

    @abstractmethod
    async def get_history(
        self,
        user_id: str,
        limit: int = 50,
        statuses: list[str] | None = None,
    ) -> list[JobRecord]:
        pass

    @abstractmethod
    async def get_status_counts(self, user_id: str) -> dict[str, int]:
        pass

    @abstractmethod
    async def list_by_states(
        self,
        states: list[str],
        *,
        user_id: str | None = None,
        limit: int = 200,
    ) -> list[JobRecord]:
        """Return jobs whose status is in `states`, optionally scoped to a user.

        Used by the HITL in-flight view (user-scoped) and the startup recovery
        scan (`user_id=None`).
        """
        pass

    # =========================================================================
    # Admin-scope Query Methods
    #
    # NOT user-scoped — intended to be called only from admin-gated endpoints.
    # =========================================================================

    @abstractmethod
    async def list_all_jobs(
        self,
        *,
        user_ids: list[str] | None = None,
        statuses: list[str] | None = None,
        sources: list[str] | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[JobRecord]:
        pass

    @abstractmethod
    async def count_all_jobs(
        self,
        *,
        user_ids: list[str] | None = None,
        statuses: list[str] | None = None,
        sources: list[str] | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        search: str | None = None,
    ) -> int:
        pass

    @abstractmethod
    async def count_by_status_global(
        self, window_hours: int | None = None
    ) -> dict[str, int]:
        pass

    @abstractmethod
    async def list_jobs_with_errors(
        self,
        limit: int = 50,
        offset: int = 0,
        since: datetime | None = None,
    ) -> list[JobRecord]:
        pass

    @abstractmethod
    async def get_latest_session_auth(self) -> dict | None:
        """Return the LinkedIn session-auth state from the most recently
        scraped job, or None if no scraped job has recorded it yet.

        Shape: {"authenticated": bool, "job_id": str, "scraped_at": datetime}.
        Used by the admin dashboard to show the current global session state
        (all users share one li_at cookie, so auth is a session-wide property).
        """
        pass

    # =========================================================================
    # Auto-Refinement Signal Methods
    # =========================================================================

    @abstractmethod
    async def list_refine_signals(
        self, user_id: str, state: str, limit: int = 50
    ) -> list[JobRecord]:
        """Return the user's jobs carrying a refine signal in ``state``.

        A "signal" is a job with a non-null decline_reason or override_reason
        and ``refine_signal_state == state``. Newest first, capped at ``limit``.
        """
        pass

    @abstractmethod
    async def mark_refine_signals(self, job_ids: list[str], state: str) -> None:
        """Set ``refine_signal_state`` to ``state`` for the given job ids."""
        pass

    # =========================================================================
    # CV Attempt Methods
    # =========================================================================

    @abstractmethod
    async def create_cv_attempt(self, attempt: CVCompositionAttempt) -> None:
        pass

    @abstractmethod
    async def get_cv_attempts(self, job_id: str) -> list[CVCompositionAttempt]:
        pass

    @abstractmethod
    async def get_latest_cv_attempt(self, job_id: str) -> CVCompositionAttempt | None:
        pass

    # =========================================================================
    # Specialized Methods
    # =========================================================================

    @abstractmethod
    async def find_by_application_url(self, url: str, user_id: str | None = None) -> JobRecord | None:
        pass

    @abstractmethod
    async def cleanup(
        self,
        older_than_days: int,
        statuses: list[str],
        user_id: str | None = None,
    ) -> int:
        pass
