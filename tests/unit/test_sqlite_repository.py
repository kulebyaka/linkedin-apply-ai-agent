"""Unit tests for SQLiteJobRepository.

Tests all methods of the SQLite-backed job repository implementation.
Uses temporary database files for isolation.
"""

from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from src.models.unified import JobRecord
from src.services.db.job_repository import (
    RepositoryError,
    SQLiteJobRepository,
)

TEST_USER_ID = "user-test-123"


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    """Create temporary SQLite database for testing."""
    db_path = tmp_path / "test_jobs.db"
    repo = SQLiteJobRepository(db_path=str(db_path))
    await repo.initialize()
    yield repo
    await repo.close()
    # Cleanup: delete temp database
    if db_path.exists():
        db_path.unlink()


# =============================================================================
# Lifecycle Tests
# =============================================================================


@pytest.mark.asyncio
async def test_initialize_creates_database_file(tmp_path):
    """Test that initialize() creates database file."""
    db_path = tmp_path / "new.db"
    repo = SQLiteJobRepository(db_path=str(db_path))
    await repo.initialize()
    assert db_path.exists()
    await repo.close()


@pytest.mark.asyncio
async def test_initialize_creates_parent_directories(tmp_path):
    """Test that initialize() creates parent directories if missing."""
    db_path = tmp_path / "nested" / "dirs" / "test.db"
    repo = SQLiteJobRepository(db_path=str(db_path))
    await repo.initialize()
    assert db_path.exists()
    await repo.close()


@pytest.mark.asyncio
async def test_operations_fail_without_initialize(tmp_path):
    """Test that operations fail if initialize() not called."""
    db_path = tmp_path / "uninit.db"
    repo = SQLiteJobRepository(db_path=str(db_path))

    job = JobRecord(job_id="test-1", user_id=TEST_USER_ID, source="manual", mode="mvp", status="queued")

    with pytest.raises(RepositoryError) as exc:
        await repo.create(job)
    assert "not initialized" in str(exc.value)


# =============================================================================
# CRUD Tests
# =============================================================================


@pytest.mark.asyncio
async def test_create_and_get(temp_db):
    """Test creating and retrieving a job."""
    job = JobRecord(job_id="test-1", user_id=TEST_USER_ID, source="manual", mode="mvp", status="queued")
    job_id = await temp_db.create(job)
    assert job_id == "test-1"

    retrieved = await temp_db.get("test-1")
    assert retrieved is not None
    assert retrieved.job_id == "test-1"
    assert retrieved.user_id == TEST_USER_ID
    assert retrieved.source == "manual"
    assert retrieved.mode == "mvp"
    assert retrieved.status == "queued"


@pytest.mark.asyncio
async def test_create_with_full_data(temp_db):
    """Test creating a job with all fields populated."""
    job = JobRecord(
        job_id="full-1",
        user_id=TEST_USER_ID,
        source="url",
        mode="full",
        status="pending",
        job_posting={"title": "Software Engineer", "company": "TechCorp"},
        raw_input={"url": "https://example.com/job/123"},
        current_cv_json={"name": "John Doe", "skills": ["Python", "FastAPI"]},
        current_pdf_path="/data/generated_cvs/full-1.pdf",
        application_url="https://example.com/apply/123",
        error_message=None,
    )

    job_id = await temp_db.create(job)
    assert job_id == "full-1"

    retrieved = await temp_db.get("full-1")
    assert retrieved is not None
    assert retrieved.job_posting["title"] == "Software Engineer"
    assert retrieved.current_cv_json["skills"] == ["Python", "FastAPI"]


@pytest.mark.asyncio
async def test_create_duplicate_raises_error(temp_db):
    """Test that creating duplicate job_id raises RepositoryError."""
    job = JobRecord(job_id="dup-1", user_id=TEST_USER_ID, source="manual", mode="mvp", status="queued")
    await temp_db.create(job)

    with pytest.raises(RepositoryError) as exc:
        await temp_db.create(job)
    assert "already exists" in str(exc.value)


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(temp_db):
    """Test that getting a nonexistent job returns None."""
    result = await temp_db.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_update(temp_db):
    """Test updating job fields."""
    job = JobRecord(job_id="test-2", user_id=TEST_USER_ID, source="url", mode="full", status="pending")
    await temp_db.create(job)

    await temp_db.update("test-2", {"status": "approved"})

    updated = await temp_db.get("test-2")
    assert updated.status == "approved"
    assert updated.updated_at > job.updated_at  # Auto-updated


