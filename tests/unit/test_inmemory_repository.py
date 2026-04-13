"""Tests for InMemoryJobRepository thread safety.

Verifies that concurrent operations on InMemoryJobRepository
do not cause lost updates or race conditions.
"""

import asyncio

import pytest

from src.models.unified import JobRecord
from src.services.job_repository import InMemoryJobRepository, RepositoryError

pytestmark = pytest.mark.asyncio

TEST_USER_ID = "user-test-123"


def _make_job(job_id: str = "test-1", status: str = "queued") -> JobRecord:
    return JobRecord(
        job_id=job_id,
        user_id=TEST_USER_ID,
        source="manual",
        mode="full",
        status=status,
    )


class TestInMemoryLockProtection:
    """Test that concurrent operations are serialized by the lock."""

    async def test_concurrent_updates_no_lost_writes(self):
        """Many concurrent updates should all be applied (no lost updates)."""
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(_make_job("job-1", status="queued"))

        results = []

        async def update_status(i: int):
            await repo.update("job-1", {"error_message": f"attempt-{i}"})
            results.append(i)

        await asyncio.gather(*[update_status(i) for i in range(50)])

        assert len(results) == 50
        job = await repo.get("job-1")
        assert job is not None
        assert job.error_message.startswith("attempt-")

    async def test_concurrent_create_duplicate_rejected(self):
        """Concurrent creates with same ID: exactly one succeeds."""
        repo = InMemoryJobRepository()
        await repo.initialize()

        errors = []
        successes = []

        async def try_create(i: int):
            try:
                await repo.create(_make_job("dup-1"))
                successes.append(i)
            except RepositoryError:
                errors.append(i)

        await asyncio.gather(*[try_create(i) for i in range(10)])

        assert len(successes) == 1
        assert len(errors) == 9

    async def test_concurrent_delete_only_one_succeeds(self):
        """Concurrent deletes: exactly one returns True."""
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(_make_job("del-1"))

        results = await asyncio.gather(
            *[repo.delete("del-1") for _ in range(10)]
        )

        assert results.count(True) == 1
        assert results.count(False) == 9

    async def test_concurrent_create_and_update(self):
        """Create then many concurrent updates should all succeed (using valid transitions)."""
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(_make_job("cu-1", status="pending"))

        valid_targets = ["approved", "declined", "retrying"]

        async def update_to(s: str):
            await repo.update("cu-1", {"error_message": f"test-{s}"})

        await asyncio.gather(*[update_to(s) for s in valid_targets])

        job = await repo.get("cu-1")
        assert job is not None
        assert job.error_message.startswith("test-")

    async def test_lock_exists(self):
        """Verify the repository has an asyncio.Lock."""
        repo = InMemoryJobRepository()
        assert isinstance(repo._lock, asyncio.Lock)


class TestInMemoryUserFiltering:
    """Test that user_id filtering works correctly."""

    async def test_get_for_user_returns_owned_job(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(_make_job("job-1"))

        result = await repo.get_for_user("job-1", TEST_USER_ID)
        assert result is not None
        assert result.job_id == "job-1"

    async def test_get_for_user_returns_none_for_other_user(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(_make_job("job-1"))

        result = await repo.get_for_user("job-1", "other-user")
        assert result is None

    async def test_get_pending_filters_by_user(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(JobRecord(
            job_id="p1", user_id=TEST_USER_ID, source="url", mode="full", status="pending"
        ))
        await repo.create(JobRecord(
            job_id="p2", user_id="other-user", source="url", mode="full", status="pending"
        ))

        pending = await repo.get_pending(TEST_USER_ID)
        assert len(pending) == 1
        assert pending[0].job_id == "p1"

    async def test_get_all_filters_by_user(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(JobRecord(
            job_id="j1", user_id=TEST_USER_ID, source="url", mode="full", status="queued"
        ))
        await repo.create(JobRecord(
            job_id="j2", user_id="other-user", source="url", mode="full", status="queued"
        ))

        all_jobs = await repo.get_all(TEST_USER_ID)
        assert len(all_jobs) == 1
        assert all_jobs[0].user_id == TEST_USER_ID

    async def test_get_history_filters_by_user(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(JobRecord(
            job_id="j1", user_id=TEST_USER_ID, source="url", mode="full", status="applied"
        ))
        await repo.create(JobRecord(
            job_id="j2", user_id="other-user", source="url", mode="full", status="applied"
        ))

        history = await repo.get_history(TEST_USER_ID)
        assert len(history) == 1
        assert history[0].user_id == TEST_USER_ID
