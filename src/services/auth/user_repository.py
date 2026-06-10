"""User Repository - Data Access Layer for user persistence.

CRUD over the user table using Piccolo ORM on a shared SQLite engine.

Magic-link token storage lives in `magic_link_repository.MagicLinkRepository`.
Business operations that combine multiple queries (e.g. last-admin demotion
guard) live in `user_service.UserService`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from src.models.job_filter import UserFilterPreferences
from src.models.user import User, UserModelPreferences, UserRole, UserSearchPreferences

logger = logging.getLogger(__name__)


class UserRepository:
    """Repository for user accounts.

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

        from src.services.db.tables import (
            MagicLinkTable,
            NotificationTable,
            UserTable,
        )

        # Set up engine if not already configured (SQLiteJobRepository may have
        # done this already when repo_type=sqlite).
        if UserTable._meta._db is None:
            from piccolo.engine.sqlite import SQLiteEngine

            db_dir = Path(db_path).parent
            db_dir.mkdir(parents=True, exist_ok=True)

            engine = SQLiteEngine(path=db_path)
            UserTable._meta._db = engine
            MagicLinkTable._meta._db = engine
            NotificationTable._meta._db = engine

            await UserTable.create_table(if_not_exists=True).run()
            await MagicLinkTable.create_table(if_not_exists=True).run()
            await NotificationTable.create_table(if_not_exists=True).run()

        # Apply pending schema migrations. Idempotent via schema_migrations table —
        # safe even when SQLiteJobRepository.initialize already ran them.
        from src.services.db.migrations import apply_migrations

        await apply_migrations(UserTable._meta._db)

        logger.info("UserRepository initialized with engine at %s", db_path)

    async def create_user(self, email: str, display_name: str = "") -> User:
        """Create a new user account.

        Returns the created User.
        """
        import uuid

        from src.services.db.tables import UserTable

        user_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc)

        if not display_name:
            display_name = email.split("@")[0]

        await UserTable.insert(
            UserTable(
                id=user_id,
                email=email,
                display_name=display_name,
                role=UserRole.TRIAL.value,
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
            role=UserRole.TRIAL,
            created_at=now,
            updated_at=now,
        )

    async def get_by_id(self, user_id: str) -> User | None:
        from src.services.db.tables import UserTable

        row = await UserTable.select().where(UserTable.id == user_id).first().run()
        if not row:
            return None
        return self._row_to_user(row)

    async def get_by_email(self, email: str) -> User | None:
        from src.services.db.tables import UserTable

        row = await UserTable.select().where(UserTable.email == email).first().run()
        if not row:
            return None
        return self._row_to_user(row)

    async def update(self, user_id: str, updates: dict) -> User:
        """Update a user's profile fields.

        Supports display_name, master_cv_json, search_preferences,
        filter_preferences, model_preferences.

        Raises KeyError if the user is missing.
        """
        from src.services.db.tables import UserTable

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

        if "model_preferences" in updates:
            mprefs = updates["model_preferences"]
            if isinstance(mprefs, UserModelPreferences):
                db_updates["model_preferences"] = mprefs.model_dump()
            else:
                db_updates["model_preferences"] = mprefs

        await UserTable.update(db_updates).where(UserTable.id == user_id).run()

        return await self.get_by_id(user_id)

    async def set_role(self, user_id: str, role: UserRole) -> User:
        """Set a user's role unconditionally.

        Lacks the last-admin guard — use UserService.set_role() instead from
        admin-facing endpoints.
        """
        from src.services.db.tables import UserTable

        existing = await UserTable.select().where(UserTable.id == user_id).first().run()
        if not existing:
            raise KeyError(f"User {user_id} not found")

        await UserTable.update(
            {
                "role": role.value,
                "updated_at": datetime.now(tz=timezone.utc),
            }
        ).where(UserTable.id == user_id).run()

        return await self.get_by_id(user_id)

    # =========================================================================
    # Auto-Refinement Proposal (single pending per user)
    # =========================================================================

    async def get_pending_proposal(self, user_id: str) -> "RefinementProposal | None":
        """Return the user's single pending refinement proposal, or None."""
        from src.models.job_filter import RefinementProposal
        from src.services.db.tables import UserTable

        row = (
            await UserTable.select(UserTable.pending_refinement)
            .where(UserTable.id == user_id)
            .first()
            .run()
        )
        if not row:
            return None
        raw = self._parse_json_field(row.get("pending_refinement"))
        if not raw:
            return None
        try:
            return RefinementProposal(**raw)
        except Exception:
            logger.warning(
                "Failed to parse pending_refinement for user %s; treating as none",
                user_id,
                exc_info=True,
            )
            return None

    async def set_pending_proposal(
        self, user_id: str, proposal: "RefinementProposal"
    ) -> None:
        """Store (or supersede) the user's single pending refinement proposal."""
        from src.services.db.tables import UserTable

        await UserTable.update(
            {
                "pending_refinement": proposal.model_dump(mode="json"),
                "updated_at": datetime.now(tz=timezone.utc),
            }
        ).where(UserTable.id == user_id).run()

    async def clear_pending_proposal(self, user_id: str) -> None:
        """Clear the user's pending refinement proposal."""
        from src.services.db.tables import UserTable

        await UserTable.update(
            {
                "pending_refinement": None,
                "updated_at": datetime.now(tz=timezone.utc),
            }
        ).where(UserTable.id == user_id).run()

    async def get_all_with_auto_refine(self) -> list[User]:
        """All users whose filter preferences opt into auto-refinement."""
        from src.services.db.tables import UserTable

        rows = (
            await UserTable.select()
            .where(UserTable.filter_preferences.is_not_null())
            .run()
        )
        users: list[User] = []
        for row in rows:
            try:
                user = self._row_to_user(row)
            except Exception:
                logger.warning(
                    "Failed to parse user row (id=%s), skipping",
                    row.get("id"),
                    exc_info=True,
                )
                continue
            if user.filter_preferences and user.filter_preferences.auto_refine_enabled:
                users.append(user)
        return users

    async def count_admins(self) -> int:
        """Return the total number of users with role == admin.

        Used by the last-admin demotion guard in UserService.
        """
        from src.services.db.tables import UserTable

        rows = (
            await UserTable.select(UserTable.id)
            .where(UserTable.role == UserRole.ADMIN.value)
            .run()
        )
        return len(rows)

    async def list_all_users(self, limit: int = 200, offset: int = 0) -> list[User]:
        """List all users ordered by created_at descending."""
        from src.services.db.tables import UserTable

        rows = (
            await UserTable.select()
            .order_by(UserTable.created_at, ascending=False)
            .limit(limit)
            .offset(offset)
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

    async def get_all_with_search_prefs(self) -> list[User]:
        """All users with non-null search preferences."""
        from src.services.db.tables import UserTable

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
    # Helpers
    # =========================================================================

    def _parse_json_field(self, value) -> dict | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return value

    def _row_to_user(self, row: dict) -> User:
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

        model_prefs_raw = self._parse_json_field(row.get("model_preferences"))
        model_prefs = (
            UserModelPreferences(**model_prefs_raw)
            if model_prefs_raw
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

        role_raw = row.get("role") or UserRole.TRIAL.value
        try:
            role = UserRole(role_raw)
        except ValueError:
            logger.warning(
                "Unknown role '%s' for user %s; defaulting to trial",
                role_raw,
                row.get("id"),
            )
            role = UserRole.TRIAL

        return User(
            id=row["id"],
            email=row["email"],
            display_name=row.get("display_name", ""),
            role=role,
            master_cv_json=cv_json,
            search_preferences=search_prefs,
            filter_preferences=filter_prefs,
            model_preferences=model_prefs,
            created_at=created_at or datetime.now(tz=timezone.utc),
            updated_at=updated_at or datetime.now(tz=timezone.utc),
        )
