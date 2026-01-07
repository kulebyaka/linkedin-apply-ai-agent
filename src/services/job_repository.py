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

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..models.unified import JobRecord

logger = logging.getLogger(__name__)


# Valid fields that can be updated via update() method
UPDATABLE_FIELDS = frozenset({
    "status",
    "job_posting",
    "raw_input",
    "cv_json",
    "pdf_path",
    "application_url",
    "application_type",
    "application_result",
    "user_feedback",
    "retry_count",
    "error_message",
    "applied_at",
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
    async def get(self, job_id: str) -> Optional[JobRecord]:
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
    async def get_pending(self) -> list[JobRecord]:
        """Get all jobs with status='pending' (awaiting HITL review).

        Convenience method equivalent to get_by_status("pending").
        Returns jobs ordered by created_at descending (newest first).

        Returns:
            List of JobRecord objects pending approval.
        """
        pass

    @abstractmethod
    async def get_by_status(
        self,
        status: str,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "created_at",
        order_desc: bool = True,
    ) -> list[JobRecord]:
        """Get jobs with a specific status, with pagination.

        Args:
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
    async def get_all(self, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        """Get all job records with pagination.

        Args:
            limit: Maximum number of records to return.
            offset: Number of records to skip.

        Returns:
            List of JobRecord objects, ordered by created_at desc.
        """
        pass

    @abstractmethod
    async def get_history(
        self,
        limit: int = 50,
        statuses: Optional[list[str]] = None,
    ) -> list[JobRecord]:
        """Get job application history.

        Args:
            limit: Maximum number of records to return.
            statuses: Optional list of statuses to filter by.
                      If None, returns all statuses.

        Returns:
            List of JobRecord objects, ordered by updated_at desc.
        """
        pass

    # =========================================================================
    # Specialized Methods
    # =========================================================================

    @abstractmethod
    async def find_by_application_url(self, url: str) -> Optional[JobRecord]:
        """Find a job by its application URL.

        Used for duplicate detection - prevents applying to the same
        job posting multiple times.

        Args:
            url: Application URL to search for.

        Returns:
            JobRecord if found, None otherwise.
        """
        pass

    @abstractmethod
    async def cleanup(
        self,
        older_than_days: int,
        statuses: list[str],
    ) -> int:
        """Delete old jobs matching criteria.

        Used for data retention - removes old declined/failed jobs
        to prevent database bloat.

        Args:
            older_than_days: Delete jobs older than this many days.
            statuses: Only delete jobs with these statuses.

        Returns:
            Number of records deleted.

        Raises:
            ValueError: If older_than_days < 1 or statuses is empty.
        """
        pass


class RepositoryError(Exception):
    """Exception raised for repository operations failures."""

    def __init__(self, message: str, job_id: Optional[str] = None):
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
        self._initialized: bool = False

    # =========================================================================
    # Lifecycle Methods
    # =========================================================================

    async def initialize(self) -> None:
        """Initialize repository (no-op for in-memory)."""
        self._initialized = True

    async def close(self) -> None:
        """Close repository and clear data."""
        self._jobs.clear()
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
        if job.job_id in self._jobs:
            raise RepositoryError(f"Job already exists: {job.job_id}", job.job_id)
        self._jobs[job.job_id] = job
        return job.job_id

    async def get(self, job_id: str) -> Optional[JobRecord]:
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
        """
        if job_id not in self._jobs:
            raise RepositoryError(f"Job not found: {job_id}", job_id)

        # Validate update fields
        invalid_fields = set(updates.keys()) - UPDATABLE_FIELDS
        if invalid_fields:
            raise ValueError(f"Invalid update fields: {invalid_fields}")

        # Auto-set updated_at
        updates["updated_at"] = datetime.now()
        self._jobs[job_id] = self._jobs[job_id].model_copy(update=updates)

    async def delete(self, job_id: str) -> bool:
        """Delete a job record.

        Args:
            job_id: Unique job identifier.

        Returns:
            True if deleted, False if not found.
        """
        if job_id in self._jobs:
            del self._jobs[job_id]
            return True
        return False

    # =========================================================================
    # Query Methods
    # =========================================================================

    async def get_pending(self) -> list[JobRecord]:
        """Get all pending jobs.

        Returns:
            List of jobs with status='pending', sorted by created_at desc.
        """
        jobs = [j for j in self._jobs.values() if j.status == "pending"]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    async def get_by_status(
        self,
        status: str,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "created_at",
        order_desc: bool = True,
    ) -> list[JobRecord]:
        """Get jobs by status with pagination.

        Args:
            status: Status to filter by.
            limit: Max records.
            offset: Records to skip.
            order_by: Field to sort by.
            order_desc: Sort descending if True.

        Returns:
            List of matching jobs.
        """
        jobs = [j for j in self._jobs.values() if j.status == status]

        # Sort by specified field
        if order_by == "updated_at":
            jobs.sort(key=lambda j: j.updated_at, reverse=order_desc)
        else:  # default to created_at
            jobs.sort(key=lambda j: j.created_at, reverse=order_desc)

        return jobs[offset:offset + limit]

    async def get_all(self, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        """Get all jobs with pagination.

        Args:
            limit: Max records.
            offset: Records to skip.

        Returns:
            List of jobs sorted by created_at desc.
        """
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[offset:offset + limit]

    async def get_history(
        self,
        limit: int = 50,
        statuses: Optional[list[str]] = None,
    ) -> list[JobRecord]:
        """Get application history.

        Args:
            limit: Max records.
            statuses: Optional status filter.

        Returns:
            List of jobs sorted by updated_at desc.
        """
        jobs = list(self._jobs.values())
        if statuses:
            jobs = [j for j in jobs if j.status in statuses]
        jobs.sort(key=lambda j: j.updated_at, reverse=True)
        return jobs[:limit]

    # =========================================================================
    # Specialized Methods
    # =========================================================================

    async def find_by_application_url(self, url: str) -> Optional[JobRecord]:
        """Find a job by its application URL.

        Args:
            url: Application URL to search for.

        Returns:
            JobRecord if found, None otherwise.
        """
        for job in self._jobs.values():
            if job.application_url == url:
                return job
        return None

    async def cleanup(
        self,
        older_than_days: int,
        statuses: list[str],
    ) -> int:
        """Delete old jobs matching criteria.

        Args:
            older_than_days: Delete jobs older than this many days.
            statuses: Only delete jobs with these statuses.

        Returns:
            Number of records deleted.

        Raises:
            ValueError: If older_than_days < 1 or statuses is empty.
        """
        if older_than_days < 1:
            raise ValueError("older_than_days must be >= 1")
        if not statuses:
            raise ValueError("statuses list cannot be empty")

        cutoff_date = datetime.now() - timedelta(days=older_than_days)
        to_delete = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in statuses and job.created_at < cutoff_date
        ]

        for job_id in to_delete:
            del self._jobs[job_id]

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

        from .tables import Job

        # Ensure data directory exists
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        # Create engine for this specific database
        self._engine = SQLiteEngine(path=self.db_path)

        # Set the engine on the Job table for this repository instance
        Job._meta._db = self._engine

        # Create tables if they don't exist
        try:
            await Job.create_table(if_not_exists=True).run()
            logger.info(f"SQLite repository initialized at {self.db_path}")
            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize SQLite repository: {e}")
            raise RepositoryError(f"Database initialization failed: {e}")

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

    # =========================================================================
    # Conversion Helpers
    # =========================================================================

    def _job_record_to_row(self, job: JobRecord) -> dict:
        """Convert Pydantic JobRecord to database row dict."""
        return {
            "job_id": job.job_id,
            "source": job.source,
            "mode": job.mode,
            "status": job.status,
            "job_posting": job.job_posting,
            "raw_input": job.raw_input,
            "cv_json": job.cv_json,
            "pdf_path": job.pdf_path,
            "application_url": job.application_url,
            "application_type": job.application_type,
            "application_result": job.application_result,
            "user_feedback": job.user_feedback,
            "retry_count": job.retry_count,
            "error_message": job.error_message,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "applied_at": job.applied_at,
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
        """Convert timezone-aware datetime to naive (remove tzinfo)."""
        if dt is None:
            return None
        if hasattr(dt, 'replace') and dt.tzinfo is not None:
            return dt.replace(tzinfo=None)
        return dt

    def _row_to_job_record(self, row: dict) -> JobRecord:
        """Convert database row dict to Pydantic JobRecord."""
        return JobRecord(
            job_id=row["job_id"],
            source=row["source"],
            mode=row["mode"],
            status=row["status"],
            job_posting=self._parse_json_field(row.get("job_posting")),
            raw_input=self._parse_json_field(row.get("raw_input")),
            cv_json=self._parse_json_field(row.get("cv_json")),
            pdf_path=row.get("pdf_path"),
            application_url=row.get("application_url"),
            application_type=row.get("application_type"),
            application_result=self._parse_json_field(row.get("application_result")),
            user_feedback=row.get("user_feedback"),
            retry_count=row.get("retry_count", 0),
            error_message=row.get("error_message"),
            created_at=self._normalize_datetime(row.get("created_at")) or datetime.now(),
            updated_at=self._normalize_datetime(row.get("updated_at")) or datetime.now(),
            applied_at=self._normalize_datetime(row.get("applied_at")),
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

    async def get(self, job_id: str) -> Optional[JobRecord]:
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

        # Auto-set updated_at
        updates["updated_at"] = datetime.now()

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

    async def get_pending(self) -> list[JobRecord]:
        """Get all pending jobs.

        Returns:
            List of jobs with status='pending', sorted by created_at desc.
        """
        self._ensure_initialized()
        from .tables import Job

        rows = (
            await Job.select()
            .where(Job.status == "pending")
            .order_by(Job.created_at, ascending=False)
            .run()
        )

        return [self._row_to_job_record(row) for row in rows]

    async def get_by_status(
        self,
        status: str,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "created_at",
        order_desc: bool = True,
    ) -> list[JobRecord]:
        """Get jobs by status with pagination.

        Args:
            status: Status to filter by.
            limit: Max records.
            offset: Records to skip.
            order_by: Field to sort by.
            order_desc: Sort descending if True.

        Returns:
            List of matching jobs.
        """
        self._ensure_initialized()
        from .tables import Job

        # Determine order column
        order_column = Job.updated_at if order_by == "updated_at" else Job.created_at

        rows = (
            await Job.select()
            .where(Job.status == status)
            .order_by(order_column, ascending=not order_desc)
            .limit(limit)
            .offset(offset)
            .run()
        )

        return [self._row_to_job_record(row) for row in rows]

    async def get_all(self, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        """Get all jobs with pagination.

        Args:
            limit: Max records.
            offset: Records to skip.

        Returns:
            List of jobs sorted by created_at desc.
        """
        self._ensure_initialized()
        from .tables import Job

        rows = (
            await Job.select()
            .order_by(Job.created_at, ascending=False)
            .limit(limit)
            .offset(offset)
            .run()
        )

        return [self._row_to_job_record(row) for row in rows]

    async def get_history(
        self,
        limit: int = 50,
        statuses: Optional[list[str]] = None,
    ) -> list[JobRecord]:
        """Get application history.

        Args:
            limit: Max records.
            statuses: Optional status filter.

        Returns:
            List of jobs sorted by updated_at desc.
        """
        self._ensure_initialized()
        from .tables import Job

        query = Job.select().order_by(Job.updated_at, ascending=False).limit(limit)

        if statuses:
            query = query.where(Job.status.is_in(statuses))

        rows = await query.run()
        return [self._row_to_job_record(row) for row in rows]

    # =========================================================================
    # Specialized Methods
    # =========================================================================

    async def find_by_application_url(self, url: str) -> Optional[JobRecord]:
        """Find a job by its application URL.

        Args:
            url: Application URL to search for.

        Returns:
            JobRecord if found, None otherwise.
        """
        self._ensure_initialized()
        from .tables import Job

        row = await Job.select().where(Job.application_url == url).first().run()
        if not row:
            return None

        return self._row_to_job_record(row)

    async def cleanup(
        self,
        older_than_days: int,
        statuses: list[str],
    ) -> int:
        """Delete old jobs matching criteria.

        Args:
            older_than_days: Delete jobs older than this many days.
            statuses: Only delete jobs with these statuses.

        Returns:
            Number of records deleted.

        Raises:
            ValueError: If older_than_days < 1 or statuses is empty.
        """
        self._ensure_initialized()
        from .tables import Job

        if older_than_days < 1:
            raise ValueError("older_than_days must be >= 1")
        if not statuses:
            raise ValueError("statuses list cannot be empty")

        cutoff_date = datetime.now() - timedelta(days=older_than_days)

        # Count jobs to delete
        to_delete = (
            await Job.select(Job.job_id)
            .where(Job.status.is_in(statuses))
            .where(Job.created_at < cutoff_date)
            .run()
        )
        count = len(to_delete)

        if count > 0:
            await (
                Job.delete()
                .where(Job.status.is_in(statuses))
                .where(Job.created_at < cutoff_date)
                .run()
            )

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
