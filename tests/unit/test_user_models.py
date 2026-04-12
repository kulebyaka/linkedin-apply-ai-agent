"""Tests for user and authentication Pydantic models."""

from datetime import datetime, timezone

from src.models.user import (
    AuthResponse,
    LoginRequest,
    LoginResponse,
    User,
    UserSearchPreferences,
    UserUpdateRequest,
)


class TestUserSearchPreferences:
    """Test UserSearchPreferences model."""

    def test_defaults(self):
        prefs = UserSearchPreferences()
        assert prefs.keywords == ""
        assert prefs.location == ""
        assert prefs.remote_filter is None
        assert prefs.date_posted is None
        assert prefs.experience_level is None
        assert prefs.job_type is None
        assert prefs.easy_apply_only is False
        assert prefs.max_jobs == 50

    def test_full_preferences(self):
        prefs = UserSearchPreferences(
            keywords="python developer",
            location="Berlin, Germany",
            remote_filter="remote",
            date_posted="week",
            experience_level=["mid-senior", "director"],
            job_type=["full-time", "contract"],
            easy_apply_only=True,
            max_jobs=25,
        )
        assert prefs.keywords == "python developer"
        assert prefs.location == "Berlin, Germany"
        assert prefs.remote_filter == "remote"
        assert prefs.date_posted == "week"
        assert prefs.experience_level == ["mid-senior", "director"]
        assert prefs.job_type == ["full-time", "contract"]
        assert prefs.easy_apply_only is True
        assert prefs.max_jobs == 25

    def test_serialization_roundtrip(self):
        prefs = UserSearchPreferences(
            keywords="data engineer",
            experience_level=["entry", "associate"],
        )
        data = prefs.model_dump()
        restored = UserSearchPreferences(**data)
        assert restored == prefs

    def test_json_roundtrip(self):
        prefs = UserSearchPreferences(
            keywords="backend",
            remote_filter="hybrid",
            job_type=["full-time"],
        )
        json_str = prefs.model_dump_json()
        restored = UserSearchPreferences.model_validate_json(json_str)
        assert restored == prefs


class TestUser:
    """Test User model."""

    def test_minimal_user(self):
        user = User(id="abc-123", email="test@example.com", display_name="Test User")
        assert user.id == "abc-123"
        assert user.email == "test@example.com"
        assert user.display_name == "Test User"
        assert user.master_cv_json is None
        assert user.search_preferences is None
        assert isinstance(user.created_at, datetime)
        assert isinstance(user.updated_at, datetime)

    def test_user_with_cv_and_preferences(self):
        cv = {"contact": {"full_name": "John Doe"}, "experiences": []}
        prefs = UserSearchPreferences(keywords="python", location="Remote")
        user = User(
            id="user-1",
            email="john@example.com",
            display_name="John Doe",
            master_cv_json=cv,
            search_preferences=prefs,
        )
        assert user.master_cv_json == cv
        assert user.search_preferences.keywords == "python"
        assert user.search_preferences.location == "Remote"

    def test_user_timestamps_default_to_utc(self):
        user = User(id="u1", email="a@b.com", display_name="A")
        assert user.created_at.tzinfo is not None
        assert user.updated_at.tzinfo is not None

    def test_user_serialization_roundtrip(self):
        user = User(
            id="u-ser",
            email="ser@test.com",
            display_name="Ser",
            master_cv_json={"skills": ["python"]},
            search_preferences=UserSearchPreferences(keywords="ml"),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        data = user.model_dump()
        restored = User(**data)
        assert restored.id == user.id
        assert restored.email == user.email
        assert restored.master_cv_json == user.master_cv_json
        assert restored.search_preferences.keywords == "ml"


class TestAuthModels:
    """Test authentication request/response models."""

    def test_login_request(self):
        req = LoginRequest(email="user@example.com")
        assert req.email == "user@example.com"

    def test_login_request_normalizes_email(self):
        req = LoginRequest(email="  User@Example.COM  ")
        assert req.email == "user@example.com"

    def test_login_request_rejects_invalid_email(self):
        import pytest

        with pytest.raises(ValueError, match="Invalid email"):
            LoginRequest(email="not-an-email")
        with pytest.raises(ValueError, match="Invalid email"):
            LoginRequest(email="")
        with pytest.raises(ValueError, match="Invalid email"):
            LoginRequest(email="@missing-local.com")

    def test_login_response(self):
        resp = LoginResponse(message="Check your email for a magic link")
        assert resp.message == "Check your email for a magic link"

    def test_auth_response(self):
        user = User(id="u1", email="a@b.com", display_name="A")
        resp = AuthResponse(user=user, message="Logged in")
        assert resp.user.id == "u1"
        assert resp.message == "Logged in"

    def test_auth_response_serialization(self):
        user = User(id="u1", email="a@b.com", display_name="A")
        resp = AuthResponse(user=user, message="OK")
        data = resp.model_dump()
        assert data["user"]["id"] == "u1"
        assert data["message"] == "OK"


class TestUserUpdateRequest:
    """Test UserUpdateRequest model."""

    def test_all_fields_optional(self):
        req = UserUpdateRequest()
        assert req.display_name is None
        assert req.master_cv_json is None
        assert req.search_preferences is None

    def test_partial_update(self):
        req = UserUpdateRequest(display_name="New Name")
        assert req.display_name == "New Name"
        assert req.master_cv_json is None
        assert req.search_preferences is None

    def test_cv_update(self):
        cv = {"contact": {"full_name": "Jane"}, "skills": ["rust"]}
        req = UserUpdateRequest(master_cv_json=cv)
        assert req.master_cv_json == cv

    def test_search_preferences_update(self):
        prefs = UserSearchPreferences(keywords="devops", easy_apply_only=True)
        req = UserUpdateRequest(search_preferences=prefs)
        assert req.search_preferences.keywords == "devops"
        assert req.search_preferences.easy_apply_only is True

    def test_full_update(self):
        req = UserUpdateRequest(
            display_name="Updated",
            master_cv_json={"data": True},
            search_preferences=UserSearchPreferences(keywords="go"),
        )
        assert req.display_name == "Updated"
        assert req.master_cv_json == {"data": True}
        assert req.search_preferences.keywords == "go"
