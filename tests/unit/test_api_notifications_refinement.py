"""API tests for notification + filter-refinement endpoints.

Uses FastAPI's TestClient with create_app_context + settings patched and auth
overridden, mirroring tests/unit/test_api_filter_preferences.py.
"""

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app, get_current_user
from src.models.job_filter import RefinementProposal, UserFilterPreferences
from src.models.notification import Notification
from src.models.user import User


def _make_user() -> User:
    return User(
        id="u1",
        email="t@example.com",
        display_name="T",
        filter_preferences=UserFilterPreferences(custom_prompt="HAND"),
    )


def _make_mock_settings() -> MagicMock:
    s = MagicMock()
    s.repo_type = "memory"
    s.db_path = ":memory:"
    s.seed_jobs_from_file = False
    s.linkedin_search_schedule_enabled = False
    s.auto_refine_enabled = False
    s.app_url = "http://localhost:5173"
    s.jwt_expiry_days = 30
    return s


def _make_mock_ctx(test_user: User) -> MagicMock:
    repo = AsyncMock()
    repo.initialize = AsyncMock()
    repo.close = AsyncMock()
    repo.mark_refine_signals = AsyncMock()

    user_repo = AsyncMock()
    user_repo.initialize = AsyncMock()
    user_repo.update = AsyncMock(return_value=test_user)
    user_repo.get_pending_proposal = AsyncMock(return_value=None)
    user_repo.clear_pending_proposal = AsyncMock()

    notif_repo = AsyncMock()

    magic_link_repo = AsyncMock()
    magic_link_repo.cleanup_expired_magic_links = AsyncMock(return_value=0)

    consumer_manager = MagicMock()
    consumer_manager.task = None
    consumer_manager.wait_stopped = AsyncMock()

    ctx = MagicMock()
    ctx.repository = repo
    ctx.user_repository = user_repo
    ctx.notification_repository = notif_repo
    ctx.magic_link_repository = magic_link_repo
    ctx.settings = _make_mock_settings()
    ctx.scheduler = None
    ctx.refinement_scheduler = None
    ctx.browser = None
    ctx.consumer_manager = consumer_manager
    ctx.linkedin_init_lock = asyncio.Lock()

    def _noop_bg_task(coro):
        if asyncio.iscoroutine(coro):
            coro.close()
        return MagicMock()

    ctx.create_background_task = _noop_bg_task
    return ctx


@contextmanager
def _client(test_user, mock_ctx, *, authed=True):
    mock_settings = _make_mock_settings()
    with (
        patch("src.api.main.create_app_context", return_value=mock_ctx),
        patch("src.api.main.settings", mock_settings),
    ):
        if authed:
            app.dependency_overrides[get_current_user] = lambda: test_user
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def test_user():
    return _make_user()


@pytest.fixture
def mock_ctx(test_user):
    return _make_mock_ctx(test_user)


def test_list_notifications(test_user, mock_ctx):
    mock_ctx.notification_repository.list_for_user = AsyncMock(
        return_value=[
            Notification(id="n1", user_id="u1", type="filter_refinement", title="A")
        ]
    )
    with _client(test_user, mock_ctx) as client:
        resp = client.get("/api/notifications")
    assert resp.status_code == 200
    assert resp.json()[0]["title"] == "A"


def test_unread_count(test_user, mock_ctx):
    mock_ctx.notification_repository.unread_count = AsyncMock(return_value=3)
    with _client(test_user, mock_ctx) as client:
        resp = client.get("/api/notifications/unread-count")
    assert resp.status_code == 200
    assert resp.json() == {"count": 3}


def test_mark_read_not_found(test_user, mock_ctx):
    mock_ctx.notification_repository.mark_read = AsyncMock(return_value=False)
    with _client(test_user, mock_ctx) as client:
        resp = client.put("/api/notifications/nope/read")
    assert resp.status_code == 404


def test_mark_all_read(test_user, mock_ctx):
    mock_ctx.notification_repository.mark_all_read = AsyncMock(return_value=5)
    with _client(test_user, mock_ctx) as client:
        resp = client.put("/api/notifications/read-all")
    assert resp.status_code == 200
    assert resp.json() == {"updated": 5}


def test_notifications_require_auth(test_user, mock_ctx):
    with _client(test_user, mock_ctx, authed=False) as client:
        resp = client.get("/api/notifications")
    assert resp.status_code == 401


def test_get_refinement_none(test_user, mock_ctx):
    with _client(test_user, mock_ctx) as client:
        resp = client.get("/api/users/me/filter-preferences/refinement")
    assert resp.status_code == 200
    body = resp.json()
    assert body["proposal"] is None


def test_accept_refinement_applies_block(test_user, mock_ctx):
    proposal = RefinementProposal(
        proposed_learned_block="## Auto-learned criteria\n- new rule",
        rationale="why",
        signal_job_ids=["j1"],
        decline_count=1,
        override_count=0,
    )
    mock_ctx.user_repository.get_pending_proposal = AsyncMock(return_value=proposal)

    captured = {}

    async def _update(user_id, updates):
        captured["updates"] = updates
        return test_user

    mock_ctx.user_repository.update = AsyncMock(side_effect=_update)

    with _client(test_user, mock_ctx) as client:
        resp = client.post("/api/users/me/filter-preferences/refinement/accept")

    assert resp.status_code == 200
    new_prompt = captured["updates"]["filter_preferences"].custom_prompt
    assert "HAND" in new_prompt  # hand-written preserved
    assert "new rule" in new_prompt
    mock_ctx.user_repository.clear_pending_proposal.assert_awaited_once()
    mock_ctx.repository.mark_refine_signals.assert_awaited_once_with(["j1"], "consumed")


def test_accept_refinement_404_when_none(test_user, mock_ctx):
    mock_ctx.user_repository.get_pending_proposal = AsyncMock(return_value=None)
    with _client(test_user, mock_ctx) as client:
        resp = client.post("/api/users/me/filter-preferences/refinement/accept")
    assert resp.status_code == 404


def test_reject_refinement(test_user, mock_ctx):
    proposal = RefinementProposal(
        proposed_learned_block="## Auto-learned criteria\n- x",
        rationale="why",
        signal_job_ids=["j1", "j2"],
    )
    mock_ctx.user_repository.get_pending_proposal = AsyncMock(return_value=proposal)
    with _client(test_user, mock_ctx) as client:
        resp = client.post("/api/users/me/filter-preferences/refinement/reject")
    assert resp.status_code == 200
    assert resp.json() == {"status": "rejected"}
    mock_ctx.repository.mark_refine_signals.assert_awaited_once_with(["j1", "j2"], "consumed")
