"""Job Repository - Data Access Layer for job persistence.

This module provides an abstract repository interface and concrete implementations
for storing and retrieving job records.

IMPLEMENTATION STATUS:
- InMemoryJobRepository: Complete implementation for development/testing.
- SQLiteJobRepository: Complete implementation using Piccolo ORM for production.

The repository pattern decouples the workflow logic from storage implementation,
allowing easy switching between in-memory, SQLite, or PostgreSQL backends.

Interface Methods:
- Lifecycle: initialize(), close()
- CRUD: create(), get(), update(), delete()
- Queries: get_pending(), get_by_status(), get_all(), get_history()
- Specialized: find_by_application_url(), cleanup()
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models.cv_attempt import CVCompositionAttempt
from ..models.state_machine import BusinessState, validate_transition
from ..models.unified import JobRecord

logger = logging.getLogger(__name__)


# Valid fields that can be updated via update() method
UPDATABLE_FIELDS = frozenset({
    "status",
    "workflow_step",
    "job_posting",
    "raw_input",
    "current_cv_json",
    "current_pdf_path",
    "application_url",
    "error_message",
    "updated_at",
})


class JobRepository(ABC):
    """Abstract base class for job repositories.

    All repository implementations must provide these methods for
    CRUD operations on JobRecord objects.

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
        """Initialize repository (create tables, connections).

        Must be called once before any other operations.
        For SQLite: creates database file and tables if missing.
        For InMemory: no-op (included for interface consistency).

        Raises:
            RepositoryError: If initialization fails.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close repository and release resources.

        Should be called on application shutdown.
        For SQLite: closes database connections.
        For InMemory: clears data (optional).
        """
        pass

    # =========================================================================
    # CRUD Methods
    # =========================================================================

    @abstractmethod
    async def create(self, job: JobRecord) -> str:
        """Create a new job record.

        Args:
            job: JobRecord to persist.

        Returns:
            The job_id of the created record.

        Raises:
            RepositoryError: If creation fails or job_id already exists.
        """
        pass

    @abstractmethod
    async def get(self, job_id: str) -> JobRecord | None:
        """Get a job record by ID.

        Args:
            job_id: Unique job identifier.

        Returns:
            JobRecord if found, None otherwise.
        """
        pass

    @abstractmethod
    async def update(self, job_id: str, updates: dict) -> None:
        """Update a job record with partial data.

        Automatically sets updated_at timestamp.

        Args:
            job_id: Unique job identifier.
            updates: Dictionary of fields to update. Only fields in
                     UPDATABLE_FIELDS are allowed.

        Raises:
            RepositoryError: If job not found or update fails.
            ValueError: If updates contain invalid field names.
        """
        pass

    @abstractmethod
    async def delete(self, job_id: str) -> bool:
        """Delete a job record.

        Args:
            job_id: Unique job identifier.

        Returns:
            True if deleted, False if not found.
        """
        pass

    # =========================================================================
    # Query Methods
    # =========================================================================

    @abstractmethod
    async def get_for_user(self, job_id: str, user_id: str) -> JobRecord | None:
        """Get a job record by ID, verifying user ownership.

        Args:
            job_id: Unique job identifier.
            user_id: Owner's user ID.

        Returns:
            JobRecord if found and owned by user, None otherwise.
        """
        pass

    @abstractmethod
    async def get_pending(self, user_id: str) -> list[JobRecord]:
        """Get all jobs with status='pending' (awaiting HITL review) for a user.

        Returns jobs ordered by created_at descending (newest first).

        Args:
            user_id: Owner's user ID.

        Returns:
            List of JobRecord objects pending approval.
        """
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
        """Get jobs with a specific status for a user, with pagination.

        Args:
            user_id: Owner's user ID.
            status: Job status to filter by.
            limit: Maximum number of records to return.
            offset: Number of records to skip.
            order_by: Field to sort by ("created_at" or "updated_at").
            order_desc: Sort descending if True, ascending if False.

        Returns:
            List of JobRecord objects with the given status.
        """
        pass

    @abstractmethod
    async def get_all(self, user_id: str, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        """Get all job records for a user with pagination.

        Args:
            user_id: Owner's user ID.
            limit: Maximum number of records to return.
            offset: Number of records to skip.

        Returns:
            List of JobRecord objects, ordered by created_at desc.
        """
        pass

    @abstractmethod
    async def get_history(
        self,
        user_id: str,
        limit: int = 50,
        statuses: list[str] | None = None,
    ) -> list[JobRecord]:
        """Get job application history for a user.

        Args:
            user_id: Owner's user ID.
            limit: Maximum number of records to return.
            statuses: Optional list of statuses to filter by.
                      If None, returns all statuses.

        Returns:
            List of JobRecord objects, ordered by updated_at desc.
        """
        pass

    # =========================================================================
    # CV Attempt Methods
    # =========================================================================

    @abstractmethod
    async def create_cv_attempt(self, attempt: CVCompositionAttempt) -> None:
        """Create a new CV composition attempt record.

        Args:
            attempt: CVCompositionAttempt to persist.

        Raises:
            RepositoryError: If creation fails.
        """
        pass

    @abstractmethod
    async def get_cv_attempts(self, job_id: str) -> list[CVCompositionAttempt]:
        """Get all CV composition attempts for a job, ordered by attempt_number.

        Args:
            job_id: Unique job identifier.

        Returns:
            List of CVCompositionAttempt objects, ordered by attempt_number asc.
        """
        pass

    @abstractmethod
    async def get_latest_cv_attempt(self, job_id: str) -> CVCompositionAttempt | None:
        """Get the most recent CV composition attempt for a job.

        Args:
            job_id: Unique job identifier.

        Returns:
            The latest CVCompositionAttempt, or None if no attempts exist.
        """
        pass

    # =========================================================================
    # Specialized Methods
    # =========================================================================

    @abstractmethod
    async def find_by_application_url(self, url: str, user_id: str | None = None) -> JobRecord | None:
        """Find a job by its application URL.

        Used for duplicate detection - prevents applying to the same
        job posting multiple times.

        Args:
            url: Application URL to search for.
            user_id: If provided, only match jobs belonging to this user.

        Returns:
            JobRecord if found, None otherwise.
        """
        pass

    @abstractmethod
    async def cleanup(
        self,
        older_than_days: int,
        statuses: list[str],
        user_id: str | None = None,
    ) -> int:
        """Delete old jobs matching criteria.

        Used for data retention - removes old declined/failed jobs
        to prevent database bloat.

        Args:
            older_than_days: Delete jobs older than this many days.
            statuses: Only delete jobs with these statuses.
            user_id: If provided, only delete jobs belonging to this user.

        Returns:
            Number of records deleted.

        Raises:
            ValueError: If older_than_days < 1 or statuses is empty.
        """
        pass


class RepositoryError(Exception):
    """Exception raised for repository operations failures."""

    def __init__(self, message: str, job_id: str | None = None):
        self.message = message
        self.job_id = job_id
        super().__init__(self.message)


class InMemoryJobRepository(JobRepository):
    """In-memory implementation of JobRepository.

    Complete implementation for development and testing.
    Data is lost on restart - use SQLiteJobRepository for persistence.
    """

    def __init__(self):
        """Initialize empty in-memory storage."""
        self._jobs: dict[str, JobRecord] = {}
        self._cv_attempts: dict[str, list[CVCompositionAttempt]] = {}
        self._initialized: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    # =========================================================================
    # Lifecycle Methods
    # =========================================================================

    async def initialize(self) -> None:
        """Initialize repository (no-op for in-memory)."""
        self._initialized = True

    async def close(self) -> None:
        """Close repository and clear data."""
        self._jobs.clear()
        self._cv_attempts.clear()
        self._initialized = False

    # =========================================================================
    # CRUD Methods
    # =========================================================================

    async def create(self, job: JobRecord) -> str:
        """Create a new job record in memory.

        Args:
            job: JobRecord to store.

        Returns:
            The job_id.

        Raises:
            RepositoryError: If job_id already exists.
        """
        async with self._lock:
            if job.job_id in self._jobs:
                raise RepositoryError(f"Job already exists: {job.job_id}", job.job_id)
            self._jobs[job.job_id] = job
            return job.job_id

    async def get(self, job_id: str) -> JobRecord | None:
        """Get a job record by ID.

        Args:
            job_id: Unique job identifier.

        Returns:
            JobRecord if found, None otherwise.
        """
        return self._jobs.get(job_id)

    async def update(self, job_id: str, updates: dict) -> None:
        """Update a job record.

        Args:
            job_id: Unique job identifier.
            updates: Fields to update.

        Raises:
            RepositoryError: If job not found.
            ValueError: If updates contain invalid field names.
            InvalidStateTransitionError: If status transition is not allowed.
        """
        async with self._lock:
            if job_id not in self._jobs:
                raise RepositoryError(f"Job not found: {job_id}", job_id)

            # Validate update fields
            invalid_fields = set(updates.keys()) - UPDATABLE_FIELDS
            if invalid_fields:
                raise ValueError(f"Invalid update fields: {invalid_fields}")

            # Validate state transition if status is being changed
            if "status" in updates:
                current_status = self._jobs[job_id].status
                new_status = updates["status"]
                # Coerce to BusinessState for validation
                if not isinstance(current_status, BusinessState):
                    current_status = BusinessState(current_status)
                if not isinstance(new_status, BusinessState):
                    new_status = BusinessState(new_status)
                validate_transition(current_status, new_status, job_id)

            # Auto-set updated_at
            updates["updated_at"] = datetime.now(tz=timezone.utc)
            self._jobs[job_id] = self._jobs[job_id].model_copy(update=updates)

    async def delete(self, job_id: str) -> bool:
        """Delete a job record.

        Args:
            job_id: Unique job identifier.

        Returns:
            True if deleted, False if not found.
        """
        async with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                return True
            return False

    # =========================================================================
    # Query Methods
    # =========================================================================

    async def get_for_user(self, job_id: str, user_id: str) -> JobRecord | None:
        """Get a job record by ID, verifying user ownership."""
        job = self._jobs.get(job_id)
        if job and job.user_id == user_id:
            return job
        return None

    async def get_pending(self, user_id: str) -> list[JobRecord]:
        """Get all pending jobs for a user."""
        jobs = [
            j for j in self._jobs.values()
            if j.status == BusinessState.PENDING_REVIEW and j.user_id == user_id
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
        """Get jobs by status for a user with pagination."""
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
        """Get all jobs for a user with pagination."""
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
        """Get application history for a user."""
        jobs = [j for j in self._jobs.values() if j.user_id == user_id]
        if statuses:
            jobs = [j for j in jobs if j.status in statuses]
        jobs.sort(key=lambda j: j.updated_at, reverse=True)
        return jobs[:limit]

    # =========================================================================
    # CV Attempt Methods
    # =========================================================================

    async def create_cv_attempt(self, attempt: CVCompositionAttempt) -> None:
        """Create a new CV composition attempt record."""
        async with self._lock:
            if attempt.job_id not in self._cv_attempts:
                self._cv_attempts[attempt.job_id] = []
            self._cv_attempts[attempt.job_id].append(attempt)

    async def get_cv_attempts(self, job_id: str) -> list[CVCompositionAttempt]:
        """Get all CV attempts for a job, ordered by attempt_number."""
        attempts = self._cv_attempts.get(job_id, [])
        return sorted(attempts, key=lambda a: a.attempt_number)

    async def get_latest_cv_attempt(self, job_id: str) -> CVCompositionAttempt | None:
        """Get the most recent CV attempt for a job."""
        attempts = self._cv_attempts.get(job_id, [])
        if not attempts:
            return None
        return max(attempts, key=lambda a: a.attempt_number)

    # =========================================================================
    # Specialized Methods
    # =========================================================================

    async def find_by_application_url(self, url: str, user_id: str | None = None) -> JobRecord | None:
        """Find a job by its application URL.

        Args:
            url: Application URL to search for.
            user_id: If provided, only match jobs belonging to this user.

        Returns:
            JobRecord if found, None otherwise.
        """
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
        """Delete old jobs matching criteria.

        Args:
            older_than_days: Delete jobs older than this many days.
            statuses: Only delete jobs with these statuses.
            user_id: If provided, only delete jobs belonging to this user.

        Returns:
            Number of records deleted.

        Raises:
            ValueError: If older_than_days < 1 or statuses is empty.
        """
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


class SQLiteJobRepository(JobRepository):
    """SQLite implementation of JobRepository using Piccolo ORM.

    Production-ready implementation for persistent storage.

    Features:
    - Persistent storage in SQLite database file
    - Auto-initialization of tables on first run
    - Indexed queries for fast status filtering
    - JSON TEXT storage for complex fields (job_posting, cv_json, etc.)
    """

    def __init__(self, db_path: str = "data/jobs.db"):
        """Initialize SQLite repository.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path
        self._initialized: bool = False
        self._engine = None

    # =========================================================================
    # Lifecycle Methods
    # =========================================================================

    async def initialize(self) -> None:
        """Initialize database (create tables if missing).

        Creates the database file and tables on first run.
        Safe to call multiple times.
        """
        from piccolo.engine.sqlite import SQLiteEngine

        from .tables import CVAttemptTable, Job, MagicLinkTable, UserTable

        # Ensure data directory exists
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        # Create engine for this specific database
        self._engine = SQLiteEngine(path=self.db_path)

        # Set the engine on tables for this repository instance
        Job._meta._db = self._engine
        CVAttemptTable._meta._db = self._engine
        UserTable._meta._db = self._engine
        MagicLinkTable._meta._db = self._engine

        # Create tables if they don't exist
        try:
            await UserTable.create_table(if_not_exists=True).run()
            await MagicLinkTable.create_table(if_not_exists=True).run()
            await Job.create_table(if_not_exists=True).run()
            await CVAttemptTable.create_table(if_not_exists=True).run()

            # Migrate legacy schema: rename cv_json -> current_cv_json, pdf_path -> current_pdf_path
            await self._migrate_legacy_columns()

            logger.info(f"SQLite repository initialized at {self.db_path}")
            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize SQLite repository: {e}")
            raise RepositoryError(f"Database initialization failed: {e}") from e

    async def close(self) -> None:
        """Close database connection."""
        if self._engine:
            await self._engine.close_connection_pool()
            self._engine = None
        self._initialized = False
        logger.info("SQLite repository closed")

    def _ensure_initialized(self) -> None:
        """Ensure repository is initialized before operations."""
        if not self._initialized:
            raise RepositoryError("Repository not initialized. Call initialize() first.")

    async def _migrate_legacy_columns(self) -> None:
        """Migrate legacy schema columns if upgrading from an older database.

        Renames cv_json -> current_cv_json and pdf_path -> current_pdf_path
        in the job table if the old columns exist.

        Raises:
            RepositoryError: If migration detection or execution fails.
        """
        conn = await self._engine.get_connection()
        try:
            cursor = await conn.execute("PRAGMA table_info(job)")
            rows = await cursor.fetchall()
            column_names = {row["name"] for row in rows}

            if "cv_json" in column_names and "current_cv_json" not in column_names:
                logger.info("Migrating legacy column: cv_json -> current_cv_json")
                await conn.execute(
                    "ALTER TABLE job RENAME COLUMN cv_json TO current_cv_json"
                )

            if "pdf_path" in column_names and "current_pdf_path" not in column_names:
                logger.info("Migrating legacy column: pdf_path -> current_pdf_path")
                await conn.execute(
                    "ALTER TABLE job RENAME COLUMN pdf_path TO current_pdf_path"
                )
        finally:
            await conn.close()

    # =========================================================================
    # Conversion Helpers
    # =========================================================================

    def _job_record_to_row(self, job: JobRecord) -> dict:
        """Convert Pydantic JobRecord to database row dict."""
        return {
            "job_id": job.job_id,
            "user_id": job.user_id,
            "source": job.source,
            "mode": job.mode,
            "status": job.status,
            "job_posting": job.job_posting,
            "raw_input": job.raw_input,
            "current_cv_json": job.current_cv_json,
            "current_pdf_path": job.current_pdf_path,
            "application_url": job.application_url,
            "error_message": job.error_message,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    def _parse_json_field(self, value) -> dict | None:
        """Parse JSON field which may be string or dict from database."""
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return value

    def _normalize_datetime(self, dt) -> datetime | None:
        """Ensure datetime is UTC-aware. Naive datetimes are assumed UTC."""
        if dt is None:
            return None
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if hasattr(dt, 'replace') and dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def _row_to_job_record(self, row: dict) -> JobRecord:
        """Convert database row dict to Pydantic JobRecord."""
        return JobRecord(
            job_id=row["job_id"],
            user_id=row.get("user_id", ""),
            source=row["source"],
            mode=row["mode"],
            status=row["status"],
            job_posting=self._parse_json_field(row.get("job_posting")),
            raw_input=self._parse_json_field(row.get("raw_input")),
            current_cv_json=self._parse_json_field(row.get("current_cv_json")),
            current_pdf_path=row.get("current_pdf_path"),
            application_url=row.get("application_url"),
            error_message=row.get("error_message"),
            created_at=self._normalize_datetime(row.get("created_at")) or datetime.now(tz=timezone.utc),
            updated_at=self._normalize_datetime(row.get("updated_at")) or datetime.now(tz=timezone.utc),
        )

    def _cv_attempt_to_row(self, attempt: CVCompositionAttempt) -> dict:
        """Convert CVCompositionAttempt to database row dict."""
        return {
            "job_id": attempt.job_id,
            "user_id": attempt.user_id,
            "attempt_number": attempt.attempt_number,
            "user_feedback": attempt.user_feedback,
            "cv_json": attempt.cv_json,
            "pdf_path": attempt.pdf_path,
            "created_at": attempt.created_at,
        }

    def _row_to_cv_attempt(self, row: dict) -> CVCompositionAttempt:
        """Convert database row dict to CVCompositionAttempt."""
        return CVCompositionAttempt(
            job_id=row["job_id"],
            user_id=row.get("user_id", ""),
            attempt_number=row["attempt_number"],
            user_feedback=row.get("user_feedback"),
            cv_json=self._parse_json_field(row.get("cv_json")) or {},
            pdf_path=row.get("pdf_path"),
            created_at=self._normalize_datetime(row.get("created_at")) or datetime.now(tz=timezone.utc),
        )

    # =========================================================================
    # CRUD Methods
    # =========================================================================

    async def create(self, job: JobRecord) -> str:
        """Create a new job record in SQLite.

        Args:
            job: JobRecord to store.

        Returns:
            The job_id.

        Raises:
            RepositoryError: If job_id already exists.
        """
        self._ensure_initialized()
        from .tables import Job

        # Check for duplicate
        existing = await Job.select().where(Job.job_id == job.job_id).first().run()
        if existing:
            raise RepositoryError(f"Job already exists: {job.job_id}", job.job_id)

        # Insert new record
        row_data = self._job_record_to_row(job)
        await Job.insert(Job(**row_data)).run()

        logger.debug(f"Created job {job.job_id}")
        return job.job_id

    async def get(self, job_id: str) -> JobRecord | None:
        """Get a job record by ID.

        Args:
            job_id: Unique job identifier.

        Returns:
            JobRecord if found, None otherwise.
        """
        self._ensure_initialized()
        from .tables import Job

        row = await Job.select().where(Job.job_id == job_id).first().run()
        if not row:
            return None

        return self._row_to_job_record(row)

    async def update(self, job_id: str, updates: dict) -> None:
        """Update a job record.

        Args:
            job_id: Unique job identifier.
            updates: Fields to update.

        Raises:
            RepositoryError: If job not found.
            ValueError: If updates contain invalid field names.
            InvalidStateTransitionError: If status transition is not allowed.
        """
        self._ensure_initialized()
        from .tables import Job

        # Check job exists
        existing = await Job.select().where(Job.job_id == job_id).first().run()
        if not existing:
            raise RepositoryError(f"Job not found: {job_id}", job_id)

        # Validate update fields
        invalid_fields = set(updates.keys()) - UPDATABLE_FIELDS
        if invalid_fields:
            raise ValueError(f"Invalid update fields: {invalid_fields}")

        # Validate state transition if status is being changed
        if "status" in updates:
            current_status = existing["status"]
            new_status = updates["status"]
            if not isinstance(current_status, BusinessState):
                current_status = BusinessState(current_status)
            if not isinstance(new_status, BusinessState):
                new_status = BusinessState(new_status)
            validate_transition(current_status, new_status, job_id)

        # Auto-set updated_at
        updates["updated_at"] = datetime.now(tz=timezone.utc)

        # Build update query
        update_query = Job.update(updates).where(Job.job_id == job_id)
        await update_query.run()

        logger.debug(f"Updated job {job_id}: {list(updates.keys())}")

    async def delete(self, job_id: str) -> bool:
        """Delete a job record.

        Args:
            job_id: Unique job identifier.

        Returns:
            True if deleted, False if not found.
        """
        self._ensure_initialized()
        from .tables import Job

        # Check if exists
        existing = await Job.select().where(Job.job_id == job_id).first().run()
        if not existing:
            return False

        await Job.delete().where(Job.job_id == job_id).run()
        logger.debug(f"Deleted job {job_id}")
        return True

    # =========================================================================
    # Query Methods
    # =========================================================================

    async def get_for_user(self, job_id: str, user_id: str) -> JobRecord | None:
        """Get a job record by ID, verifying user ownership."""
        self._ensure_initialized()
        from .tables import Job

        row = (
            await Job.select()
            .where(Job.job_id == job_id)
            .where(Job.user_id == user_id)
            .first()
            .run()
        )
        if not row:
            return None
        return self._row_to_job_record(row)

    async def get_pending(self, user_id: str) -> list[JobRecord]:
        """Get all pending jobs for a user."""
        self._ensure_initialized()
        from .tables import Job

        rows = (
            await Job.select()
            .where(Job.status == BusinessState.PENDING_REVIEW)
            .where(Job.user_id == user_id)
            .order_by(Job.created_at, ascending=False)
            .run()
        )

        return [self._row_to_job_record(row) for row in rows]

    async def get_by_status(
        self,
        user_id: str,
        status: str,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "created_at",
        order_desc: bool = True,
    ) -> list[JobRecord]:
        """Get jobs by status for a user with pagination."""
        self._ensure_initialized()
        from .tables import Job

        order_column = Job.updated_at if order_by == "updated_at" else Job.created_at

        rows = (
            await Job.select()
            .where(Job.status == status)
            .where(Job.user_id == user_id)
            .order_by(order_column, ascending=not order_desc)
            .limit(limit)
            .offset(offset)
            .run()
        )

        return [self._row_to_job_record(row) for row in rows]

    async def get_all(self, user_id: str, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        """Get all jobs for a user with pagination."""
        self._ensure_initialized()
        from .tables import Job

        rows = (
            await Job.select()
            .where(Job.user_id == user_id)
            .order_by(Job.created_at, ascending=False)
            .limit(limit)
            .offset(offset)
            .run()
        )

        return [self._row_to_job_record(row) for row in rows]

    async def get_history(
        self,
        user_id: str,
        limit: int = 50,
        statuses: list[str] | None = None,
    ) -> list[JobRecord]:
        """Get application history for a user."""
        self._ensure_initialized()
        from .tables import Job

        query = (
            Job.select()
            .where(Job.user_id == user_id)
            .order_by(Job.updated_at, ascending=False)
            .limit(limit)
        )

        if statuses:
            query = query.where(Job.status.is_in(statuses))

        rows = await query.run()
        return [self._row_to_job_record(row) for row in rows]

    # =========================================================================
    # CV Attempt Methods
    # =========================================================================

    async def create_cv_attempt(self, attempt: CVCompositionAttempt) -> None:
        """Create a new CV composition attempt record."""
        self._ensure_initialized()
        from .tables import CVAttemptTable

        row_data = self._cv_attempt_to_row(attempt)
        await CVAttemptTable.insert(CVAttemptTable(**row_data)).run()
        logger.debug(
            f"Created CV attempt {attempt.attempt_number} for job {attempt.job_id}"
        )

    async def get_cv_attempts(self, job_id: str) -> list[CVCompositionAttempt]:
        """Get all CV attempts for a job, ordered by attempt_number."""
        self._ensure_initialized()
        from .tables import CVAttemptTable

        rows = (
            await CVAttemptTable.select()
            .where(CVAttemptTable.job_id == job_id)
            .order_by(CVAttemptTable.attempt_number, ascending=True)
            .run()
        )
        return [self._row_to_cv_attempt(row) for row in rows]

    async def get_latest_cv_attempt(self, job_id: str) -> CVCompositionAttempt | None:
        """Get the most recent CV attempt for a job."""
        self._ensure_initialized()
        from .tables import CVAttemptTable

        row = (
            await CVAttemptTable.select()
            .where(CVAttemptTable.job_id == job_id)
            .order_by(CVAttemptTable.attempt_number, ascending=False)
            .first()
            .run()
        )
        if not row:
            return None
        return self._row_to_cv_attempt(row)

    # =========================================================================
    # Specialized Methods
    # =========================================================================

    async def find_by_application_url(self, url: str, user_id: str | None = None) -> JobRecord | None:
        """Find a job by its application URL.

        Args:
            url: Application URL to search for.
            user_id: If provided, only match jobs belonging to this user.

        Returns:
            JobRecord if found, None otherwise.
        """
        self._ensure_initialized()
        from .tables import Job

        query = Job.select().where(Job.application_url == url)
        if user_id is not None:
            query = query.where(Job.user_id == user_id)
        row = await query.first().run()
        if not row:
            return None

        return self._row_to_job_record(row)

    async def cleanup(
        self,
        older_than_days: int,
        statuses: list[str],
        user_id: str | None = None,
    ) -> int:
        """Delete old jobs matching criteria.

        Args:
            older_than_days: Delete jobs older than this many days.
            statuses: Only delete jobs with these statuses.
            user_id: If provided, only delete jobs belonging to this user.

        Returns:
            Number of records deleted.

        Raises:
            ValueError: If older_than_days < 1 or statuses is empty.
        """
        self._ensure_initialized()
        from .tables import CVAttemptTable, Job

        if older_than_days < 1:
            raise ValueError("older_than_days must be >= 1")
        if not statuses:
            raise ValueError("statuses list cannot be empty")

        cutoff_date = datetime.now(tz=timezone.utc) - timedelta(days=older_than_days)

        # Build query with optional user_id filter
        query = Job.select(Job.job_id).where(Job.status.is_in(statuses)).where(Job.created_at < cutoff_date)
        if user_id is not None:
            query = query.where(Job.user_id == user_id)

        to_delete = await query.run()
        count = len(to_delete)

        if count > 0:
            job_ids = [row["job_id"] for row in to_delete]
            await (
                CVAttemptTable.delete()
                .where(CVAttemptTable.job_id.is_in(job_ids))
                .run()
            )
            delete_query = Job.delete().where(Job.job_id.is_in(job_ids))
            await delete_query.run()

        logger.info(f"Cleanup: deleted {count} jobs older than {older_than_days} days")
        return count


def get_repository(repo_type: str = "memory", **kwargs) -> JobRepository:
    """Factory function to get a repository instance.

    Args:
        repo_type: Type of repository ("memory" or "sqlite").
        **kwargs: Additional arguments for repository constructor.

    Returns:
        JobRepository instance.

    Raises:
        ValueError: If repo_type is not recognized.
    """
    repositories = {
        "memory": InMemoryJobRepository,
        "sqlite": SQLiteJobRepository,
    }

    if repo_type not in repositories:
        raise ValueError(
            f"Unknown repository type: {repo_type}. "
            f"Supported: {list(repositories.keys())}"
        )

    # Only pass db_path to SQLite repository
    if repo_type == "memory":
        kwargs.pop("db_path", None)

    return repositories[repo_type](**kwargs)