@pytest.mark.asyncio
async def test_update_multiple_fields(temp_db):
    """Test updating multiple fields at once."""
    job = JobRecord(job_id="test-3", user_id=TEST_USER_ID, source="url", mode="full", status="pending")
    await temp_db.create(job)

    await temp_db.update("test-3", {
        "status": "approved",
        "current_pdf_path": "/data/cv.pdf",
    })

    updated = await temp_db.get("test-3")
    assert updated.status == "approved"
    assert updated.current_pdf_path == "/data/cv.pdf"


@pytest.mark.asyncio
async def test_update_invalid_field_raises_error(temp_db):
    """Test that updating with invalid field raises ValueError."""
    job = JobRecord(job_id="test-4", user_id=TEST_USER_ID, source="url", mode="full", status="pending")
    await temp_db.create(job)

    with pytest.raises(ValueError) as exc:
        await temp_db.update("test-4", {"invalid_field": "value"})
    assert "Invalid update fields" in str(exc.value)


@pytest.mark.asyncio
async def test_update_nonexistent_raises_error(temp_db):
    """Test that updating nonexistent job raises RepositoryError."""
    with pytest.raises(RepositoryError) as exc:
        await temp_db.update("nonexistent", {"status": "approved"})
    assert "not found" in str(exc.value)


@pytest.mark.asyncio
async def test_delete(temp_db):
    """Test deleting a job."""
    job = JobRecord(job_id="del-1", user_id=TEST_USER_ID, source="url", mode="mvp", status="queued")
    await temp_db.create(job)

    assert await temp_db.delete("del-1") is True
    assert await temp_db.get("del-1") is None
    assert await temp_db.delete("del-1") is False  # Already deleted


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_false(temp_db):
    """Test that deleting nonexistent job returns False."""
    result = await temp_db.delete("nonexistent")
    assert result is False


# =============================================================================
# get_for_user Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_for_user_returns_owned_job(temp_db):
    """Test get_for_user returns job when user matches."""
    job = JobRecord(job_id="own-1", user_id=TEST_USER_ID, source="manual", mode="mvp", status="queued")
    await temp_db.create(job)

    result = await temp_db.get_for_user("own-1", TEST_USER_ID)
    assert result is not None
    assert result.job_id == "own-1"


@pytest.mark.asyncio
async def test_get_for_user_returns_none_for_other_user(temp_db):
    """Test get_for_user returns None when user doesn't match."""
    job = JobRecord(job_id="own-2", user_id=TEST_USER_ID, source="manual", mode="mvp", status="queued")
    await temp_db.create(job)

    result = await temp_db.get_for_user("own-2", "other-user-id")
    assert result is None


# =============================================================================
# Query Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_pending(temp_db):
    """Test retrieving pending jobs for a specific user."""
    await temp_db.create(JobRecord(job_id="p1", user_id=TEST_USER_ID, source="url", mode="full", status="pending"))
    await temp_db.create(JobRecord(job_id="p2", user_id=TEST_USER_ID, source="url", mode="full", status="pending"))
    await temp_db.create(JobRecord(job_id="c1", user_id=TEST_USER_ID, source="url", mode="mvp", status="completed"))
    await temp_db.create(JobRecord(job_id="p3", user_id="other-user", source="url", mode="full", status="pending"))

    pending = await temp_db.get_pending(TEST_USER_ID)
    assert len(pending) == 2
    assert all(j.status == "pending" for j in pending)
    assert all(j.user_id == TEST_USER_ID for j in pending)


@pytest.mark.asyncio
async def test_get_pending_empty(temp_db):
    """Test get_pending with no pending jobs."""
    await temp_db.create(JobRecord(job_id="c1", user_id=TEST_USER_ID, source="url", mode="mvp", status="completed"))

    pending = await temp_db.get_pending(TEST_USER_ID)
    assert len(pending) == 0


@pytest.mark.asyncio
async def test_get_by_status(temp_db):
    """Test getting jobs by status for a user."""
    await temp_db.create(JobRecord(job_id="j1", user_id=TEST_USER_ID, source="url", mode="full", status="pending"))
    await temp_db.create(JobRecord(job_id="j2", user_id=TEST_USER_ID, source="url", mode="full", status="pending"))
    await temp_db.create(JobRecord(job_id="j3", user_id=TEST_USER_ID, source="url", mode="full", status="approved"))
    await temp_db.create(JobRecord(job_id="j4", user_id="other-user", source="url", mode="full", status="pending"))

    pending = await temp_db.get_by_status(TEST_USER_ID, "pending")
    assert len(pending) == 2

    approved = await temp_db.get_by_status(TEST_USER_ID, "approved")
    assert len(approved) == 1
    assert approved[0].job_id == "j3"


