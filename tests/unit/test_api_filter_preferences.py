"""API tests for filter preferences endpoints.

Tests for:
  GET  /api/users/me/filter-preferences
  PUT  /api/users/me/filter-preferences
  POST /api/users/me/filter-preferences/generate-prompt

Uses FastAPI's TestClient with dependency overrides and patching of both
create_app_context and the module-level settings to avoid real DB / LLM calls.

For the generate-prompt endpoint, src.agents._shared is patched in sys.modules
to prevent importing weasyprint (which requires native libs unavailable in CI).
"""

import asyncio
import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app, get_current_user
from src.models.job_filter import UserFilterPreferences
from src.models.user import User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(filter_prefs: UserFilterPreferences | None = None) -> User:
    return User(
        id="test-user-id",
        email="test@example.com",
        display_name="Test User",
        filter_preferences=filter_prefs,
    )


def _make_mock_settings() -> MagicMock:
    """Return a MagicMock with the minimum attributes used by the lifespan."""
    s = MagicMock()
    s.repo_type = "memory"
    s.db_path = ":memory:"
    s.seed_jobs_from_file = False
    s.linkedin_search_schedule_enabled = False
    s.app_url = "http://localhost:5173"
    s.jwt_expiry_days = 30
    return s


def _make_mock_ctx(test_user: User) -> MagicMock:
    """Return a minimal AppContext mock that survives the lifespan."""
    repo = AsyncMock()
    repo.initialize = AsyncMock()
    repo.close = AsyncMock()

    user_repo = AsyncMock()
    user_repo.initialize = AsyncMock()
    user_repo.cleanup_expired_magic_links = AsyncMock(return_value=0)
    user_repo.update = AsyncMock(return_value=test_user)

    ctx = MagicMock()
    ctx.repository = repo
    ctx.user_repository = user_repo
    ctx.auth_service = MagicMock()
    ctx.settings = _make_mock_settings()
    ctx.job_queue = MagicMock()
    ctx.job_queue.size = MagicMock(return_value=0)
    ctx.scheduler = None
    ctx.browser = None

    # Close any coroutine passed in rather than scheduling it,
    # to avoid "coroutine never awaited" warnings and runaway sleep loops.
    def _noop_bg_task(coro):
        if asyncio.iscoroutine(coro):
            coro.close()
        return MagicMock()

    ctx.create_background_task = _noop_bg_task
    return ctx


@contextmanager
def _patched_client(test_user: User, mock_ctx: MagicMock, raise_server_exceptions: bool = True):
    """Context manager that patches create_app_context + settings and overrides auth."""
    mock_settings = _make_mock_settings()
    with (
        patch("src.api.main.create_app_context", return_value=mock_ctx),
        patch("src.api.main.settings", mock_settings),
    ):
        app.dependency_overrides[get_current_user] = lambda: test_user
        try:
            with TestClient(app, raise_server_exceptions=raise_server_exceptions) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_current_user, None)


