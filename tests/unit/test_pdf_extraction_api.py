"""API tests for the PDF CV extraction endpoints.

POST /api/users/me/master-cv/extract
GET  /api/users/me/master-cv/extract/{extraction_id}
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.api.main import app, get_current_user
from src.models.user import ModelChoice, User, UserModelPreferences
from src.services.cv.pdf_extraction import CVExtractionRegistry


def _make_user(provider: str | None = None, model: str | None = None) -> User:
    prefs = None
    if provider:
        prefs = UserModelPreferences(
            cv_generation=ModelChoice(provider=provider, model=model or "x")
        )
    return User(
        id="user-1",
        email="test@example.com",
        display_name="Test",
        model_preferences=prefs,
    )


def _make_mock_settings(primary_provider: str = "anthropic") -> MagicMock:
    s = MagicMock()
    s.repo_type = "memory"
    s.db_path = ":memory:"
    s.seed_jobs_from_file = False
    s.linkedin_search_schedule_enabled = False
    s.app_url = "http://localhost:5173"
    s.jwt_expiry_days = 30
    s.dev_auth_bypass = False
    s.pdf_cv_upload_max_bytes = 10_485_760
    s.pdf_cv_upload_max_pages = 20
    s.primary_llm_provider = primary_provider
    return s


def _make_mock_ctx() -> MagicMock:
    repo = AsyncMock()
    repo.initialize = AsyncMock()
    repo.close = AsyncMock()

    user_repo = AsyncMock()
    user_repo.initialize = AsyncMock()
    user_repo.cleanup_expired_magic_links = AsyncMock(return_value=0)

    ctx = MagicMock()
    ctx.repository = repo
    ctx.user_repository = user_repo
    ctx.auth_service = MagicMock()
    ctx.settings = _make_mock_settings()
    ctx.job_queue = MagicMock()
    ctx.job_queue.size = MagicMock(return_value=0)
    ctx.scheduler = None
    ctx.browser = None
    ctx.cv_extraction_registry = CVExtractionRegistry()

    def _noop_bg_task(coro):
        if asyncio.iscoroutine(coro):
            coro.close()
        return MagicMock()

    ctx.create_background_task = _noop_bg_task
    return ctx


def _stub_shared_module(supports_pdf: bool = True) -> MagicMock:
    """Fake src.agents._shared so endpoint imports don't pull in weasyprint."""
    fake_llm = MagicMock()
    fake_llm.model = "mock-model"
    fake_llm.SUPPORTS_PDF_INPUT = supports_pdf
    mod = MagicMock()
    mod.create_llm_client.return_value = fake_llm
    return mod


@contextmanager
def _patched_client(user: User, ctx: MagicMock, primary_provider: str = "anthropic"):
    mock_settings = _make_mock_settings(primary_provider=primary_provider)
    with (
        patch("src.api.main.create_app_context", return_value=ctx),
        patch("src.api.main.settings", mock_settings),
        patch.dict(sys.modules, {"src.agents._shared": _stub_shared_module()}),
    ):
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_current_user, None)


@contextmanager
def _patch_llm_factory(supports_pdf: bool = True):
    mock_shared = _stub_shared_module(supports_pdf=supports_pdf)
    with patch.dict(sys.modules, {"src.agents._shared": mock_shared}):
        yield mock_shared.create_llm_client.return_value


def _fake_pdf_reader(page_count: int):
    class FakeReader:
        def __init__(self, _stream):
            self.pages = [object()] * page_count

    return FakeReader


# ---------------------------------------------------------------------------
# POST /api/users/me/master-cv/extract
# ---------------------------------------------------------------------------


