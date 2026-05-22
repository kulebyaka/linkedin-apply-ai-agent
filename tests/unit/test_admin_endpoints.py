"""Tests for admin API endpoints.

Each endpoint gets at least one success path + one 403 (non-admin) path,
plus the last-admin demotion guard. Uses FastAPI TestClient with dependency
overrides and a mocked AppContext to keep tests fast and hermetic.
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
from src.models.user import User, UserRole, UserSearchPreferences

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(role: UserRole, *, user_id: str = "admin-id", email: str = "admin@example.com") -> User:
    return User(
        id=user_id,
        email=email,
        display_name=email.split("@")[0],
        role=role,
    )


def _make_job(
    job_id: str = "job-1",
    *,
    user_id: str = "user-a",
    status: str = BusinessState.QUEUED.value,
    source: str = "linkedin",
    error_message: str | None = None,
    raw_input: dict | None = None,
) -> JobRecord:
    now = datetime.now(tz=timezone.utc)
    return JobRecord(
        job_id=job_id,
        user_id=user_id,
        source=source,
        mode="full",
        status=status,
        job_posting={"title": "Eng", "company": "Acme"},
        raw_input=raw_input,
        error_message=error_message,
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


def _make_mock_ctx() -> MagicMock:
    repo = AsyncMock()
    repo.initialize = AsyncMock()
    repo.close = AsyncMock()
    # Default returns; tests override per-case.
    repo.list_all_jobs = AsyncMock(return_value=[])
    repo.count_all_jobs = AsyncMock(return_value=0)
    repo.count_by_status_global = AsyncMock(return_value={})
    repo.list_jobs_with_errors = AsyncMock(return_value=[])
    repo.get_status_counts = AsyncMock(return_value={})
    repo.get = AsyncMock(return_value=None)
    repo.update = AsyncMock()
    repo.delete = AsyncMock(return_value=True)
    repo.delete_cascade = AsyncMock(return_value=True)
    repo.try_claim_failed_for_retry = AsyncMock(return_value=None)

    user_repo = AsyncMock()
    user_repo.initialize = AsyncMock()
    user_repo.list_all_users = AsyncMock(return_value=[])
    user_repo.count_admins = AsyncMock(return_value=0)
    user_repo.get_by_id = AsyncMock(return_value=None)
    user_repo.set_role = AsyncMock()

    magic_link_repo = AsyncMock()
    magic_link_repo.cleanup_expired_magic_links = AsyncMock(return_value=0)

    user_service = AsyncMock()
    user_service.set_role = AsyncMock()

    consumer_manager = MagicMock()
    consumer_manager.task = None
    consumer_manager.start = MagicMock()
    consumer_manager.stop = MagicMock()
    consumer_manager.wait_stopped = AsyncMock()
    consumer_manager.reset = MagicMock()
    consumer_manager.health_check = MagicMock(return_value={})
    consumer_manager.snapshot = MagicMock(return_value={})

    ctx = MagicMock()
    ctx.repository = repo
    ctx.user_repository = user_repo
    ctx.magic_link_repository = magic_link_repo
    ctx.user_service = user_service
    ctx.auth_service = MagicMock()
    ctx.settings = _make_mock_settings()
    ctx.job_queue = MagicMock()
    ctx.job_queue.size = MagicMock(return_value=0)
    ctx.job_queue.put = AsyncMock()
    ctx.scheduler = None
    ctx.browser = None
    ctx.consumer_manager = consumer_manager
    ctx.linkedin_init_lock = asyncio.Lock()
    ctx.admin_role_lock = asyncio.Lock()
    ctx.admin_retry_lock = asyncio.Lock()

    def _noop_bg_task(coro):
        if asyncio.iscoroutine(coro):
            coro.close()
        return MagicMock()

    ctx.create_background_task = _noop_bg_task
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_user():
    return _make_user(UserRole.ADMIN, user_id="admin-id", email="admin@example.com")


@pytest.fixture
def trial_user():
    return _make_user(UserRole.TRIAL, user_id="trial-id", email="trial@example.com")


@pytest.fixture
def mock_ctx():
    return _make_mock_ctx()


# ---------------------------------------------------------------------------
# 403 forbidden cases (non-admin)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/api/admin/jobs", None),
        ("get", "/api/admin/jobs/abc", None),
        ("post", "/api/admin/jobs/abc/retry", None),
        ("delete", "/api/admin/jobs/abc", None),
        ("post", "/api/admin/jobs/bulk-delete", {"job_ids": ["a"]}),
        ("get", "/api/admin/queue", None),
        ("post", "/api/admin/scheduler/run/user-1", None),
        ("get", "/api/admin/errors", None),
        ("get", "/api/admin/users", None),
        ("put", "/api/admin/users/user-1/role", {"role": "admin"}),
    ],
)
def test_non_admin_blocked(method, path, body, trial_user, mock_ctx):
    with _patched_client(trial_user, mock_ctx) as client:
        fn = getattr(client, method)
        resp = fn(path, json=body) if body is not None else fn(path)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/admin/jobs
# ---------------------------------------------------------------------------


class TestAdminListJobs:
    def test_returns_items_and_total(self, admin_user, mock_ctx):
        mock_ctx.repository.list_all_jobs.return_value = [
            _make_job("j1"), _make_job("j2")
        ]
        mock_ctx.repository.count_all_jobs.return_value = 5
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.get("/api/admin/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["items"][0]["job_id"] == "j1"

    def test_passes_filters_through(self, admin_user, mock_ctx):
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.get(
                "/api/admin/jobs",
                params=[
                    ("user_id", "u1"), ("user_id", "u2"),
                    ("status", "failed"),
                    ("source", "linkedin"),
                    ("search", "python"),
                    ("limit", 25), ("offset", 50),
                ],
            )
        assert resp.status_code == 200
        kwargs = mock_ctx.repository.list_all_jobs.call_args.kwargs
        assert kwargs["user_ids"] == ["u1", "u2"]
        assert kwargs["statuses"] == ["failed"]
        assert kwargs["sources"] == ["linkedin"]
        assert kwargs["search"] == "python"
        assert kwargs["limit"] == 25
        assert kwargs["offset"] == 50


# ---------------------------------------------------------------------------
# GET /api/admin/jobs/{job_id}
# ---------------------------------------------------------------------------


class TestAdminGetJob:
    def test_returns_job(self, admin_user, mock_ctx):
        mock_ctx.repository.get.return_value = _make_job("j1")
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.get("/api/admin/jobs/j1")
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "j1"

    def test_404_when_missing(self, admin_user, mock_ctx):
        mock_ctx.repository.get.return_value = None
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.get("/api/admin/jobs/nope")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/admin/jobs/{job_id}/retry
# ---------------------------------------------------------------------------


class TestAdminRetryJob:
    def test_retry_failed_job_transitions_to_queued(self, admin_user, mock_ctx):
        failed_job = _make_job(
            "j1", status=BusinessState.FAILED.value, error_message="boom",
            source="url",
            raw_input={"url": "https://example.com"},
        )
        queued_job = _make_job("j1", status=BusinessState.QUEUED.value)
        mock_ctx.repository.get.return_value = failed_job
        mock_ctx.repository.try_claim_failed_for_retry.return_value = queued_job
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.post("/api/admin/jobs/j1/retry")
        assert resp.status_code == 200
        mock_ctx.repository.try_claim_failed_for_retry.assert_called_once_with("j1")
        assert resp.json()["status"] == BusinessState.QUEUED.value

    def test_retry_409_when_no_raw_input(self, admin_user, mock_ctx):
        mock_ctx.repository.get.return_value = _make_job(
            "j1", status=BusinessState.FAILED.value, raw_input=None,
        )
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.post("/api/admin/jobs/j1/retry")
        assert resp.status_code == 409

    def test_retry_409_when_not_failed(self, admin_user, mock_ctx):
        mock_ctx.repository.get.return_value = _make_job(
            "j1", status=BusinessState.QUEUED.value
        )
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.post("/api/admin/jobs/j1/retry")
        assert resp.status_code == 409

    def test_retry_404_when_missing(self, admin_user, mock_ctx):
        mock_ctx.repository.get.return_value = None
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.post("/api/admin/jobs/nope/retry")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/admin/jobs/{job_id}
# ---------------------------------------------------------------------------


class TestAdminDeleteJob:
    def test_deletes_job(self, admin_user, mock_ctx):
        mock_ctx.repository.delete_cascade.return_value = True
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.delete("/api/admin/jobs/j1")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        mock_ctx.repository.delete_cascade.assert_called_once_with("j1")

    def test_404_when_missing(self, admin_user, mock_ctx):
        mock_ctx.repository.delete_cascade.return_value = False
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.delete("/api/admin/jobs/nope")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/admin/jobs/bulk-delete
# ---------------------------------------------------------------------------


class TestAdminBulkDelete:
    def test_bulk_delete_succeeds(self, admin_user, mock_ctx):
        mock_ctx.repository.delete_cascade.side_effect = [True, True, False]
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.post(
                "/api/admin/jobs/bulk-delete",
                json={"job_ids": ["j1", "j2", "nope"]},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 2
        assert "nope" in data["failed"]

    def test_400_when_missing_ids(self, admin_user, mock_ctx):
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.post("/api/admin/jobs/bulk-delete", json={"job_ids": []})
        assert resp.status_code == 400

    def test_400_when_too_many(self, admin_user, mock_ctx):
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.post(
                "/api/admin/jobs/bulk-delete",
                json={"job_ids": [f"j{i}" for i in range(101)]},
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/admin/queue
# ---------------------------------------------------------------------------


class TestAdminQueue:
    def test_returns_queue_state(self, admin_user, mock_ctx):
        mock_ctx.repository.count_by_status_global.side_effect = [
            {"queued": 3, "failed": 1},  # 24h
            {"queued": 10, "failed": 2},  # 7d
            {"queued": 50, "failed": 5},  # all
        ]
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.get("/api/admin/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert "consumer" in data
        assert "scheduler" in data
        assert data["counts"]["last_24h"]["queued"] == 3
        assert data["counts"]["last_7d"]["queued"] == 10
        assert data["counts"]["all_time"]["queued"] == 50


# ---------------------------------------------------------------------------
# POST /api/admin/scheduler/run/{user_id}
# ---------------------------------------------------------------------------


class TestAdminRunScheduler:
    def test_503_when_no_scheduler(self, admin_user, mock_ctx):
        mock_ctx.scheduler = None
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.post("/api/admin/scheduler/run/user-1")
        assert resp.status_code == 503

    def test_started_when_scheduler_present(self, admin_user, mock_ctx):
        scheduler = MagicMock()
        scheduler.run_search = AsyncMock(return_value=3)
        scheduler.search_in_progress = False
        mock_ctx.scheduler = scheduler
        target_user = _make_user(UserRole.TRIAL, user_id="user-1", email="u1@example.com")
        target_user.search_preferences = UserSearchPreferences(keywords="engineer")
        mock_ctx.user_repository.get_by_id.return_value = target_user
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.post("/api/admin/scheduler/run/user-1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

    def test_409_when_search_already_in_progress(self, admin_user, mock_ctx):
        scheduler = MagicMock()
        scheduler.search_in_progress = True
        mock_ctx.scheduler = scheduler
        target_user = _make_user(UserRole.TRIAL, user_id="user-1", email="u1@example.com")
        target_user.search_preferences = UserSearchPreferences(keywords="engineer")
        mock_ctx.user_repository.get_by_id.return_value = target_user
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.post("/api/admin/scheduler/run/user-1")
        assert resp.status_code == 409

    def test_404_when_user_missing(self, admin_user, mock_ctx):
        mock_ctx.scheduler = MagicMock()
        mock_ctx.user_repository.get_by_id.return_value = None
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.post("/api/admin/scheduler/run/nope")
        assert resp.status_code == 404

    def test_409_when_user_has_no_search_prefs(self, admin_user, mock_ctx):
        mock_ctx.scheduler = MagicMock()
        target_user = _make_user(UserRole.TRIAL, user_id="user-2", email="u2@example.com")
        target_user.search_preferences = None
        mock_ctx.user_repository.get_by_id.return_value = target_user
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.post("/api/admin/scheduler/run/user-2")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /api/admin/errors
# ---------------------------------------------------------------------------


class TestAdminErrors:
    def test_returns_errored_jobs(self, admin_user, mock_ctx):
        mock_ctx.repository.list_jobs_with_errors.return_value = [
            _make_job("e1", error_message="boom"),
        ]
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.get("/api/admin/errors")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["job_id"] == "e1"


# ---------------------------------------------------------------------------
# GET /api/admin/users
# ---------------------------------------------------------------------------


class TestAdminListUsers:
    def test_returns_users_with_counts(self, admin_user, mock_ctx):
        u1 = _make_user(UserRole.TRIAL, user_id="u1", email="u1@example.com")
        mock_ctx.user_repository.list_all_users.return_value = [u1]
        mock_ctx.repository.get_status_counts.return_value = {"queued": 2}
        mock_ctx.repository.list_all_jobs.return_value = [_make_job("j1", user_id="u1")]
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.get("/api/admin/users")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        row = data["items"][0]
        assert row["user"]["id"] == "u1"
        assert row["job_counts"] == {"queued": 2}
        assert row["last_job_at"] is not None


# ---------------------------------------------------------------------------
# PUT /api/admin/users/{user_id}/role
# ---------------------------------------------------------------------------


class TestAdminSetUserRole:
    def test_sets_role(self, admin_user, mock_ctx):
        promoted = _make_user(UserRole.ADMIN, user_id="u1", email="u1@example.com")
        mock_ctx.user_service.set_role.return_value = promoted
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.put(
                "/api/admin/users/u1/role", json={"role": "admin"}
            )
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"
        mock_ctx.user_service.set_role.assert_called_once_with(
            "u1", UserRole.ADMIN
        )

    def test_400_on_invalid_role(self, admin_user, mock_ctx):
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.put(
                "/api/admin/users/u1/role", json={"role": "superuser"}
            )
        assert resp.status_code == 400

    def test_404_when_user_missing(self, admin_user, mock_ctx):
        mock_ctx.user_service.set_role.side_effect = KeyError("missing")
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.put(
                "/api/admin/users/missing/role", json={"role": "premium"}
            )
        assert resp.status_code == 404

    def test_409_last_admin_demotion_blocked(self, admin_user, mock_ctx):
        from src.services.auth.user_service import LastAdminError
        # The DB-side guard raises LastAdminError when the demotion would
        # leave zero admins.
        mock_ctx.user_service.set_role.side_effect = LastAdminError(
            "Cannot demote the last remaining admin"
        )
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.put(
                f"/api/admin/users/{admin_user.id}/role",
                json={"role": "trial"},
            )
        assert resp.status_code == 409

    def test_409_blocks_demoting_other_last_admin(self, admin_user, mock_ctx):
        from src.services.auth.user_service import LastAdminError
        mock_ctx.user_service.set_role.side_effect = LastAdminError(
            "Cannot demote the last remaining admin"
        )
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.put(
                "/api/admin/users/solo-admin/role",
                json={"role": "trial"},
            )
        assert resp.status_code == 409

    def test_admin_can_demote_self_if_other_admin_exists(self, admin_user, mock_ctx):
        demoted = _make_user(
            UserRole.TRIAL, user_id=admin_user.id, email=admin_user.email
        )
        mock_ctx.user_service.set_role.return_value = demoted
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.put(
                f"/api/admin/users/{admin_user.id}/role",
                json={"role": "trial"},
            )
        assert resp.status_code == 200
        mock_ctx.user_service.set_role.assert_called_once()


# ---------------------------------------------------------------------------
# GET /api/auth/me — role exposed
# ---------------------------------------------------------------------------


class TestAuthMeIncludesRole:
    def test_role_in_response(self, admin_user, mock_ctx):
        with _patched_client(admin_user, mock_ctx) as client:
            resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"
