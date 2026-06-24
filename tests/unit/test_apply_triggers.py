"""Tests for Task 7 triggers: trigger_apply, auto_apply prep branch, apply API, WS auth.

Covers the wiring that turns an approved/auto-applied job into a dispatched Easy
Apply run (or a fail-fast ``needs_extension`` park), plus the manual retry
endpoint and the WebSocket relay's JWT handshake rejection.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.api.deps import get_current_user
from src.api.main import app
from src.bridge.session_store import SessionStore
from src.bridge.ws_relay import WsRelay
from src.models.state_machine import BusinessState
from src.models.unified import JobRecord
from src.models.user import ApplyProfile, User, UserRole
from src.services.db.in_memory_repository import InMemoryJobRepository
from src.services.jobs.apply_trigger import trigger_apply

TEST_USER_ID = "user-apply-1"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _make_user(user_id: str = TEST_USER_ID) -> User:
    return User(
        id=user_id,
        email="apply@example.com",
        display_name="Apply Tester",
        role=UserRole.TRIAL,
        apply_profile=ApplyProfile(years_experience=5),
        master_cv_json={"contact": {"full_name": "Apply Tester", "email": "apply@example.com"}},
        auto_apply=True,
    )


def _make_job(job_id: str = "job-apply-1", *, status: str = "approved") -> JobRecord:
    now = datetime.now(tz=timezone.utc)
    return JobRecord(
        job_id=job_id,
        user_id=TEST_USER_ID,
        source="linkedin",
        mode="full",
        status=status,
        job_posting={"title": "Engineer", "company": "Acme", "url": "https://x/jobs/1"},
        current_pdf_path="/tmp/cv.pdf",
        created_at=now,
        updated_at=now,
    )


def _make_trigger_ctx(repo, *, connected: bool, user: User | None = None) -> MagicMock:
    """A MagicMock AppContext wired for trigger_apply with a real repo."""
    ctx = MagicMock()
    ctx.repository = repo
    session_store = AsyncMock()
    session_store.is_connected = AsyncMock(return_value=connected)
    ctx.session_store = session_store
    user_repo = AsyncMock()
    user_repo.get_by_id = AsyncMock(return_value=user or _make_user())
    ctx.user_repository = user_repo
    ctx.workflow_dispatcher = MagicMock()
    ctx.workflow_dispatcher.dispatch_application = AsyncMock()

    def _noop_bg_task(coro):
        if asyncio.iscoroutine(coro):
            coro.close()
        return MagicMock()

    ctx.create_background_task = _noop_bg_task
    return ctx


# --------------------------------------------------------------------------- #
# trigger_apply
# --------------------------------------------------------------------------- #
class TestTriggerApply:
    def test_no_session_parks_needs_extension(self):
        async def run():
            repo = InMemoryJobRepository()
            await repo.initialize()
            await repo.create(_make_job(status="approved"))
            ctx = _make_trigger_ctx(repo, connected=False)

            result = await trigger_apply(ctx, "job-apply-1", TEST_USER_ID)

            assert result == BusinessState.NEEDS_EXTENSION
            job = await repo.get("job-apply-1")
            assert job.status == BusinessState.NEEDS_EXTENSION
            assert job.error_message  # user-facing prompt set
            ctx.workflow_dispatcher.dispatch_application.assert_not_called()

        asyncio.run(run())

    def test_connected_dispatches_with_state(self):
        async def run():
            repo = InMemoryJobRepository()
            await repo.initialize()
            await repo.create(_make_job(status="approved"))
            ctx = _make_trigger_ctx(repo, connected=True)

            result = await trigger_apply(ctx, "job-apply-1", TEST_USER_ID)

            assert result == BusinessState.APPLYING
            job = await repo.get("job-apply-1")
            assert job.status == BusinessState.APPLYING

            ctx.workflow_dispatcher.dispatch_application.assert_called_once()
            kwargs = ctx.workflow_dispatcher.dispatch_application.call_args.kwargs
            assert kwargs["job_id"] == "job-apply-1"
            assert kwargs["user_id"] == TEST_USER_ID
            init = kwargs["initial_state"]
            assert init["job_url"] == "https://x/jobs/1"
            assert init["pdf_path"] == "/tmp/cv.pdf"
            assert isinstance(init["apply_profile"], ApplyProfile)
            assert init["contact_info"].full_name == "Apply Tester"

        asyncio.run(run())


# --------------------------------------------------------------------------- #
# auto_apply branch in save_to_db_node
# --------------------------------------------------------------------------- #
class TestAutoApplyPrepBranch:
    def _run_save(self, *, auto_apply: bool, connected: bool):
        from src.agents.preparation_workflow import save_to_db_node

        async def run():
            repo = InMemoryJobRepository()
            await repo.initialize()
            # Job already exists in PROCESSING (created earlier in the pipeline).
            await repo.create(_make_job(status="processing"))

            user = _make_user()
            user.auto_apply = auto_apply
            ctx = _make_trigger_ctx(repo, connected=connected, user=user)

            config = {
                "configurable": {
                    "repository": repo,
                    "user_repository": ctx.user_repository,
                    "ctx": ctx,
                }
            }
            state = {
                "job_id": "job-apply-1",
                "user_id": TEST_USER_ID,
                "mode": "full",
                "job_posting": {"title": "Engineer", "url": "https://x/jobs/1"},
                "tailored_cv_json": {"contact": {"full_name": "T"}},
                "tailored_cv_pdf_path": "/tmp/cv.pdf",
            }
            await save_to_db_node(state, config)
            return ctx, await repo.get("job-apply-1")

        return asyncio.run(run())

    def test_auto_apply_off_stays_pending(self):
        ctx, job = self._run_save(auto_apply=False, connected=True)
        assert job.status == BusinessState.PENDING
        ctx.workflow_dispatcher.dispatch_application.assert_not_called()

    def test_auto_apply_on_connected_dispatches(self):
        ctx, job = self._run_save(auto_apply=True, connected=True)
        assert job.status == BusinessState.APPLYING
        ctx.workflow_dispatcher.dispatch_application.assert_called_once()

    def test_auto_apply_on_no_extension_parks(self):
        ctx, job = self._run_save(auto_apply=True, connected=False)
        assert job.status == BusinessState.NEEDS_EXTENSION
        ctx.workflow_dispatcher.dispatch_application.assert_not_called()


# --------------------------------------------------------------------------- #
# API: POST /api/jobs/{id}/apply  +  WS handshake
# --------------------------------------------------------------------------- #
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


def _make_api_ctx(repo, *, connected: bool, ws_relay=None) -> MagicMock:
    ctx = MagicMock()
    ctx.repository = repo
    ctx.settings = _make_mock_settings()
    ctx.scheduler = None
    ctx.browser = None

    consumer_manager = MagicMock()
    consumer_manager.task = None
    consumer_manager.stop = MagicMock()
    consumer_manager.wait_stopped = AsyncMock()
    ctx.consumer_manager = consumer_manager

    magic_link_repo = AsyncMock()
    magic_link_repo.cleanup_expired_magic_links = AsyncMock(return_value=0)
    ctx.magic_link_repository = magic_link_repo

    user_repo = AsyncMock()
    user_repo.initialize = AsyncMock()
    user_repo.get_by_id = AsyncMock(return_value=_make_user())
    ctx.user_repository = user_repo

    session_store = AsyncMock()
    session_store.is_connected = AsyncMock(return_value=connected)
    ctx.session_store = session_store

    ctx.workflow_dispatcher = MagicMock()
    ctx.workflow_dispatcher.dispatch_application = AsyncMock()

    ctx.ws_relay = ws_relay

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


class TestApplyEndpoint:
    def test_apply_connected_dispatches(self):
        repo = InMemoryJobRepository()
        asyncio.run(_seed(repo, _make_job(status="needs_extension")))
        ctx = _make_api_ctx(repo, connected=True)

        with _patched_client(_make_user(), ctx) as client:
            resp = client.post("/api/jobs/job-apply-1/apply")

        assert resp.status_code == 200
        assert resp.json()["status"] == BusinessState.APPLYING.value
        ctx.workflow_dispatcher.dispatch_application.assert_called_once()

    def test_apply_no_session_parks_needs_extension(self):
        repo = InMemoryJobRepository()
        asyncio.run(_seed(repo, _make_job(status="approved")))
        ctx = _make_api_ctx(repo, connected=False)

        with _patched_client(_make_user(), ctx) as client:
            resp = client.post("/api/jobs/job-apply-1/apply")

        assert resp.status_code == 200
        assert resp.json()["status"] == BusinessState.NEEDS_EXTENSION.value

    def test_apply_wrong_status_409(self):
        repo = InMemoryJobRepository()
        asyncio.run(_seed(repo, _make_job(status="pending")))
        ctx = _make_api_ctx(repo, connected=True)

        with _patched_client(_make_user(), ctx) as client:
            resp = client.post("/api/jobs/job-apply-1/apply")

        assert resp.status_code == 409

    def test_apply_missing_job_404(self):
        repo = InMemoryJobRepository()
        asyncio.run(repo.initialize())
        ctx = _make_api_ctx(repo, connected=True)

        with _patched_client(_make_user(), ctx) as client:
            resp = client.post("/api/jobs/nope/apply")

        assert resp.status_code == 404


class TestWsHandshake:
    def _ws_relay(self, *, valid: bool) -> WsRelay:
        auth = MagicMock()
        if valid:
            auth.decode_jwt = MagicMock(return_value={"user_id": TEST_USER_ID})
        else:
            auth.decode_jwt = MagicMock(side_effect=ValueError("bad token"))
        return WsRelay(SessionStore(), auth)

    def test_invalid_token_closes_4401(self):
        repo = InMemoryJobRepository()
        asyncio.run(repo.initialize())
        relay = self._ws_relay(valid=False)
        ctx = _make_api_ctx(repo, connected=False, ws_relay=relay)

        with _patched_client(_make_user(), ctx) as client:
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect("/ws/extension") as ws:
                    ws.send_json({"type": "auth", "token": "bad"})
                    ws.receive_json()
            assert exc.value.code == 4401

    def test_missing_auth_frame_closes_4401(self):
        repo = InMemoryJobRepository()
        asyncio.run(repo.initialize())
        relay = self._ws_relay(valid=True)
        ctx = _make_api_ctx(repo, connected=False, ws_relay=relay)

        with _patched_client(_make_user(), ctx) as client:
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect("/ws/extension") as ws:
                    ws.send_json({"type": "not_auth"})
                    ws.receive_json()
            assert exc.value.code == 4401

    def test_valid_token_gets_ready(self):
        repo = InMemoryJobRepository()
        asyncio.run(repo.initialize())
        relay = self._ws_relay(valid=True)
        ctx = _make_api_ctx(repo, connected=False, ws_relay=relay)

        with _patched_client(_make_user(), ctx) as client:
            with client.websocket_connect("/ws/extension") as ws:
                ws.send_json({"type": "auth", "token": "good"})
                msg = ws.receive_json()
                assert msg == {"type": "ready"}


async def _seed(repo: InMemoryJobRepository, job: JobRecord) -> None:
    await repo.initialize()
    await repo.create(job)
