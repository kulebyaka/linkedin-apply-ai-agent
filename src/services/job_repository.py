"""Job Repository - Data Access Layer for job persistence.

This module provides an abstract repository interface and concrete implementations
for storing and retrieving job records.

IMPLEMENTATION STATUS: Method stubs only.
- InMemoryJobRepository: Stub implementation for development/testing.
- SQLiteJobRepository: Stub for future production use.

The repository pattern decouples the workflow logic from storage implementation,
allowing easy switching between in-memory, SQLite, or PostgreSQL backends.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from ..models.unified import JobRecord


class JobRepository(ABC):
    """Abstract base class for job repositories.

    All repository implementations must provide these methods for
    CRUD operations on JobRecord objects.
    """

    @abstractmethod
    async def create(self, job: JobRecord) -> str:
        """Create a new job record.

        Args:
            job: JobRecord to persist.

        Returns:
            The job_id of the created record.

        Raises:
            RepositoryError: If creation fails.
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

        Args:
            job_id: Unique job identifier.
            updates: Dictionary of fields to update.

        Raises:
            RepositoryError: If job not found or update fails.
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

    @abstractmethod
    async def get_pending(self) -> list[JobRecord]:
        """Get all jobs with status='pending' (awaiting HITL review).

        Returns:
            List of JobRecord objects pending approval.
        """
        pass

    @abstractmethod
    async def get_by_status(self, status: str) -> list[JobRecord]:
        """Get all jobs with a specific status.

        Args:
            status: Job status to filter by.

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
            List of JobRecord objects.
        """
        pass

    @abstractmethod
    async def get_history(
        self,
        limit: int = 50,
        statuses: Optional[list[str]] = None
    ) -> list[JobRecord]:
        """Get job application history.

        Args:
            limit: Maximum number of records to return.
            statuses: Optional list of statuses to filter by.

        Returns:
            List of JobRecord objects, ordered by created_at desc.
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

    IMPLEMENTATION STATUS: Stub only.
    Suitable for development and testing. Data is lost on restart.
    """

    def __init__(self):
        """Initialize empty in-memory storage."""
        self._jobs: dict[str, JobRecord] = {}

    async def create(self, job: JobRecord) -> str:
        """Create a new job record in memory.

        Args:
            job: JobRecord to store.

        Returns:
            The job_id.
        """
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
        """
        if job_id not in self._jobs:
            raise RepositoryError(f"Job not found: {job_id}", job_id)
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

    async def get_pending(self) -> list[JobRecord]:
        """Get all pending jobs.

        Returns:
            List of jobs with status='pending'.
        """
        return [j for j in self._jobs.values() if j.status == "pending"]

    async def get_by_status(self, status: str) -> list[JobRecord]:
        """Get jobs by status.

        Args:
            status: Status to filter by.

        Returns:
            List of matching jobs.
        """
        return [j for j in self._jobs.values() if j.status == status]

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
        statuses: Optional[list[str]] = None
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


class SQLiteJobRepository(JobRepository):
    """SQLite implementation of JobRepository.

    IMPLEMENTATION STATUS: Stub only - future feature.
    For production use with persistent storage.
    """

    def __init__(self, db_path: str = "data/jobs.db"):
        """Initialize SQLite repository.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path
        self._connection = None

    async def _get_connection(self):
        """Get or create database connection."""
        raise NotImplementedError(
            "SQLiteJobRepository not yet implemented - future feature"
        )

    async def create(self, job: JobRecord) -> str:
        raise NotImplementedError(
            "SQLiteJobRepository.create() not yet implemented - future feature"
        )

    async def get(self, job_id: str) -> Optional[JobRecord]:
        raise NotImplementedError(
            "SQLiteJobRepository.get() not yet implemented - future feature"
        )

    async def update(self, job_id: str, updates: dict) -> None:
        raise NotImplementedError(
            "SQLiteJobRepository.update() not yet implemented - future feature"
        )

    async def delete(self, job_id: str) -> bool:
        raise NotImplementedError(
            "SQLiteJobRepository.delete() not yet implemented - future feature"
        )

    async def get_pending(self) -> list[JobRecord]:
        raise NotImplementedError(
            "SQLiteJobRepository.get_pending() not yet implemented - future feature"
        )

    async def get_by_status(self, status: str) -> list[JobRecord]:
        raise NotImplementedError(
            "SQLiteJobRepository.get_by_status() not yet implemented - future feature"
        )

    async def get_all(self, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        raise NotImplementedError(
            "SQLiteJobRepository.get_all() not yet implemented - future feature"
        )

    async def get_history(
        self,
        limit: int = 50,
        statuses: Optional[list[str]] = None
    ) -> list[JobRecord]:
        raise NotImplementedError(
            "SQLiteJobRepository.get_history() not yet implemented - future feature"
        )


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

    return repositories[repo_type](**kwargs)