@pytest.mark.asyncio
async def test_get_by_status_with_pagination(temp_db):
    """Test get_by_status with pagination."""
    for i in range(5):
        await temp_db.create(JobRecord(
            job_id=f"job-{i}", user_id=TEST_USER_ID, source="url", mode="full", status="pending"
        ))

    page1 = await temp_db.get_by_status(TEST_USER_ID, "pending", limit=2, offset=0)
    page2 = await temp_db.get_by_status(TEST_USER_ID, "pending", limit=2, offset=2)

    assert len(page1) == 2
    assert len(page2) == 2
    # Ensure different jobs on different pages
    page1_ids = {j.job_id for j in page1}
    page2_ids = {j.job_id for j in page2}
    assert page1_ids.isdisjoint(page2_ids)


@pytest.mark.asyncio
async def test_get_all(temp_db):
    """Test getting all jobs for a user."""
    await temp_db.create(JobRecord(job_id="j1", user_id=TEST_USER_ID, source="url", mode="full", status="pending"))
    await temp_db.create(JobRecord(job_id="j2", user_id=TEST_USER_ID, source="url", mode="mvp", status="completed"))
    await temp_db.create(JobRecord(job_id="j3", user_id="other-user", source="manual", mode="full", status="declined"))

    all_jobs = await temp_db.get_all(TEST_USER_ID)
    assert len(all_jobs) == 2
    assert all(j.user_id == TEST_USER_ID for j in all_jobs)


@pytest.mark.asyncio
async def test_get_all_with_pagination(temp_db):
    """Test get_all with pagination."""
    for i in range(10):
        await temp_db.create(JobRecord(
            job_id=f"job-{i}", user_id=TEST_USER_ID, source="url", mode="full", status="pending"
        ))

    page1 = await temp_db.get_all(TEST_USER_ID, limit=5, offset=0)
    page2 = await temp_db.get_all(TEST_USER_ID, limit=5, offset=5)

    assert len(page1) == 5
    assert len(page2) == 5


@pytest.mark.asyncio
async def test_get_history(temp_db):
    """Test getting job history for a user."""
    await temp_db.create(JobRecord(job_id="j1", user_id=TEST_USER_ID, source="url", mode="full", status="applied"))
    await temp_db.create(JobRecord(job_id="j2", user_id=TEST_USER_ID, source="url", mode="full", status="declined"))
    await temp_db.create(JobRecord(job_id="j3", user_id="other-user", source="url", mode="full", status="pending"))

    history = await temp_db.get_history(TEST_USER_ID, limit=10)
    assert len(history) == 2
    assert all(j.user_id == TEST_USER_ID for j in history)


@pytest.mark.asyncio
async def test_get_history_with_status_filter(temp_db):
    """Test get_history with status filter."""
    await temp_db.create(JobRecord(job_id="j1", user_id=TEST_USER_ID, source="url", mode="full", status="applied"))
    await temp_db.create(JobRecord(job_id="j2", user_id=TEST_USER_ID, source="url", mode="full", status="declined"))
    await temp_db.create(JobRecord(job_id="j3", user_id=TEST_USER_ID, source="url", mode="full", status="failed"))

    history = await temp_db.get_history(TEST_USER_ID, limit=10, statuses=["applied", "declined"])
    assert len(history) == 2
    assert all(j.status in ["applied", "declined"] for j in history)


# =============================================================================
# Specialized Tests
# =============================================================================


@pytest.mark.asyncio
async def test_find_by_application_url(temp_db):
    """Test finding job by application URL."""
    job = JobRecord(
        job_id="url-1",
        user_id=TEST_USER_ID,
        source="url",
        mode="full",
        status="pending",
        application_url="https://example.com/job/123"
    )
    await temp_db.create(job)

    found = await temp_db.find_by_application_url("https://example.com/job/123")
    assert found is not None
    assert found.job_id == "url-1"


@pytest.mark.asyncio
async def test_find_by_application_url_not_found(temp_db):
    """Test find_by_application_url when URL doesn't exist."""
    not_found = await temp_db.find_by_application_url("https://example.com/job/999")
    assert not_found is None


