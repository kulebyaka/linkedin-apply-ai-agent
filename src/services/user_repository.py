"""User Repository - Data Access Layer for user persistence.

Provides CRUD operations for users and magic link token management
using Piccolo ORM with SQLite backend.
"""

import json
import logging
from datetime import datetime, timezone

from src.models.job_filter import UserFilterPreferences
from src.models.user import User, UserSearchPreferences

logger = logging.getLogger(__name__)


class UserRepository:
    """Repository for user accounts and magic link tokens.

    Uses the same Piccolo engine as the job repository (set during
    SQLiteJobRepository.initialize()). When repo_type=memory, call
    initialize() to set up the Piccolo engine for user tables.
    """

    async def initialize(self, db_path: str = "./data/jobs.db") -> None:
        """Ensure Piccolo engine is set on user tables.

        When repo_type=sqlite, SQLiteJobRepository.initialize() already
        does this. When repo_type=memory, this method must be called
        separately so that UserRepository can still persist to SQLite.
        """
        from pathlib import Path

        from .tables import MagicLinkTable, UserTable

        # Skip if engine already configured (e.g., by SQLiteJobRepository)
        if UserTable._meta._db is not None:
            return

        from piccolo.engine.sqlite import SQLiteEngine

        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        engine = SQLiteEngine(path=db_path)
        UserTable._meta._db = engine
        MagicLinkTable._meta._db = engine

        await UserTable.create_table(if_not_exists=True).run()
        await MagicLinkTable.create_table(if_not_exists=True).run()

        # Migrate: add columns that may be missing from older databases
        conn = await engine.get_connection()
        try:
            cursor = await conn.execute("PRAGMA table_info(user)")
            rows = await cursor.fetchall()
            user_columns = {row["name"] for row in rows}
            if "filter_preferences" not in user_columns:
                logger.info("Migrating: adding filter_preferences column to user table")
                await conn.execute(
                    "ALTER TABLE user ADD COLUMN filter_preferences JSON NULL"
                )
            await conn.commit()
        finally:
            await conn.close()

        logger.info("UserRepository initialized with engine at %s", db_path)

    async def create_user(self, email: str, display_name: str = "") -> User:
        """Create a new user account.

        Args:
            email: User's email address.
            display_name: Optional display name (defaults to email local part).

        Returns:
            The created User.
        """
        import uuid

        from .tables import UserTable

        user_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc)

        if not display_name:
            display_name = email.split("@")[0]

        await UserTable.insert(
            UserTable(
                id=user_id,
                email=email,
                display_name=display_name,
                master_cv_json=None,
                search_preferences=None,
                created_at=now,
                updated_at=now,
            )
        ).run()

        logger.info("Created user %s (%s)", user_id, email)
        return User(
            id=user_id,
            email=email,
            display_name=display_name,
            created_at=now,
            updated_at=now,
        )

    async def get_by_id(self, user_id: str) -> User | None:
        """Get a user by ID.

        Returns:
            User if found, None otherwise.
        """
        from .tables import UserTable

        row = await UserTable.select().where(UserTable.id == user_id).first().run()
        if not row:
            return None
        return self._row_to_user(row)

    async def get_by_email(self, email: str) -> User | None:
        """Get a user by email address.

        Returns:
            User if found, None otherwise.
        """
        from .tables import UserTable

        row = await UserTable.select().where(UserTable.email == email).first().run()
        if not row:
            return None
        return self._row_to_user(row)

    async def update(self, user_id: str, updates: dict) -> User:
        """Update a user's profile fields.

        Args:
            user_id: User ID.
            updates: Dict of fields to update. Supports: display_name,
                     master_cv_json, search_preferences.

        Returns:
            The updated User.

        Raises:
            KeyError: If user not found.
        """
        from .tables import UserTable

        existing = await UserTable.select().where(UserTable.id == user_id).first().run()
        if not existing:
            raise KeyError(f"User {user_id} not found")

        db_updates = {"updated_at": datetime.now(tz=timezone.utc)}

        if "display_name" in updates and updates["display_name"] is not None:
            db_updates["display_name"] = updates["display_name"]
        if "master_cv_json" in updates:
            db_updates["master_cv_json"] = updates["master_cv_json"]
        if "search_preferences" in updates:
            prefs = updates["search_preferences"]
            if isinstance(prefs, UserSearchPreferences):
                db_updates["search_preferences"] = prefs.model_dump()
            else:
                db_updates["search_preferences"] = prefs

        if "filter_preferences" in updates:
            fprefs = updates["filter_preferences"]
            if isinstance(fprefs, UserFilterPreferences):
                db_updates["filter_preferences"] = fprefs.model_dump()
            else:
                db_updates["filter_preferences"] = fprefs

        await UserTable.update(db_updates).where(UserTable.id == user_id).run()

        return await self.get_by_id(user_id)

    async def get_all_with_search_prefs(self) -> list[User]:
        """Get all users that have non-null search preferences configured.

        Returns:
            List of Users with search_preferences set.
        """
        from .tables import UserTable

        rows = (
            await UserTable.select()
            .where(UserTable.search_preferences.is_not_null())
            .run()
        )
        users: list[User] = []
        for row in rows:
            try:
                users.append(self._row_to_user(row))
            except Exception:
                logger.warning(
                    "Failed to parse user row (id=%s), skipping",
                    row.get("id"),
                    exc_info=True,
                )
        return users

    # =========================================================================
    # Magic Link Methods
    # =========================================================================

    async def create_magic_link(
        self, email: str, token: str, expires_at: datetime
    ) -> None:
        """Store a magic link token for email verification.

        Args:
            email: Email address the link was sent to.
            token: The magic link token.
            expires_at: When the token expires.
        """
        from .tables import MagicLinkTable

        await MagicLinkTable.insert(
            MagicLinkTable(
                token=token,
                email=email,
                expires_at=expires_at,
                used=False,
            )
        ).run()

    async def verify_magic_link(self, token: str) -> str | None:
        """Verify a magic link token.

        Checks that the token exists, hasn't been used, and hasn't expired.
        If valid, marks it as used and returns the email.

        Uses raw SQL with rowcount check to prevent TOCTOU race: two
        concurrent requests may both SELECT an unused token, but only one
        UPDATE will match WHERE used=0. We check cursor.rowcount to ensure
        only the winning request returns the email.

        Args:
            token: The magic link token to verify.

        Returns:
            The email address if token is valid, None otherwise.
        """
        from .tables import MagicLinkTable

        row = (
            await MagicLinkTable.select()
            .where(MagicLinkTable.token == token)
            .first()
            .run()
        )

        if not row:
            return None

        if row["used"]:
            return None

        expires_at = row["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at < datetime.now(tz=timezone.utc):
            return None

        # Atomic claim: UPDATE only if still unused AND not expired.
        # Including the expiry check in the WHERE clause prevents a race
        # where a token passes the SELECT expiry check but expires before
        # the UPDATE executes.
        engine = MagicLinkTable._meta._db
        conn = await engine.get_connection()
        try:
            cursor = await conn.execute(
                "UPDATE magic_link SET used = 1 "
                "WHERE token = ? AND used = 0 AND expires_at > datetime('now')",
                (token,),
            )
            if cursor.rowcount == 0:
                # Another concurrent request already consumed this token,
                # or the token expired between SELECT and UPDATE.
                return None
            await conn.commit()
        finally:
            await conn.close()

        return row["email"]

    async def cleanup_expired_magic_links(self) -> int:
        """Delete expired magic link tokens.

        Returns:
            Number of tokens deleted.
        """
        from .tables import MagicLinkTable

        now = datetime.now(tz=timezone.utc)

        # Count expired
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

    # =========================================================================
    # Helpers
    # =========================================================================

    def _parse_json_field(self, value) -> dict | None:
        """Parse a JSON field that may be string or dict."""
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return value

    def _row_to_user(self, row: dict) -> User:
        """Convert a database row to a User model."""
        search_prefs_raw = self._parse_json_field(row.get("search_preferences"))
        search_prefs = (
            UserSearchPreferences(**search_prefs_raw)
            if search_prefs_raw
            else None
        )

        filter_prefs_raw = self._parse_json_field(row.get("filter_preferences"))
        filter_prefs = (
            UserFilterPreferences(**filter_prefs_raw)
            if filter_prefs_raw
            else None
        )

        cv_json = self._parse_json_field(row.get("master_cv_json"))

        created_at = row.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        updated_at = row.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
        if updated_at and updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        return User(
            id=row["id"],
            email=row["email"],
            display_name=row.get("display_name", ""),
            master_cv_json=cv_json,
            search_preferences=search_prefs,
            filter_preferences=filter_prefs,
            created_at=created_at or datetime.now(tz=timezone.utc),
            updated_at=updated_at or datetime.now(tz=timezone.utc),
        )
