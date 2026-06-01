"""Tests for the user-scoped GET /api/jobs list endpoint.

Exercises the route end-to-end against a real JobOrchestrator backed by a
real InMemoryJobRepository, so user scoping and filter semantics are tested
for real (not just mock pass-through). The surrounding AppContext is a
MagicMock so the FastAPI lifespan can boot without external services.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app, get_current_user
from src.models.state_machine import BusinessState
from src.models.unified import JobRecord
from src.models.user import User, UserRole
from src.services.db.in_memory_repository import InMemoryJobRepository
from src.services.jobs.job_orchestrator import JobOrchestrator


def _make_user(*, user_id: str, email: str) -> User:
    return User(
        id=user_id,
        email=email,
        display_name=email.split("@")[0],
        role=UserRole.TRIAL,
    )


def _make_job(
    job_id: str,
    *,
    user_id: str,
    status: str = BusinessState.PENDING.value,
    source: str = "linkedin",
    title: str = "Engineer",
    company: str = "Acme",
) -> JobRecord:
    now = datetime.now(tz=timezone.utc)
    return JobRecord(
        job_id=job_id,
        user_id=user_id,
        source=source,
        mode="full",
        status=status,
        job_posting={"title": title, "company": company},
        created_at=now,
        updated_at=now,
    )


def _make_mock_settings() -> MagicMock:
    s = MagicMock()
    s.repo_type = "memory"
    s.db_path = ":memory:"
    s.seed_jobs_from_file = False
    s.linkedin_search_schedule_enabled = False
    s.app_url = "http://localhost:5173"
    s.jwt_expiry_days = 30
    s.dev_auth_bypass = False
    s.generated_cvs_dir = "./data/generated_cvs"
    return s


def _make_ctx_with_real_repo(repo: InMemoryJobRepository) -> MagicMock:
    """Build a mock AppContext using a real repository + orchestrator."""
    user_repo = AsyncMock()
    user_repo.initialize = AsyncMock()

    magic_link_repo = AsyncMock()
    magic_link_repo.cleanup_expired_magic_links = AsyncMock(return_value=0)

    consumer_manager = MagicMock()
    consumer_manager.task = None
    consumer_manager.stop = MagicMock()
    consumer_manager.wait_stopped = AsyncMock()

    ctx = MagicMock()
    ctx.repository = repo
    ctx.user_repository = user_repo
    ctx.magic_link_repository = magic_link_repo
    ctx.auth_service = MagicMock()
    ctx.settings = _make_mock_settings()
    ctx.scheduler = None
    ctx.browser = None
    ctx.consumer_manager = consumer_manager

    def _noop_bg_task(coro):
        if asyncio.iscoroutine(coro):
            coro.close()
        return MagicMock()

    ctx.create_background_task = _noop_bg_task
    ctx.orchestrator = JobOrchestrator(ctx)
    return ctx


@contextmanager
def _patched_client(test_user: User, mock_ctx: MagicMock):
    mock_settings = _make_mock_settings()
    with (
        patch("src.api.main.create_app_context", return_value=mock_ctx),
        patch("src.api.main.settings", mock_settings),
    ):
        app.dependency_overrides[get_current_user] = lambda: test_user
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def user_a() -> User:
    return _make_user(user_id="user-a", email="a@example.com")


@pytest.fixture
def user_b() -> User:
    return _make_user(user_id="user-b", email="b@example.com")


async def _seed(repo: InMemoryJobRepository, jobs: list[JobRecord]) -> None:
    await repo.initialize()
    for job in jobs:
        await repo.create(job)


def test_user_only_sees_own_jobs(user_a, user_b):
    repo = InMemoryJobRepository()
    asyncio.run(
        _seed(
            repo,
            [
                _make_job("a1", user_id=user_a.id),
                _make_job("a2", user_id=user_a.id),
                _make_job("b1", user_id=user_b.id),
            ],
        )
    )
    ctx = _make_ctx_with_real_repo(repo)

    with _patched_client(user_a, ctx) as client:
        resp = client.get("/api/jobs")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    returned_ids = {item["job_id"] for item in data["items"]}
    assert returned_ids == {"a1", "a2"}
    # No row from user_b leaks even with no filters.
    assert all(item["user_id"] == user_a.id for item in data["items"])


def test_status_filter_narrows_and_total_reflects_filtered_count(user_a):
    repo = InMemoryJobRepository()
    asyncio.run(
        _seed(
            repo,
            [
                _make_job("p1", user_id=user_a.id, status=BusinessState.PENDING.value),
                _make_job("p2", user_id=user_a.id, status=BusinessState.PENDING.value),
                _make_job("f1", user_id=user_a.id, status=BusinessState.FAILED.value),
            ],
        )
    )
    ctx = _make_ctx_with_real_repo(repo)

    with _patched_client(user_a, ctx) as client:
        resp = client.get("/api/jobs", params={"status": "failed"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert {item["job_id"] for item in data["items"]} == {"f1"}


def test_source_filter_narrows(user_a):
    repo = InMemoryJobRepository()
    asyncio.run(
        _seed(
            repo,
            [
                _make_job("li1", user_id=user_a.id, source="linkedin"),
                _make_job("url1", user_id=user_a.id, source="url"),
                _make_job("url2", user_id=user_a.id, source="url"),
            ],
        )
    )
    ctx = _make_ctx_with_real_repo(repo)

    with _patched_client(user_a, ctx) as client:
        resp = client.get("/api/jobs", params={"source": "url"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert {item["job_id"] for item in data["items"]} == {"url1", "url2"}


def test_search_filter_narrows_on_title(user_a):
    repo = InMemoryJobRepository()
    asyncio.run(
        _seed(
            repo,
            [
                _make_job("s1", user_id=user_a.id, title="Senior Python Engineer"),
                _make_job("s2", user_id=user_a.id, title="Java Developer"),
            ],
        )
    )
    ctx = _make_ctx_with_real_repo(repo)

    with _patched_client(user_a, ctx) as client:
        resp = client.get("/api/jobs", params={"search": "python"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert {item["job_id"] for item in data["items"]} == {"s1"}


def test_total_reflects_filtered_count_not_page_size(user_a):
    repo = InMemoryJobRepository()
    jobs = [
        _make_job(f"j{i}", user_id=user_a.id, status=BusinessState.PENDING.value)
        for i in range(5)
    ]
    asyncio.run(_seed(repo, jobs))
    ctx = _make_ctx_with_real_repo(repo)

    with _patched_client(user_a, ctx) as client:
        resp = client.get("/api/jobs", params={"limit": 2})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5  # filtered count, not the page size
    assert len(data["items"]) == 2
    assert data["limit"] == 2


def test_limit_capped_at_100(user_a):
    repo = InMemoryJobRepository()
    asyncio.run(_seed(repo, [_make_job("j1", user_id=user_a.id)]))
    ctx = _make_ctx_with_real_repo(repo)

    with _patched_client(user_a, ctx) as client:
        resp = client.get("/api/jobs", params={"limit": 101})

    # Query(le=100) rejects values above the cap with 422.
    assert resp.status_code == 422


def test_invalid_status_token_returns_400(user_a):
    repo = InMemoryJobRepository()
    asyncio.run(_seed(repo, [_make_job("j1", user_id=user_a.id)]))
    ctx = _make_ctx_with_real_repo(repo)

    with _patched_client(user_a, ctx) as client:
        resp = client.get("/api/jobs", params={"status": "bogus"})

    assert resp.status_code == 400
