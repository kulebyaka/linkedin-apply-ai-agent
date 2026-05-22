"""Tests for admin-scope JobRepository methods.

Covers list_all_jobs, count_all_jobs, count_by_status_global,
list_jobs_with_errors, and delete idempotency on both
InMemoryJobRepository and SQLiteJobRepository.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from src.models.unified import JobRecord
from src.services.db.job_repository import (
    InMemoryJobRepository,
    SQLiteJobRepository,
)


def _job(
    job_id: str,
    *,
    user_id: str = "user-a",
    source: str = "linkedin",
    status: str = "queued",
    title: str = "Engineer",
    company: str = "Acme",
    error_message: str | None = None,
    last_scrape_error: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> JobRecord:
    now = datetime.now(tz=timezone.utc)
    return JobRecord(
        job_id=job_id,
        user_id=user_id,
        source=source,
        mode="full",
        status=status,
        job_posting={"title": title, "company": company},
        error_message=error_message,
        last_scrape_error=last_scrape_error,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )


# =============================================================================
# Fixtures providing both repository implementations under the same name
# so each test runs against both.
# =============================================================================


@pytest_asyncio.fixture
async def memory_repo():
    repo = InMemoryJobRepository()
    await repo.initialize()
    yield repo
    await repo.close()


@pytest_asyncio.fixture
async def sqlite_repo(tmp_path):
    db_path = tmp_path / "admin_jobs.db"
    repo = SQLiteJobRepository(db_path=str(db_path))
    await repo.initialize()
    yield repo
    await repo.close()


@pytest_asyncio.fixture(params=["memory", "sqlite"])
async def repo(request, tmp_path):
    """Parametrised fixture yielding an initialized repo per backend."""
    if request.param == "memory":
        r = InMemoryJobRepository()
        await r.initialize()
        yield r
        await r.close()
    else:
        db_path = tmp_path / "admin_param.db"
        r = SQLiteJobRepository(db_path=str(db_path))
        await r.initialize()
        yield r
        await r.close()


# =============================================================================
# list_all_jobs / count_all_jobs
# =============================================================================


@pytest.mark.asyncio
async def test_list_all_jobs_returns_jobs_across_users(repo):
    await repo.create(_job("j1", user_id="user-a"))
    await repo.create(_job("j2", user_id="user-b"))
    await repo.create(_job("j3", user_id="user-c"))

    items = await repo.list_all_jobs()
    assert {j.job_id for j in items} == {"j1", "j2", "j3"}


@pytest.mark.asyncio
async def test_list_all_jobs_filter_by_user_ids(repo):
    await repo.create(_job("j1", user_id="user-a"))
    await repo.create(_job("j2", user_id="user-b"))
    await repo.create(_job("j3", user_id="user-c"))

    items = await repo.list_all_jobs(user_ids=["user-a", "user-c"])
    assert {j.job_id for j in items} == {"j1", "j3"}


@pytest.mark.asyncio
async def test_list_all_jobs_filter_by_status(repo):
    await repo.create(_job("q1", status="queued"))
    await repo.create(_job("p1", status="pending"))
    await repo.create(_job("f1", status="failed"))

    items = await repo.list_all_jobs(statuses=["failed"])
    assert [j.job_id for j in items] == ["f1"]


@pytest.mark.asyncio
async def test_list_all_jobs_filter_by_source(repo):
    await repo.create(_job("a", source="linkedin"))
    await repo.create(_job("b", source="manual"))
    await repo.create(_job("c", source="url"))

    items = await repo.list_all_jobs(sources=["manual", "url"])
    assert {j.job_id for j in items} == {"b", "c"}


@pytest.mark.asyncio
async def test_list_all_jobs_filter_by_created_window(repo):
    now = datetime.now(tz=timezone.utc)
    old = now - timedelta(days=30)
    mid = now - timedelta(days=5)
    new = now

    await repo.create(_job("old", created_at=old, updated_at=old))
    await repo.create(_job("mid", created_at=mid, updated_at=mid))
    await repo.create(_job("new", created_at=new, updated_at=new))

    items = await repo.list_all_jobs(
        created_from=now - timedelta(days=10),
        created_to=now - timedelta(days=1),
    )
    assert {j.job_id for j in items} == {"mid"}


@pytest.mark.asyncio
async def test_list_all_jobs_free_text_search_title(repo):
    await repo.create(_job("j1", title="Senior Python Engineer", company="Acme"))
    await repo.create(_job("j2", title="Java Developer", company="Globex"))
    await repo.create(_job("j3", title="Devops", company="Pythonistas Inc"))

    title_hits = await repo.list_all_jobs(search="python")
    assert {j.job_id for j in title_hits} == {"j1", "j3"}


@pytest.mark.asyncio
async def test_list_all_jobs_search_matches_error_message(repo):
    await repo.create(_job("j1", error_message="Timeout while scraping"))
    await repo.create(_job("j2", error_message=None))

    hits = await repo.list_all_jobs(search="timeout")
    assert [j.job_id for j in hits] == ["j1"]


@pytest.mark.asyncio
async def test_list_all_jobs_pagination(repo):
    now = datetime.now(tz=timezone.utc)
    for i in range(5):
        await repo.create(
            _job(f"j{i}", created_at=now - timedelta(seconds=i))
        )

    page1 = await repo.list_all_jobs(limit=2, offset=0)
    page2 = await repo.list_all_jobs(limit=2, offset=2)
    page3 = await repo.list_all_jobs(limit=2, offset=4)
    assert [j.job_id for j in page1] == ["j0", "j1"]
    assert [j.job_id for j in page2] == ["j2", "j3"]
    assert [j.job_id for j in page3] == ["j4"]


@pytest.mark.asyncio
async def test_list_all_jobs_ordered_created_at_desc(repo):
    now = datetime.now(tz=timezone.utc)
    await repo.create(_job("old", created_at=now - timedelta(hours=2)))
    await repo.create(_job("mid", created_at=now - timedelta(hours=1)))
    await repo.create(_job("new", created_at=now))

    items = await repo.list_all_jobs()
    assert [j.job_id for j in items] == ["new", "mid", "old"]


@pytest.mark.asyncio
async def test_count_all_jobs_respects_filters(repo):
    await repo.create(_job("j1", user_id="user-a", status="queued"))
    await repo.create(_job("j2", user_id="user-a", status="failed"))
    await repo.create(_job("j3", user_id="user-b", status="failed"))

    assert await repo.count_all_jobs() == 3
    assert await repo.count_all_jobs(statuses=["failed"]) == 2
    assert await repo.count_all_jobs(user_ids=["user-a"], statuses=["failed"]) == 1


# =============================================================================
# count_by_status_global
# =============================================================================


@pytest.mark.asyncio
async def test_count_by_status_global_no_window(repo):
    await repo.create(_job("a", status="queued"))
    await repo.create(_job("b", status="queued"))
    await repo.create(_job("c", status="failed"))

    counts = await repo.count_by_status_global()
    assert counts["queued"] == 2
    assert counts["failed"] == 1


@pytest.mark.asyncio
async def test_count_by_status_global_with_window(repo):
    now = datetime.now(tz=timezone.utc)
    await repo.create(_job("recent", status="queued", created_at=now))
    await repo.create(
        _job("old", status="queued", created_at=now - timedelta(hours=48))
    )

    last_24h = await repo.count_by_status_global(window_hours=24)
    assert last_24h.get("queued") == 1


# =============================================================================
# list_jobs_with_errors
# =============================================================================


@pytest.mark.asyncio
async def test_list_jobs_with_errors_filters_to_errored(repo):
    await repo.create(_job("clean", error_message=None, last_scrape_error=None))
    await repo.create(_job("err", error_message="boom"))
    await repo.create(_job("scrape", last_scrape_error="404"))

    items = await repo.list_jobs_with_errors()
    assert {j.job_id for j in items} == {"err", "scrape"}


@pytest.mark.asyncio
async def test_list_jobs_with_errors_ordered_updated_at_desc(repo):
    now = datetime.now(tz=timezone.utc)
    await repo.create(
        _job(
            "old-err",
            error_message="x",
            updated_at=now - timedelta(hours=2),
            created_at=now - timedelta(hours=3),
        )
    )
    await repo.create(
        _job(
            "fresh-err",
            error_message="y",
            updated_at=now,
            created_at=now - timedelta(hours=1),
        )
    )

    items = await repo.list_jobs_with_errors()
    assert [j.job_id for j in items] == ["fresh-err", "old-err"]


@pytest.mark.asyncio
async def test_list_jobs_with_errors_pagination(repo):
    now = datetime.now(tz=timezone.utc)
    for i in range(5):
        await repo.create(
            _job(
                f"e{i}",
                error_message=f"err-{i}",
                updated_at=now - timedelta(seconds=i),
            )
        )

    page1 = await repo.list_jobs_with_errors(limit=2, offset=0)
    page2 = await repo.list_jobs_with_errors(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {j.job_id for j in page1}.isdisjoint({j.job_id for j in page2})


# =============================================================================
# delete idempotency
# =============================================================================


@pytest.mark.asyncio
async def test_delete_returns_true_then_false(repo):
    await repo.create(_job("d1"))

    first = await repo.delete("d1")
    second = await repo.delete("d1")
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_delete_unknown_returns_false(repo):
    assert await repo.delete("never-existed") is False
