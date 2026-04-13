"""Unit tests for filter_preferences CRUD and filter_result persistence.

Tests cover:
- UserRepository: filter_preferences storage, retrieval, and update
- InMemoryJobRepository: filter_result on JobRecord create/update/get
- SQLiteJobRepository: filter_result persistence and schema migration
"""

import pytest
import pytest_asyncio

from src.models.job_filter import UserFilterPreferences
from src.models.unified import JobRecord
from src.services.job_repository import InMemoryJobRepository, SQLiteJobRepository

TEST_USER_ID = "user-filter-test"

SAMPLE_FILTER_RESULT = {
    "score": 45,
    "red_flags": ["Requires security clearance", "On-site only"],
    "disqualified": False,
    "disqualifier_reason": None,
    "reasoning": "Job has concerning requirements but not a hard disqualifier.",
}

SAMPLE_FILTER_RESULT_REJECTED = {
    "score": 15,
    "red_flags": ["Requires TS/SCI clearance"],
    "disqualified": True,
    "disqualifier_reason": "Requires TS/SCI security clearance",
    "reasoning": "Hard disqualifier: security clearance required.",
}


def _make_job(job_id: str = "filter-test-1", filter_result: dict | None = None) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        user_id=TEST_USER_ID,
        source="linkedin",
        mode="full",
        status="queued",
        filter_result=filter_result,
    )


# =============================================================================
# UserRepository: filter_preferences Tests
# =============================================================================


@pytest_asyncio.fixture
async def user_repo(tmp_path):
    """Set up a temporary SQLite database with user tables."""
    from piccolo.engine.sqlite import SQLiteEngine

    from src.services.tables import MagicLinkTable, UserTable

    db_path = tmp_path / "test_filter_users.db"
    engine = SQLiteEngine(path=str(db_path))

    UserTable._meta._db = engine
    MagicLinkTable._meta._db = engine

    await UserTable.create_table(if_not_exists=True).run()
    await MagicLinkTable.create_table(if_not_exists=True).run()

    from src.services.user_repository import UserRepository

    repo = UserRepository()
    yield repo

    await engine.close_connection_pool()


@pytest.mark.asyncio
async def test_user_created_without_filter_prefs(user_repo):
    """New users should have filter_preferences = None."""
    user = await user_repo.create_user("nofilter@example.com")
    assert user.filter_preferences is None


@pytest.mark.asyncio
async def test_update_filter_preferences_with_model(user_repo):
    """Updating filter_preferences with a UserFilterPreferences model should persist and round-trip."""
    user = await user_repo.create_user("filter@example.com")

    prefs = UserFilterPreferences(
        natural_language_prefs="No on-site jobs, no clearance",
        custom_prompt="Check for hidden requirements",
        reject_threshold=25,
        warning_threshold=65,
        enabled=True,
    )
    updated = await user_repo.update(user.id, {"filter_preferences": prefs})

    assert updated.filter_preferences is not None
    assert updated.filter_preferences.natural_language_prefs == "No on-site jobs, no clearance"
    assert updated.filter_preferences.custom_prompt == "Check for hidden requirements"
    assert updated.filter_preferences.reject_threshold == 25
    assert updated.filter_preferences.warning_threshold == 65
    assert updated.filter_preferences.enabled is True


@pytest.mark.asyncio
async def test_update_filter_preferences_with_dict(user_repo):
    """Updating filter_preferences with a raw dict should also work."""
    user = await user_repo.create_user("dictfilter@example.com")

    prefs_dict = {
        "natural_language_prefs": "Remote only",
        "custom_prompt": None,
        "reject_threshold": 30,
        "warning_threshold": 70,
        "enabled": False,
    }
    updated = await user_repo.update(user.id, {"filter_preferences": prefs_dict})

    assert updated.filter_preferences is not None
    assert updated.filter_preferences.natural_language_prefs == "Remote only"
    assert updated.filter_preferences.enabled is False


@pytest.mark.asyncio
async def test_update_filter_preferences_to_none(user_repo):
    """Setting filter_preferences to None should clear them."""
    user = await user_repo.create_user("clearpref@example.com")

    prefs = UserFilterPreferences(natural_language_prefs="Something")
    await user_repo.update(user.id, {"filter_preferences": prefs})

    updated = await user_repo.update(user.id, {"filter_preferences": None})
    assert updated.filter_preferences is None