@pytest.mark.asyncio
async def test_cleanup(temp_db):
    """Test cleanup of old jobs."""
    # Create old declined job (manually set old created_at)
    old_job = JobRecord(
        job_id="old-1",
        user_id=TEST_USER_ID,
        source="url",
        mode="full",
        status="declined",
        created_at=datetime.now() - timedelta(days=100)
    )
    await temp_db.create(old_job)

    # Create recent declined job (should NOT be deleted)
    recent_job = JobRecord(
        job_id="recent-1",
        user_id=TEST_USER_ID,
        source="url",
        mode="full",
        status="declined",
        created_at=datetime.now() - timedelta(days=10)
    )
    await temp_db.create(recent_job)

    # Create old pending job (should NOT be deleted - different status)
    old_pending = JobRecord(
        job_id="old-pending",
        user_id=TEST_USER_ID,
        source="url",
        mode="full",
        status="pending",
        created_at=datetime.now() - timedelta(days=100)
    )
    await temp_db.create(old_pending)

    # Cleanup jobs older than 90 days with status 'declined'
    deleted = await temp_db.cleanup(older_than_days=90, statuses=["declined"])
    assert deleted == 1

    # Verify correct job deleted
    assert await temp_db.get("old-1") is None
    assert await temp_db.get("recent-1") is not None
    assert await temp_db.get("old-pending") is not None


@pytest.mark.asyncio
async def test_cleanup_multiple_statuses(temp_db):
    """Test cleanup with multiple statuses."""
    # Create old jobs with different statuses
    for status in ["declined", "failed", "completed"]:
        await temp_db.create(JobRecord(
            job_id=f"old-{status}",
            user_id=TEST_USER_ID,
            source="url",
            mode="full",
            status=status,
            created_at=datetime.now() - timedelta(days=100)
        ))

    # Cleanup declined and failed only
    deleted = await temp_db.cleanup(older_than_days=90, statuses=["declined", "failed"])
    assert deleted == 2

    # Completed should still exist
    assert await temp_db.get("old-completed") is not None


@pytest.mark.asyncio
async def test_cleanup_validation_older_than_days(temp_db):
    """Test cleanup parameter validation - older_than_days."""
    with pytest.raises(ValueError) as exc:
        await temp_db.cleanup(older_than_days=0, statuses=["declined"])
    assert "older_than_days must be >= 1" in str(exc.value)


@pytest.mark.asyncio
async def test_cleanup_validation_empty_statuses(temp_db):
    """Test cleanup parameter validation - empty statuses."""
    with pytest.raises(ValueError) as exc:
        await temp_db.cleanup(older_than_days=30, statuses=[])
    assert "statuses list cannot be empty" in str(exc.value)


# =============================================================================
# Persistence Tests
# =============================================================================


@pytest.mark.asyncio
async def test_data_persists_after_close_and_reopen(tmp_path):
    """Test that data persists after closing and reopening repository."""
    db_path = tmp_path / "persist_test.db"

    # Create and populate first repository instance
    repo1 = SQLiteJobRepository(db_path=str(db_path))
    await repo1.initialize()
    await repo1.create(JobRecord(
        job_id="persist-1", user_id=TEST_USER_ID, source="url", mode="full", status="pending"
    ))
    await repo1.close()

    # Create second repository instance with same database
    repo2 = SQLiteJobRepository(db_path=str(db_path))
    await repo2.initialize()

    # Verify data persisted
    job = await repo2.get("persist-1")
    assert job is not None
    assert job.job_id == "persist-1"
    assert job.user_id == TEST_USER_ID
    assert job.status == "pending"

    await repo2.close()


# =============================================================================
# Factory Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_repository_factory_memory():
    """Test get_repository factory returns InMemoryJobRepository."""
    from src.services.db.job_repository import InMemoryJobRepository, get_repository

    repo = get_repository(repo_type="memory")
    assert isinstance(repo, InMemoryJobRepository)


@pytest.mark.asyncio
async def test_get_repository_factory_sqlite(tmp_path):
    """Test get_repository factory returns SQLiteJobRepository."""
    from src.services.db.job_repository import SQLiteJobRepository, get_repository

    db_path = tmp_path / "factory_test.db"
    repo = get_repository(repo_type="sqlite", db_path=str(db_path))
    assert isinstance(repo, SQLiteJobRepository)


@pytest.mark.asyncio
async def test_get_repository_factory_invalid():
    """Test get_repository factory raises error for invalid type."""
    from src.services.db.job_repository import get_repository

    with pytest.raises(ValueError) as exc:
        get_repository(repo_type="invalid")
    assert "Unknown repository type" in str(exc.value)
