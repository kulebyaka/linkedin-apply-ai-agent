"""Unit tests for AuthService.

Tests JWT creation/decoding, magic link token generation/verification,
expired token rejection, and auto-create user on first login.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.config.settings import Settings
from src.models.user import User
from src.services.auth import AuthService
from src.services.user_repository import UserRepository


@pytest.fixture
def settings():
    """Test settings with known JWT secret."""
    return Settings(
        _env_file=None,
        jwt_secret="test-secret-key-for-testing-extended",
        jwt_expiry_days=30,
        magic_link_ttl_minutes=15,
        app_url="http://localhost:5173",
        resend_api_key="",  # No actual email sending in tests
    )


@pytest.fixture
def user_repo():
    """Mock UserRepository for unit tests."""
    repo = AsyncMock(spec=UserRepository)
    return repo


@pytest.fixture
def auth_service(settings, user_repo):
    return AuthService(settings, user_repo)


# =============================================================================
# JWT Tests
# =============================================================================


class TestJWT:
    def test_create_jwt_returns_string(self, auth_service):
        token = auth_service.create_jwt("user-123", "test@example.com")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_decode_jwt_roundtrip(self, auth_service):
        token = auth_service.create_jwt("user-456", "alice@example.com")
        claims = auth_service.decode_jwt(token)

        assert claims["user_id"] == "user-456"
        assert claims["email"] == "alice@example.com"
        assert "exp" in claims
        assert "iat" in claims

    def test_decode_jwt_expired_raises(self, auth_service, settings):
        import jwt as pyjwt

        payload = {
            "user_id": "expired-user",
            "email": "expired@example.com",
            "exp": datetime.now(tz=timezone.utc) - timedelta(hours=1),
            "iat": datetime.now(tz=timezone.utc) - timedelta(hours=2),
        }
        token = pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")

        with pytest.raises(ValueError, match="expired"):
            auth_service.decode_jwt(token)

    def test_decode_jwt_invalid_token_raises(self, auth_service):
        with pytest.raises(ValueError, match="Invalid JWT token"):
            auth_service.decode_jwt("not-a-valid-jwt-token")

    def test_decode_jwt_wrong_secret_raises(self, auth_service):
        import jwt as pyjwt

        payload = {
            "user_id": "user-1",
            "email": "a@b.com",
            "exp": datetime.now(tz=timezone.utc) + timedelta(hours=1),
        }
        token = pyjwt.encode(payload, "wrong-secret", algorithm="HS256")

        with pytest.raises(ValueError, match="Invalid JWT token"):
            auth_service.decode_jwt(token)

    def test_jwt_expiry_matches_settings(self, auth_service, settings):
        token = auth_service.create_jwt("u1", "a@b.com")
        claims = auth_service.decode_jwt(token)

        exp = datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
        now = datetime.now(tz=timezone.utc)

        # Should expire roughly settings.jwt_expiry_days from now
        delta = exp - now
        assert delta.days >= settings.jwt_expiry_days - 1
        assert delta.days <= settings.jwt_expiry_days + 1


# =============================================================================
# Magic Link Tests
# =============================================================================


class TestMagicLink:
    @pytest.mark.asyncio
    async def test_send_magic_link_stores_token(self, auth_service, user_repo):
        """send_magic_link should create a token in the repository."""
        await auth_service.send_magic_link("user@example.com")

        user_repo.create_magic_link.assert_called_once()
        call_args = user_repo.create_magic_link.call_args
        assert call_args[0][0] == "user@example.com"  # email
        assert isinstance(call_args[0][1], str)  # token
        assert len(call_args[0][1]) > 20  # reasonable token length
        assert isinstance(call_args[0][2], datetime)  # expires_at

    @pytest.mark.asyncio
    async def test_send_magic_link_no_resend_key_logs_token(self, auth_service, user_repo):
        """Without RESEND_API_KEY, token should still be stored but no email sent."""
        # resend_api_key is "" in test settings, so it should just log
        await auth_service.send_magic_link("nokey@example.com")
        user_repo.create_magic_link.assert_called_once()


# =============================================================================
# Verify Token Tests
# =============================================================================


class TestVerifyToken:
    @pytest.mark.asyncio
    async def test_verify_token_existing_user(self, auth_service, user_repo):
        """verify_token should return existing user without creating new one."""
        existing_user = User(
            id="existing-id", email="existing@example.com", display_name="Existing"
        )
        user_repo.verify_magic_link.return_value = "existing@example.com"
        user_repo.get_by_email.return_value = existing_user

        result = await auth_service.verify_token("valid-token")

        assert result.id == "existing-id"
        assert result.email == "existing@example.com"
        user_repo.create_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_token_auto_creates_user(self, auth_service, user_repo):
        """verify_token should auto-create user on first login."""
        new_user = User(
            id="new-id", email="new@example.com", display_name="new"
        )
        user_repo.verify_magic_link.return_value = "new@example.com"
        user_repo.get_by_email.return_value = None  # No existing user
        user_repo.create_user.return_value = new_user

        result = await auth_service.verify_token("first-login-token")

        assert result.id == "new-id"
        user_repo.create_user.assert_called_once_with("new@example.com")

    @pytest.mark.asyncio
    async def test_verify_token_invalid_raises(self, auth_service, user_repo):
        """verify_token should raise ValueError for invalid tokens."""
        user_repo.verify_magic_link.return_value = None

        with pytest.raises(ValueError, match="Invalid or expired"):
            await auth_service.verify_token("bad-token")

    @pytest.mark.asyncio
    async def test_verify_token_expired_raises(self, auth_service, user_repo):
        """verify_token should raise ValueError for expired tokens."""
        user_repo.verify_magic_link.return_value = None  # Expired = not found

        with pytest.raises(ValueError, match="Invalid or expired"):
            await auth_service.verify_token("expired-token")