@pytest.mark.asyncio
async def test_filter_preferences_persists_across_reads(user_repo):
    """filter_preferences should survive a round-trip through the DB."""
    user = await user_repo.create_user("persist@example.com")

    prefs = UserFilterPreferences(
        natural_language_prefs="Senior backend roles only",
        reject_threshold=40,
        warning_threshold=75,
    )
    await user_repo.update(user.id, {"filter_preferences": prefs})

    fetched = await user_repo.get_by_id(user.id)
    assert fetched is not None
    assert fetched.filter_preferences is not None
    assert fetched.filter_preferences.reject_threshold == 40
    assert fetched.filter_preferences.warning_threshold == 75
    assert fetched.filter_preferences.natural_language_prefs == "Senior backend roles only"


@pytest.mark.asyncio
async def test_update_filter_prefs_does_not_affect_search_prefs(user_repo):
    """Updating filter_preferences should not affect search_preferences."""
    from src.models.user import UserSearchPreferences

    user = await user_repo.create_user("both@example.com")

    search = UserSearchPreferences(keywords="python", location="Berlin")
    await user_repo.update(user.id, {"search_preferences": search})

    fprefs = UserFilterPreferences(natural_language_prefs="No clearance")
    updated = await user_repo.update(user.id, {"filter_preferences": fprefs})

    assert updated.search_preferences is not None
    assert updated.search_preferences.keywords == "python"
    assert updated.filter_preferences is not None
    assert updated.filter_preferences.natural_language_prefs == "No clearance"


# =============================================================================
# InMemoryJobRepository: filter_result Tests
# =============================================================================


@pytest.mark.asyncio
async def test_inmemory_create_job_with_filter_result():
    """Creating a job with filter_result should store it."""
    repo = InMemoryJobRepository()
    await repo.initialize()

    job = _make_job(filter_result=SAMPLE_FILTER_RESULT)
    await repo.create(job)

    retrieved = await repo.get("filter-test-1")
    assert retrieved is not None
    assert retrieved.filter_result == SAMPLE_FILTER_RESULT


@pytest.mark.asyncio
async def test_inmemory_create_job_without_filter_result():
    """Creating a job without filter_result should default to None."""
    repo = InMemoryJobRepository()
    await repo.initialize()

    job = _make_job(job_id="no-filter")
    await repo.create(job)

    retrieved = await repo.get("no-filter")
    assert retrieved is not None
    assert retrieved.filter_result is None


@pytest.mark.asyncio
async def test_inmemory_update_filter_result():
    """Updating filter_result via update() should work."""
    repo = InMemoryJobRepository()
    await repo.initialize()

    job = _make_job(job_id="update-filter")
    await repo.create(job)

    await repo.update("update-filter", {"filter_result": SAMPLE_FILTER_RESULT_REJECTED})

    retrieved = await repo.get("update-filter")
    assert retrieved is not None
    assert retrieved.filter_result == SAMPLE_FILTER_RESULT_REJECTED
    assert retrieved.filter_result["disqualified"] is True


@pytest.mark.asyncio
async def test_inmemory_filter_result_in_get_for_user():
    """filter_result should be present in get_for_user results."""
    repo = InMemoryJobRepository()
    await repo.initialize()

    job = _make_job(filter_result=SAMPLE_FILTER_RESULT)
    await repo.create(job)

    retrieved = await repo.get_for_user("filter-test-1", TEST_USER_ID)
    assert retrieved is not None
    assert retrieved.filter_result == SAMPLE_FILTER_RESULT


# =============================================================================
# SQLiteJobRepository: filter_result Tests
# =============================================================================


@pytest_asyncio.fixture
async def sqlite_repo(tmp_path):
    """Create temporary SQLite database for testing."""
    db_path = tmp_path / "test_filter_jobs.db"
    repo = SQLiteJobRepository(db_path=str(db_path))
    await repo.initialize()
    yield repo
    await repo.close()


@pytest.mark.asyncio
async def test_sqlite_create_job_with_filter_result(sqlite_repo):
    """Creating a job with filter_result should persist it."""
    job = _make_job(filter_result=SAMPLE_FILTER_RESULT)
    await sqlite_repo.create(job)

    retrieved = await sqlite_repo.get("filter-test-1")
    assert retrieved is not None
    assert retrieved.filter_result == SAMPLE_FILTER_RESULT
    assert retrieved.filter_result["score"] == 45
    assert retrieved.filter_result["red_flags"] == ["Requires security clearance", "On-site only"]


