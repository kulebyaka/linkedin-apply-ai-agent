"""Persistence layer — Piccolo ORM tables and job repository."""

from .factory import get_repository
from .in_memory_repository import InMemoryJobRepository
from .repository import JobRepository, RepositoryError
from .sqlite_repository import SQLiteJobRepository

__all__ = [
    "InMemoryJobRepository",
    "JobRepository",
    "RepositoryError",
    "SQLiteJobRepository",
    "get_repository",
]
