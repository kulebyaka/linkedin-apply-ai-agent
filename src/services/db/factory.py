"""Factory for selecting a JobRepository implementation."""

from .in_memory_repository import InMemoryJobRepository
from .repository import JobRepository
from .sqlite_repository import SQLiteJobRepository


def get_repository(repo_type: str = "memory", **kwargs) -> JobRepository:
    """Factory function to get a repository instance.

    Args:
        repo_type: "memory" or "sqlite".
        **kwargs: Additional arguments for the repository constructor
            (e.g., db_path for sqlite).

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

    if repo_type == "memory":
        kwargs.pop("db_path", None)

    return repositories[repo_type](**kwargs)