@pytest.mark.asyncio
async def test_sqlite_create_job_without_filter_result(sqlite_repo):
    """Creating a job without filter_result should default to None."""
    job = _make_job(job_id="no-filter-sqlite")
    await sqlite_repo.create(job)

    retrieved = await sqlite_repo.get("no-filter-sqlite")
    assert retrieved is not None
    assert retrieved.filter_result is None


@pytest.mark.asyncio
async def test_sqlite_update_filter_result(sqlite_repo):
    """Updating filter_result via update() should persist."""
    job = _make_job(job_id="update-filter-sqlite")
    await sqlite_repo.create(job)

    await sqlite_repo.update("update-filter-sqlite", {"filter_result": SAMPLE_FILTER_RESULT_REJECTED})

    retrieved = await sqlite_repo.get("update-filter-sqlite")
    assert retrieved is not None
    assert retrieved.filter_result == SAMPLE_FILTER_RESULT_REJECTED
    assert retrieved.filter_result["disqualified"] is True
    assert retrieved.filter_result["disqualifier_reason"] == "Requires TS/SCI security clearance"


@pytest.mark.asyncio
async def test_sqlite_filter_result_in_get_for_user(sqlite_repo):
    """filter_result should be present in get_for_user results."""
    job = _make_job(filter_result=SAMPLE_FILTER_RESULT)
    await sqlite_repo.create(job)

    retrieved = await sqlite_repo.get_for_user("filter-test-1", TEST_USER_ID)
    assert retrieved is not None
    assert retrieved.filter_result == SAMPLE_FILTER_RESULT


@pytest.mark.asyncio
async def test_sqlite_filter_result_in_pending_query(sqlite_repo):
    """filter_result should be present when querying by status."""
    job = _make_job(job_id="pending-filter", filter_result=SAMPLE_FILTER_RESULT)
    await sqlite_repo.create(job)

    jobs = await sqlite_repo.get_by_status(TEST_USER_ID, "queued")
    assert len(jobs) == 1
    assert jobs[0].filter_result == SAMPLE_FILTER_RESULT


@pytest.mark.asyncio
async def test_sqlite_filter_result_roundtrip_complex(sqlite_repo):
    """Complex filter_result with all fields should survive a round-trip."""
    complex_result = {
        "score": 82,
        "red_flags": [],
        "disqualified": False,
        "disqualifier_reason": None,
        "reasoning": "Good match for the candidate's profile. Remote-friendly, Python/Go stack.",
    }
    job = _make_job(job_id="complex-filter", filter_result=complex_result)
    await sqlite_repo.create(job)

    retrieved = await sqlite_repo.get("complex-filter")
    assert retrieved is not None
    assert retrieved.filter_result == complex_result
    assert retrieved.filter_result["red_flags"] == []


@pytest.mark.asyncio
async def test_sqlite_migration_adds_filter_result_column(tmp_path):
    """Migrating an existing DB without filter_result column should add it."""
    from piccolo.engine.sqlite import SQLiteEngine

    from src.services.tables import CVAttemptTable, Job, MagicLinkTable, UserTable

    db_path = tmp_path / "migrate_test.db"
    engine = SQLiteEngine(path=str(db_path))

    Job._meta._db = engine
    CVAttemptTable._meta._db = engine
    UserTable._meta._db = engine
    MagicLinkTable._meta._db = engine

    # Create tables WITHOUT filter_result column (simulate old schema)
    await UserTable.create_table(if_not_exists=True).run()
    await MagicLinkTable.create_table(if_not_exists=True).run()
    await Job.create_table(if_not_exists=True).run()
    await CVAttemptTable.create_table(if_not_exists=True).run()

    # Drop the filter_result column to simulate old schema
    conn = await engine.get_connection()
    try:
        # Check that filter_result exists (since table def includes it now)
        cursor = await conn.execute("PRAGMA table_info(job)")
        rows = await cursor.fetchall()
        col_names = {row["name"] for row in rows}
        assert "filter_result" in col_names  # Piccolo creates it from current schema
    finally:
        await conn.close()

    await engine.close_connection_pool()

    # Now test that SQLiteJobRepository initialization handles it fine
    repo = SQLiteJobRepository(db_path=str(db_path))
    await repo.initialize()

    # Should be able to create a job with filter_result
    job = _make_job(job_id="post-migrate", filter_result=SAMPLE_FILTER_RESULT)
    await repo.create(job)

    retrieved = await repo.get("post-migrate")
    assert retrieved is not None
    assert retrieved.filter_result == SAMPLE_FILTER_RESULT

    await repo.close()
