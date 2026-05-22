"""Re-export shim for the split repository package.

The implementation has been split across `repository.py`, `in_memory_repository.py`,
`sqlite_repository.py`, `sqlite_admin_queries.py`, `migrations.py`, and
`factory.py`. Existing imports such as
`from src.services.db.job_repository import InMemoryJobRepository`
continue to work via the re-exports below.
"""

from .factory import get_repository
from .in_memory_repository import InMemoryJobRepository
from .repository import UPDATABLE_FIELDS, JobRepository, RepositoryError
from .sqlite_repository import SQLiteJobRepository

__all__ = [
    "InMemoryJobRepository",
    "JobRepository",
    "RepositoryError",
    "SQLiteJobRepository",
    "UPDATABLE_FIELDS",
    "get_repository",
]