class TestStartExtraction:
    def test_rejects_non_pdf_content_type(self):
        user = _make_user(provider="anthropic", model="claude-sonnet-4.6")
        ctx = _make_mock_ctx()
        with _patched_client(user, ctx) as client:
            resp = client.post(
                "/api/users/me/master-cv/extract",
                files={"file": ("resume.txt", b"hello", "text/plain")},
            )
        assert resp.status_code == 400
        assert "PDF" in resp.json()["detail"]

    def test_rejects_oversized_pdf(self):
        user = _make_user(provider="anthropic", model="claude-sonnet-4.6")
        ctx = _make_mock_ctx()
        big = b"%PDF-1.4\n" + b"0" * (11 * 1024 * 1024)
        with _patched_client(user, ctx) as client:
            resp = client.post(
                "/api/users/me/master-cv/extract",
                files={"file": ("resume.pdf", big, "application/pdf")},
            )
        assert resp.status_code == 400
        assert "MB" in resp.json()["detail"]

    def test_rejects_unsupported_provider(self):
        user = _make_user(provider="deepseek", model="deepseek-chat")
        ctx = _make_mock_ctx()
        with _patched_client(user, ctx) as client:
            resp = client.post(
                "/api/users/me/master-cv/extract",
                files={"file": ("r.pdf", b"%PDF-1.4 stub", "application/pdf")},
            )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "Anthropic" in detail or "GPT" in detail

    def test_rejects_too_many_pages(self):
        user = _make_user(provider="anthropic", model="claude-sonnet-4.6")
        ctx = _make_mock_ctx()
        with (
            _patched_client(user, ctx) as client,
            patch("pypdf.PdfReader", _fake_pdf_reader(99)),
        ):
            resp = client.post(
                "/api/users/me/master-cv/extract",
                files={"file": ("r.pdf", b"%PDF-1.4 stub", "application/pdf")},
            )
        assert resp.status_code == 400
        assert "page" in resp.json()["detail"].lower()

    def test_happy_path_returns_202_and_id(self):
        user = _make_user(provider="anthropic", model="claude-sonnet-4.6")
        ctx = _make_mock_ctx()
        with (
            _patched_client(user, ctx) as client,
            patch("pypdf.PdfReader", _fake_pdf_reader(2)),
            _patch_llm_factory(supports_pdf=True),
        ):
            resp = client.post(
                "/api/users/me/master-cv/extract",
                files={"file": ("r.pdf", b"%PDF-1.4 stub", "application/pdf")},
            )
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "pending"
        assert body["extraction_id"]

    def test_in_flight_returns_409(self):
        user = _make_user(provider="anthropic", model="claude-sonnet-4.6")
        ctx = _make_mock_ctx()

        async def seed():
            task = await ctx.cv_extraction_registry.create(user.id)
            await ctx.cv_extraction_registry.update(task.id, status="running")

        asyncio.run(seed())

        with (
            _patched_client(user, ctx) as client,
            patch("pypdf.PdfReader", _fake_pdf_reader(2)),
            _patch_llm_factory(supports_pdf=True),
        ):
            resp = client.post(
                "/api/users/me/master-cv/extract",
                files={"file": ("r.pdf", b"%PDF-1.4 stub", "application/pdf")},
            )
        assert resp.status_code == 409

    def test_rejects_corrupt_pdf(self):
        from pypdf.errors import PdfReadError

        user = _make_user(provider="anthropic", model="claude-sonnet-4.6")
        ctx = _make_mock_ctx()

        class BadReader:
            def __init__(self, _stream):
                raise PdfReadError("not a pdf")

        with (
            _patched_client(user, ctx) as client,
            patch("pypdf.PdfReader", BadReader),
        ):
            resp = client.post(
                "/api/users/me/master-cv/extract",
                files={"file": ("r.pdf", b"junk", "application/pdf")},
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/users/me/master-cv/extract/{id}
# ---------------------------------------------------------------------------


class TestGetExtractionStatus:
    def test_returns_status_for_owner(self):
        user = _make_user()
        ctx = _make_mock_ctx()

        async def seed():
            task = await ctx.cv_extraction_registry.create(user.id)
            await ctx.cv_extraction_registry.update(
                task.id,
                status="completed",
                result_json={"summary": "ok"},
                validation_errors=["contact.email: missing"],
            )
            return task.id

        tid = asyncio.run(seed())

        with _patched_client(user, ctx) as client:
            resp = client.get(f"/api/users/me/master-cv/extract/{tid}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["result_json"] == {"summary": "ok"}
        assert body["validation_errors"] == ["contact.email: missing"]

    def test_404_when_unknown(self):
        user = _make_user()
        ctx = _make_mock_ctx()
        with _patched_client(user, ctx) as client:
            resp = client.get("/api/users/me/master-cv/extract/does-not-exist")
        assert resp.status_code == 404

    def test_403_when_not_owner(self):
        owner = _make_user()
        other = User(id="someone-else", email="x@y.com", display_name="X")
        ctx = _make_mock_ctx()

        async def seed():
            task = await ctx.cv_extraction_registry.create(owner.id)
            return task.id

        tid = asyncio.run(seed())

        with _patched_client(other, ctx) as client:
            resp = client.get(f"/api/users/me/master-cv/extract/{tid}")
        assert resp.status_code == 403


class TestRouteRegistration:
    def test_routes_registered(self):
        paths = {route.path for route in app.routes}
        assert "/api/users/me/master-cv/extract" in paths
        assert "/api/users/me/master-cv/extract/{extraction_id}" in paths
