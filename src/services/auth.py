"""Authentication Service - Magic link email auth with JWT sessions.

Handles:
- Magic link generation and email sending via Resend
- Magic link verification with auto-create on first login
- JWT token creation and validation for session management
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from src.config.settings import Settings
from src.models.user import User
from src.services.user_repository import UserRepository

logger = logging.getLogger(__name__)


class AuthService:
    """Manages passwordless authentication via magic link emails.

    Flow:
    1. User enters email -> send_magic_link() sends email with token
    2. User clicks link -> verify_token() validates, creates user if new
    3. JWT cookie set -> subsequent requests authenticated via decode_jwt()
    """

    def __init__(self, settings: Settings, user_repository: UserRepository) -> None:
        self._settings = settings
        self._user_repo = user_repository
        if settings.jwt_secret == "change-me-in-production":
            logger.warning(
                "JWT_SECRET is using the default value. "
                "Set JWT_SECRET in .env for production use."
            )

    async def send_magic_link(self, email: str) -> None:
        """Generate a magic link token and send it via email.

        Args:
            email: The email address to send the magic link to.

        Raises:
            RuntimeError: If email sending fails.
        """
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(tz=timezone.utc) + timedelta(
            minutes=self._settings.magic_link_ttl_minutes
        )

        # Store token in database
        await self._user_repo.create_magic_link(email, token, expires_at)

        # Build magic link URL
        verify_url = f"{self._settings.app_url}/auth/verify?token={token}"

        # Send email via Resend
        if not self._settings.resend_api_key:
            logger.warning("RESEND_API_KEY not set — magic link not emailed for %s.", email)
            logger.debug("Dev-mode magic link token for %s: %s", email, token)
            return

        try:
            import asyncio

            import resend

            resend.api_key = self._settings.resend_api_key

            params = {
                "from": "LinkedIn Agent <noreply@resend.dev>",
                "to": [email],
                "subject": "Your magic link login",
                "html": (
                    f"<p>Click the link below to sign in:</p>"
                    f'<p><a href="{verify_url}">{verify_url}</a></p>'
                    f"<p>This link expires in {self._settings.magic_link_ttl_minutes} minutes.</p>"
                ),
            }
            await asyncio.to_thread(resend.Emails.send, params)
            logger.info("Magic link email sent to %s", email)
        except Exception as e:
            logger.error("Failed to send magic link email to %s: %s", email, e)
            raise RuntimeError(f"Failed to send magic link email: {e}") from e

    async def verify_token(self, token: str) -> User:
        """Verify a magic link token and return the authenticated user.

        Auto-creates user on first login (open registration).

        Args:
            token: The magic link token from the email.

        Returns:
            The authenticated User.

        Raises:
            ValueError: If token is invalid or expired.
        """
        email = await self._user_repo.verify_magic_link(token)
        if email is None:
            raise ValueError("Invalid or expired magic link token")

        # Get or create user
        user = await self._user_repo.get_by_email(email)
        if user is None:
            user = await self._user_repo.create_user(email)
            logger.info("Auto-created user for %s on first login", email)

        return user

    def create_jwt(self, user_id: str, email: str) -> str:
        """Create a JWT token for session management.

        Args:
            user_id: The user's UUID.
            email: The user's email.

        Returns:
            Encoded JWT string.
        """
        payload = {
            "user_id": user_id,
            "email": email,
            "exp": datetime.now(tz=timezone.utc)
            + timedelta(days=self._settings.jwt_expiry_days),
            "iat": datetime.now(tz=timezone.utc),
        }
        return jwt.encode(payload, self._settings.jwt_secret, algorithm="HS256")

    def decode_jwt(self, token: str) -> dict:
        """Decode and validate a JWT token.

        Args:
            token: The JWT token string.

        Returns:
            Dict with user_id, email, and other claims.

        Raises:
            ValueError: If token is invalid or expired.
        """
        try:
            return jwt.decode(
                token, self._settings.jwt_secret, algorithms=["HS256"]
            )
        except jwt.ExpiredSignatureError:
            raise ValueError("JWT token has expired") from None
        except jwt.InvalidTokenError as e:
            raise ValueError(f"Invalid JWT token: {e}") from None