@contextmanager
def _unauthenticated_client(mock_ctx: MagicMock):
    """Context manager that patches create_app_context + settings but no auth override."""
    mock_settings = _make_mock_settings()
    with (
        patch("src.api.main.create_app_context", return_value=mock_ctx),
        patch("src.api.main.settings", mock_settings),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_user():
    return _make_user()


@pytest.fixture
def mock_ctx(test_user):
    return _make_mock_ctx(test_user)


@pytest.fixture
def api_client(test_user, mock_ctx):
    """TestClient with mocked lifespan context and auth dependency override."""
    with _patched_client(test_user, mock_ctx) as client:
        yield client, mock_ctx


# ---------------------------------------------------------------------------
# GET /api/users/me/filter-preferences
# ---------------------------------------------------------------------------


class TestGetFilterPreferences:
    def test_returns_defaults_when_no_prefs_set(self, api_client):
        client, _ = api_client
        resp = client.get("/api/users/me/filter-preferences")
        assert resp.status_code == 200
        data = resp.json()
        assert data["reject_threshold"] == 30
        assert data["warning_threshold"] == 70
        assert data["enabled"] is True
        assert data["natural_language_prefs"] == ""
        assert data["custom_prompt"] is None

    def test_returns_existing_prefs(self):
        prefs = UserFilterPreferences(
            natural_language_prefs="No on-site jobs",
            reject_threshold=20,
            warning_threshold=60,
            enabled=False,
        )
        user_with_prefs = _make_user(filter_prefs=prefs)
        ctx = _make_mock_ctx(user_with_prefs)

        with _patched_client(user_with_prefs, ctx) as client:
            resp = client.get("/api/users/me/filter-preferences")

        assert resp.status_code == 200
        data = resp.json()
        assert data["natural_language_prefs"] == "No on-site jobs"
        assert data["reject_threshold"] == 20
        assert data["warning_threshold"] == 60
        assert data["enabled"] is False

    def test_requires_auth(self, mock_ctx):
        with _unauthenticated_client(mock_ctx) as client:
            resp = client.get("/api/users/me/filter-preferences")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PUT /api/users/me/filter-preferences
# ---------------------------------------------------------------------------


class TestUpdateFilterPreferences:
    def test_saves_prefs_and_returns_user(self, api_client):
        client, _ = api_client
        payload = {
            "natural_language_prefs": "No clearance required",
            "custom_prompt": None,
            "reject_threshold": 25,
            "warning_threshold": 65,
            "enabled": True,
        }
        resp = client.put("/api/users/me/filter-preferences", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        # Response is the User model
        assert "email" in data
        assert "id" in data

    def test_calls_repository_update_with_correct_prefs(self, api_client):
        client, ctx = api_client
        payload = {
            "natural_language_prefs": "Remote only please",
            "custom_prompt": "My custom prompt",
            "reject_threshold": 30,
            "warning_threshold": 70,
            "enabled": True,
        }
        client.put("/api/users/me/filter-preferences", json=payload)
        ctx.user_repository.update.assert_called_once()
        call_args = ctx.user_repository.update.call_args
        # First positional arg is user_id
        assert call_args[0][0] == "test-user-id"
        # Second arg contains filter_preferences key
        updates = call_args[0][1]
        assert "filter_preferences" in updates
        saved_prefs = updates["filter_preferences"]
        assert saved_prefs.natural_language_prefs == "Remote only please"
        assert saved_prefs.custom_prompt == "My custom prompt"

    def test_validation_error_when_warning_below_reject(self, api_client):
        client, _ = api_client
        payload = {
            "natural_language_prefs": "",
            "reject_threshold": 80,
            "warning_threshold": 50,  # Below reject_threshold → validation fails
            "enabled": True,
        }
        resp = client.put("/api/users/me/filter-preferences", json=payload)
        assert resp.status_code == 422

    def test_requires_auth(self, mock_ctx):
        with _unauthenticated_client(mock_ctx) as client:
            resp = client.put(
                "/api/users/me/filter-preferences",
                json={"natural_language_prefs": "", "enabled": True},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/users/me/filter-preferences/generate-prompt
# ---------------------------------------------------------------------------


def _mock_shared_module(create_llm_side_effect=None):
    """Build a mock src.agents._shared module without importing weasyprint.

    The real _shared.py imports PDFGenerator which pulls in weasyprint (native
    libs unavailable in CI). We substitute a fake module in sys.modules so the
    lazy import inside the endpoint gets the mock instead.
    """
    mock_shared = MagicMock()
    if create_llm_side_effect is not None:
        mock_shared.create_llm_client.side_effect = create_llm_side_effect
    else:
        mock_shared.create_llm_client.return_value = MagicMock()
    return mock_shared


class TestGenerateFilterPrompt:
    def test_returns_generated_prompt(self, api_client):
        """Happy-path: LLM returns a prompt string."""
        client, _ = api_client
        generated = "Evaluate this job. Reject if clearance required."

        mock_filter_instance = MagicMock()
        mock_filter_instance.generate_prompt_from_preferences.return_value = generated

        with (
            patch.dict(sys.modules, {"src.agents._shared": _mock_shared_module()}),
            patch("src.services.jobs.job_filter.JobFilter", return_value=mock_filter_instance),
        ):
            resp = client.post(
                "/api/users/me/filter-preferences/generate-prompt",
                json={"natural_language_prefs": "I want remote senior roles"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["prompt"] == generated

    def test_passes_natural_language_prefs_to_filter(self, api_client):
        """Ensure the user's natural language input is forwarded to JobFilter."""
        client, _ = api_client
        mock_filter_instance = MagicMock()
        mock_filter_instance.generate_prompt_from_preferences.return_value = "some prompt"

        with (
            patch.dict(sys.modules, {"src.agents._shared": _mock_shared_module()}),
            patch("src.services.jobs.job_filter.JobFilter", return_value=mock_filter_instance),
        ):
            client.post(
                "/api/users/me/filter-preferences/generate-prompt",
                json={"natural_language_prefs": "No visa sponsorship, no clearance"},
            )

        mock_filter_instance.generate_prompt_from_preferences.assert_called_once_with(
            "No visa sponsorship, no clearance"
        )

    def test_503_when_llm_not_configured(self, api_client):
        client, _ = api_client
        mock_shared = _mock_shared_module(
            create_llm_side_effect=ValueError("API key not configured for provider")
        )
        with patch.dict(sys.modules, {"src.agents._shared": mock_shared}):
            resp = client.post(
                "/api/users/me/filter-preferences/generate-prompt",
                json={"natural_language_prefs": "anything"},
            )
        assert resp.status_code == 503

    def test_500_when_job_filter_raises(self, api_client):
        client, _ = api_client
        from src.services.jobs.job_filter import JobFilterError

        mock_filter_instance = MagicMock()
        mock_filter_instance.generate_prompt_from_preferences.side_effect = JobFilterError(
            "LLM call failed"
        )
        with (
            patch.dict(sys.modules, {"src.agents._shared": _mock_shared_module()}),
            patch("src.services.jobs.job_filter.JobFilter", return_value=mock_filter_instance),
        ):
            resp = client.post(
                "/api/users/me/filter-preferences/generate-prompt",
                json={"natural_language_prefs": "anything"},
            )
        assert resp.status_code == 500

    def test_requires_auth(self, mock_ctx):
        with _unauthenticated_client(mock_ctx) as client:
            resp = client.post(
                "/api/users/me/filter-preferences/generate-prompt",
                json={"natural_language_prefs": "anything"},
            )
        assert resp.status_code == 401

    def test_422_when_body_missing_required_field(self, api_client):
        client, _ = api_client
        resp = client.post(
            "/api/users/me/filter-preferences/generate-prompt",
            json={},  # missing natural_language_prefs
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Route registration sanity check
# ---------------------------------------------------------------------------


class TestRouteRegistration:
    def test_filter_preferences_routes_registered(self):
        routes = {route.path for route in app.routes}
        assert "/api/users/me/filter-preferences" in routes
        assert "/api/users/me/filter-preferences/generate-prompt" in routes
