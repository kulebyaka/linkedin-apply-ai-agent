"""Tests for CV composition attempt tracking in repositories.

Tests the create_cv_attempt(), get_cv_attempts(), and get_latest_cv_attempt()
methods on both InMemoryJobRepository and SQLiteJobRepository.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from src.models.cv_attempt import CVCompositionAttempt
from src.models.unified import JobRecord
from src.services.db.job_repository import InMemoryJobRepository, SQLiteJobRepository

pytestmark = pytest.mark.asyncio


def _make_job(job_id: str = "job-1") -> JobRecord:
    return JobRecord(
        job_id=job_id,
        source="manual",
        mode="full",
        status="pending",
        current_cv_json={"name": "Test"},
        current_pdf_path="/tmp/test.pdf",
    )


def _make_attempt(
    job_id: str = "job-1", attempt_number: int = 1, feedback: str | None = None
) -> CVCompositionAttempt:
    return CVCompositionAttempt(
        job_id=job_id,
        attempt_number=attempt_number,
        user_feedback=feedback,
        cv_json={"name": "Test", "attempt": attempt_number},
        pdf_path=f"/tmp/cv_v{attempt_number}.pdf",
    )


# ============================================================================
# InMemory Tests
# ============================================================================


class TestInMemoryCVAttempts:
    async def test_create_and_get_attempts(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(_make_job("job-1"))

        await repo.create_cv_attempt(_make_attempt("job-1", 1))
        await repo.create_cv_attempt(_make_attempt("job-1", 2, "More Python"))

        attempts = await repo.get_cv_attempts("job-1")
        assert len(attempts) == 2
        assert attempts[0].attempt_number == 1
        assert attempts[1].attempt_number == 2
        assert attempts[1].user_feedback == "More Python"

    async def test_get_latest_attempt(self):
        repo = InMemoryJobRepository()
        await repo.initialize()

        await repo.create_cv_attempt(_make_attempt("job-1", 1))
        await repo.create_cv_attempt(_make_attempt("job-1", 2, "Add skills"))
        await repo.create_cv_attempt(_make_attempt("job-1", 3, "Shorter"))

        latest = await repo.get_latest_cv_attempt("job-1")
        assert latest is not None
        assert latest.attempt_number == 3
        assert latest.user_feedback == "Shorter"

    async def test_get_latest_no_attempts(self):
        repo = InMemoryJobRepository()
        await repo.initialize()

        latest = await repo.get_latest_cv_attempt("nonexistent")
        assert latest is None

    async def test_get_attempts_empty(self):
        repo = InMemoryJobRepository()
        await repo.initialize()

        attempts = await repo.get_cv_attempts("nonexistent")
        assert attempts == []

    async def test_attempts_isolated_per_job(self):
        repo = InMemoryJobRepository()
        await repo.initialize()

        await repo.create_cv_attempt(_make_attempt("job-1", 1))
        await repo.create_cv_attempt(_make_attempt("job-2", 1))
        await repo.create_cv_attempt(_make_attempt("job-2", 2))

        assert len(await repo.get_cv_attempts("job-1")) == 1
        assert len(await repo.get_cv_attempts("job-2")) == 2

    async def test_cleanup_removes_attempts(self):
        from datetime import timedelta

        repo = InMemoryJobRepository()
        await repo.initialize()

        old_job = JobRecord(
            job_id="old-1",
            source="manual",
            mode="full",
            status="declined",
            created_at=datetime.now(tz=timezone.utc) - timedelta(days=100),
        )
        await repo.create(old_job)
        await repo.create_cv_attempt(_make_attempt("old-1", 1))

        deleted = await repo.cleanup(older_than_days=90, statuses=["declined"])
        assert deleted == 1
        assert await repo.get_cv_attempts("old-1") == []


# ============================================================================
# SQLite Tests
# ============================================================================


@pytest_asyncio.fixture
async def sqlite_repo(tmp_path):
    db_path = tmp_path / "test_cv_attempts.db"
    repo = SQLiteJobRepository(db_path=str(db_path))
    await repo.initialize()
    yield repo
    await repo.close()


class TestSQLiteCVAttempts:
    async def test_create_and_get_attempts(self, sqlite_repo):
        await sqlite_repo.create(_make_job("job-1"))

        await sqlite_repo.create_cv_attempt(_make_attempt("job-1", 1))
        await sqlite_repo.create_cv_attempt(
            _make_attempt("job-1", 2, "More Python")
        )

        attempts = await sqlite_repo.get_cv_attempts("job-1")
        assert len(attempts) == 2
        assert attempts[0].attempt_number == 1
        assert attempts[1].attempt_number == 2
        assert attempts[1].user_feedback == "More Python"

    async def test_get_latest_attempt(self, sqlite_repo):
        await sqlite_repo.create(_make_job("job-1"))
        await sqlite_repo.create_cv_attempt(_make_attempt("job-1", 1))
        await sqlite_repo.create_cv_attempt(_make_attempt("job-1", 2, "Add skills"))
        await sqlite_repo.create_cv_attempt(_make_attempt("job-1", 3, "Shorter"))

        latest = await sqlite_repo.get_latest_cv_attempt("job-1")
        assert latest is not None
        assert latest.attempt_number == 3
        assert latest.user_feedback == "Shorter"

    async def test_get_latest_no_attempts(self, sqlite_repo):
        latest = await sqlite_repo.get_latest_cv_attempt("nonexistent")
        assert latest is None

    async def test_get_attempts_empty(self, sqlite_repo):
        attempts = await sqlite_repo.get_cv_attempts("nonexistent")
        assert attempts == []

    async def test_attempts_isolated_per_job(self, sqlite_repo):
        await sqlite_repo.create(_make_job("job-1"))
        await sqlite_repo.create(_make_job("job-2"))

        await sqlite_repo.create_cv_attempt(_make_attempt("job-1", 1))
        await sqlite_repo.create_cv_attempt(_make_attempt("job-2", 1))
        await sqlite_repo.create_cv_attempt(_make_attempt("job-2", 2))

        assert len(await sqlite_repo.get_cv_attempts("job-1")) == 1
        assert len(await sqlite_repo.get_cv_attempts("job-2")) == 2

    async def test_attempt_cv_json_roundtrip(self, sqlite_repo):
        await sqlite_repo.create(_make_job("job-1"))

        cv_data = {
            "contact": {"full_name": "John Doe"},
            "skills": ["Python", "FastAPI", "React"],
            "experience": [{"company": "Acme", "years": 5}],
        }
        attempt = CVCompositionAttempt(
            job_id="job-1",
            attempt_number=1,
            cv_json=cv_data,
        )
        await sqlite_repo.create_cv_attempt(attempt)

        retrieved = await sqlite_repo.get_latest_cv_attempt("job-1")
        assert retrieved is not None
        assert retrieved.cv_json == cv_data
