"""In-memory JobRepository implementation for development and testing.

Data is lost on restart — use SQLiteJobRepository for persistence.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.models.cv_attempt import CVCompositionAttempt
from src.models.state_machine import BusinessState, validate_transition
from src.models.unified import JobRecord

from .repository import (
    UPDATABLE_FIELDS,
    JobRepository,
    RepositoryError,
    _unlink_pdfs,
)

logger = logging.getLogger(__name__)


class InMemoryJobRepository(JobRepository):
    """In-memory implementation of JobRepository."""

    def __init__(self):
        self._jobs: dict[str, JobRecord] = {}
        self._cv_attempts: dict[str, list[CVCompositionAttempt]] = {}
        self._initialized: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    # =========================================================================
    # Lifecycle Methods
    # =========================================================================

    async def initialize(self) -> None:
        self._initialized = True

    async def close(self) -> None:
        self._jobs.clear()
        self._cv_attempts.clear()
        self._initialized = False

    # =========================================================================
    # CRUD Methods
    # =========================================================================

    async def create(self, job: JobRecord) -> str:
        async with self._lock:
            if job.job_id in self._jobs:
                raise RepositoryError(f"Job already exists: {job.job_id}", job.job_id)
            self._jobs[job.job_id] = job
            return job.job_id

    async def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    async def update(self, job_id: str, updates: dict) -> None:
        async with self._lock:
            if job_id not in self._jobs:
                raise RepositoryError(f"Job not found: {job_id}", job_id)

            invalid_fields = set(updates.keys()) - UPDATABLE_FIELDS
            if invalid_fields:
                raise ValueError(f"Invalid update fields: {invalid_fields}")

            if "status" in updates:
                current_status = self._jobs[job_id].status
                new_status = updates["status"]
                if not isinstance(current_status, BusinessState):
                    current_status = BusinessState(current_status)
                if not isinstance(new_status, BusinessState):
                    new_status = BusinessState(new_status)
                validate_transition(current_status, new_status, job_id)

            updates["updated_at"] = datetime.now(tz=timezone.utc)
            self._jobs[job_id] = self._jobs[job_id].model_copy(update=updates)

    async def delete(self, job_id: str) -> bool:
        async with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                return True
            return False

    async def try_claim_failed_for_retry(self, job_id: str) -> JobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if str(job.status) != BusinessState.FAILED.value:
                return None
            job.status = BusinessState.QUEUED
            job.error_message = None
            job.last_scrape_error = None
            job.updated_at = datetime.now(tz=timezone.utc)
            return job

    async def try_claim_for_apply(self, job_id: str) -> JobRecord | None:
        claimable = (BusinessState.APPROVED, BusinessState.NEEDS_EXTENSION)
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            status = job.status
            if not isinstance(status, BusinessState):
                status = BusinessState(status)
            if status not in claimable:
                return None
            updated = job.model_copy(
                update={
                    "status": BusinessState.APPLYING,
                    "error_message": None,
                    "updated_at": datetime.now(tz=timezone.utc),
                }
            )
            self._jobs[job_id] = updated
            return updated

    async def delete_for_user(self, job_id: str, user_id: str) -> bool:
        pdf_paths: list[str] = []
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.user_id != user_id:
                return False

            if job.current_pdf_path:
                pdf_paths.append(job.current_pdf_path)
            for attempt in self._cv_attempts.get(job_id, []):
                if attempt.pdf_path:
                    pdf_paths.append(attempt.pdf_path)

            del self._jobs[job_id]
            self._cv_attempts.pop(job_id, None)

        _unlink_pdfs(pdf_paths, job_id)
        return True

    async def delete_cascade(self, job_id: str) -> bool:
        pdf_paths: list[str] = []
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False

            if job.current_pdf_path:
                pdf_paths.append(job.current_pdf_path)
            for attempt in self._cv_attempts.get(job_id, []):
                if attempt.pdf_path:
                    pdf_paths.append(attempt.pdf_path)

            del self._jobs[job_id]
            self._cv_attempts.pop(job_id, None)

        _unlink_pdfs(pdf_paths, job_id)
        return True

    # =========================================================================
    # Query Methods
    # =========================================================================

    async def get_for_user(self, job_id: str, user_id: str) -> JobRecord | None:
        job = self._jobs.get(job_id)
        if job and job.user_id == user_id:
            return job
        return None

    async def get_pending(self, user_id: str) -> list[JobRecord]:
        jobs = [
            j for j in self._jobs.values()
            if j.status == BusinessState.PENDING and j.user_id == user_id
        ]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    async def get_by_status(
        self,
        user_id: str,
        status: str,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "created_at",
        order_desc: bool = True,
    ) -> list[JobRecord]:
        jobs = [
            j for j in self._jobs.values()
            if j.status == status and j.user_id == user_id
        ]

        if order_by == "updated_at":
            jobs.sort(key=lambda j: j.updated_at, reverse=order_desc)
        else:
            jobs.sort(key=lambda j: j.created_at, reverse=order_desc)

        return jobs[offset:offset + limit]

    async def get_all(self, user_id: str, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        jobs = sorted(
            [j for j in self._jobs.values() if j.user_id == user_id],
            key=lambda j: j.created_at, reverse=True,
        )
        return jobs[offset:offset + limit]

    async def get_history(
        self,
        user_id: str,
        limit: int = 50,
        statuses: list[str] | None = None,
    ) -> list[JobRecord]:
        jobs = [j for j in self._jobs.values() if j.user_id == user_id]
        if statuses:
            jobs = [j for j in jobs if j.status in statuses]
        jobs.sort(key=lambda j: j.updated_at, reverse=True)
        return jobs[:limit]

    async def list_by_states(
        self,
        states: list[str],
        *,
        user_id: str | None = None,
        limit: int = 200,
    ) -> list[JobRecord]:
        state_values = {str(s) for s in states}
        jobs = [
            j for j in self._jobs.values()
            if str(j.status) in state_values
            and (user_id is None or j.user_id == user_id)
        ]
        jobs.sort(key=lambda j: j.updated_at, reverse=True)
        return jobs[:limit]

    async def get_status_counts(self, user_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for j in self._jobs.values():
            if j.user_id != user_id:
                continue
            key = str(j.status)
            counts[key] = counts.get(key, 0) + 1
        return counts

    async def list_refine_signals(
        self, user_id: str, state: str, limit: int = 50
    ) -> list[JobRecord]:
        jobs = [
            j for j in self._jobs.values()
            if j.user_id == user_id and j.refine_signal_state == state
        ]
        jobs.sort(key=lambda j: j.updated_at, reverse=True)
        return jobs[:limit]

    async def mark_refine_signals(self, job_ids: list[str], state: str) -> None:
        async with self._lock:
            now = datetime.now(tz=timezone.utc)
            for job_id in job_ids:
                job = self._jobs.get(job_id)
                if job is not None:
                    self._jobs[job_id] = job.model_copy(
                        update={"refine_signal_state": state, "updated_at": now}
                    )

    # =========================================================================
    # Admin-scope Query Methods
    # =========================================================================

    def _matches_admin_filters(
        self,
        job: JobRecord,
        *,
        user_ids: list[str] | None,
        statuses: list[str] | None,
        sources: list[str] | None,
        created_from: datetime | None,
        created_to: datetime | None,
        search: str | None,
    ) -> bool:
        if user_ids and job.user_id not in user_ids:
            return False
        if statuses and str(job.status) not in statuses:
            return False
        if sources and job.source not in sources:
            return False
        if created_from and job.created_at < created_from:
            return False
        if created_to and job.created_at > created_to:
            return False
        if search:
            needle = search.lower()
            haystacks: list[str] = []
            if job.job_posting:
                haystacks.append(str(job.job_posting.get("title", "")))
                haystacks.append(str(job.job_posting.get("company", "")))
                haystacks.append(str(job.job_posting.get("description", "")))
            if job.error_message:
                haystacks.append(job.error_message)
            if not any(needle in h.lower() for h in haystacks):
                return False
        return True

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
        matches = [
            j for j in self._jobs.values()
            if self._matches_admin_filters(
                j,
                user_ids=user_ids,
                statuses=statuses,
                sources=sources,
                created_from=created_from,
                created_to=created_to,
                search=search,
            )
        ]
        matches.sort(key=lambda j: j.created_at, reverse=True)
        return matches[offset:offset + limit]

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
        return sum(
            1 for j in self._jobs.values()
            if self._matches_admin_filters(
                j,
                user_ids=user_ids,
                statuses=statuses,
                sources=sources,
                created_from=created_from,
                created_to=created_to,
                search=search,
            )
        )

    async def count_by_status_global(
        self, window_hours: int | None = None
    ) -> dict[str, int]:
        cutoff: datetime | None = None
        if window_hours is not None:
            cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=window_hours)
        counts: dict[str, int] = {}
        for j in self._jobs.values():
            if cutoff is not None and j.created_at < cutoff:
                continue
            key = str(j.status)
            counts[key] = counts.get(key, 0) + 1
        return counts

    async def get_latest_session_auth(self) -> dict | None:
        candidates = [
            j for j in self._jobs.values()
            if j.source == "linkedin" and j.session_authenticated is not None
        ]
        if not candidates:
            return None
        latest = max(candidates, key=lambda j: j.created_at)
        return {
            "authenticated": bool(latest.session_authenticated),
            "job_id": latest.job_id,
            "scraped_at": latest.created_at.isoformat() if latest.created_at else None,
        }

    async def list_jobs_with_errors(
        self,
        limit: int = 50,
        offset: int = 0,
        since: datetime | None = None,
    ) -> list[JobRecord]:
        matches = [
            j for j in self._jobs.values()
            if (j.error_message or j.last_scrape_error)
        ]
        if since is not None:
            since_aware = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
            matches = [j for j in matches if j.updated_at and j.updated_at >= since_aware]
        matches.sort(key=lambda j: j.updated_at, reverse=True)
        return matches[offset:offset + limit]

    # =========================================================================
    # CV Attempt Methods
    # =========================================================================

    async def create_cv_attempt(self, attempt: CVCompositionAttempt) -> None:
        async with self._lock:
            if attempt.job_id not in self._cv_attempts:
                self._cv_attempts[attempt.job_id] = []
            self._cv_attempts[attempt.job_id].append(attempt)

    async def get_cv_attempts(self, job_id: str) -> list[CVCompositionAttempt]:
        attempts = self._cv_attempts.get(job_id, [])
        return sorted(attempts, key=lambda a: a.attempt_number)

    async def get_latest_cv_attempt(self, job_id: str) -> CVCompositionAttempt | None:
        attempts = self._cv_attempts.get(job_id, [])
        if not attempts:
            return None
        return max(attempts, key=lambda a: a.attempt_number)

    # =========================================================================
    # Specialized Methods
    # =========================================================================

    async def find_by_application_url(self, url: str, user_id: str | None = None) -> JobRecord | None:
        for job in self._jobs.values():
            if job.application_url == url and (user_id is None or job.user_id == user_id):
                return job
        return None

    async def cleanup(
        self,
        older_than_days: int,
        statuses: list[str],
        user_id: str | None = None,
    ) -> int:
        if older_than_days < 1:
            raise ValueError("older_than_days must be >= 1")
        if not statuses:
            raise ValueError("statuses list cannot be empty")

        cutoff_date = datetime.now(tz=timezone.utc) - timedelta(days=older_than_days)
        async with self._lock:
            to_delete = [
                job_id
                for job_id, job in self._jobs.items()
                if job.status in statuses
                and job.created_at < cutoff_date
                and (user_id is None or job.user_id == user_id)
            ]

            for job_id in to_delete:
                del self._jobs[job_id]
                self._cv_attempts.pop(job_id, None)

            return len(to_delete)
