"""MagicLinkRepository - data access for one-shot magic-link tokens.

Split out of UserRepository so the user table and the magic-link table have
distinct repositories. Shares the same Piccolo SQLite engine; engine setup is
performed by the job/user repositories at startup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class MagicLinkRepository:
    """Storage for magic-link tokens used by the passwordless login flow."""

    async def create_magic_link(
        self, email: str, token: str, expires_at: datetime
    ) -> None:
        """Persist a freshly minted magic link token."""
        from src.services.db.tables import MagicLinkTable

        await MagicLinkTable.insert(
            MagicLinkTable(
                token=token,
                email=email,
                expires_at=expires_at,
                used=False,
            )
        ).run()

    async def peek_magic_link(self, token: str) -> str | None:
        """Check that a token is valid without consuming it.

        Returns the email if the token exists, is unused, and has not
        expired. Does NOT mark the token as used — call claim_magic_link()
        after downstream steps succeed.
        """
        from src.services.db.tables import MagicLinkTable

        row = (
            await MagicLinkTable.select()
            .where(MagicLinkTable.token == token)
            .first()
            .run()
        )

        if not row or row["used"]:
            return None

        expires_at = row["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at < datetime.now(tz=timezone.utc):
            return None

        return row["email"]

    async def verify_magic_link(self, token: str) -> str | None:
        """Peek + claim in one step.

        Kept for callers with no downstream steps that can fail between
        the two. Returns the email on successful claim, None otherwise.
        """
        email = await self.peek_magic_link(token)
        if email is None:
            return None
        if not await self.claim_magic_link(token):
            return None
        return email

    async def claim_magic_link(self, token: str) -> bool:
        """Atomically mark a token as used.

        Uses an UPDATE with the full validity predicate so the claim only
        succeeds if the token is still unused AND unexpired. This prevents
        TOCTOU races where two concurrent verifications both pass
        peek_magic_link() — only one UPDATE can match WHERE used=0.

        Returns True if the caller won the claim, False if it was already
        consumed or has expired.
        """
        from src.services.db.tables import MagicLinkTable

        engine = MagicLinkTable._meta._db
        conn = await engine.get_connection()
        try:
            cursor = await conn.execute(
                "UPDATE magic_link SET used = 1 "
                "WHERE token = ? AND used = 0 AND expires_at > datetime('now')",
                (token,),
            )
            if cursor.rowcount == 0:
                return False
            await conn.commit()
        finally:
            await conn.close()
        return True

    async def cleanup_expired_magic_links(self) -> int:
        """Delete expired tokens. Returns count of deleted rows."""
        from src.services.db.tables import MagicLinkTable

        now = datetime.now(tz=timezone.utc)

        expired = (
            await MagicLinkTable.select(MagicLinkTable.token)
            .where(MagicLinkTable.expires_at < now)
            .run()
        )
        count = len(expired)

        if count > 0:
            await (
                MagicLinkTable.delete()
                .where(MagicLinkTable.expires_at < now)
                .run()
            )

        return count
