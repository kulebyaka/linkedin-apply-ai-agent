"""User business-logic service.

Wraps UserRepository to host operations that don't belong in a pure data
access layer — specifically, the last-admin demotion guard, which has to
combine a count query, a role lookup, and the update inside one DB
transaction.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.models.user import User, UserRole

from .user_repository import UserRepository

logger = logging.getLogger(__name__)


class LastAdminError(Exception):
    """Raised when a role change would leave the system with zero admins."""


class UserService:
    """Higher-level user operations layered on top of UserRepository.

    Currently the focus is the role-change flow used by admin endpoints,
    but additional cross-cutting user operations (promotion ceremonies,
    invitation flows, anonymisation) should land here rather than the
    repository.
    """

    def __init__(self, user_repository: UserRepository) -> None:
        self._user_repo = user_repository

    async def set_role(self, user_id: str, role: UserRole) -> User:
        """Set a user's role atomically with a last-admin guard.

        Raises:
            KeyError: If the user is not found.
            LastAdminError: If the change would leave zero admins.
        """
        from src.services.db.tables import UserTable

        async with UserTable._meta.db.transaction():
            existing = (
                await UserTable.select().where(UserTable.id == user_id).first().run()
            )
            if not existing:
                raise KeyError(f"User {user_id} not found")

            current_role = (existing.get("role") or UserRole.TRIAL.value)
            if (
                current_role == UserRole.ADMIN.value
                and role != UserRole.ADMIN
            ):
                admin_rows = (
                    await UserTable.select(UserTable.id)
                    .where(UserTable.role == UserRole.ADMIN.value)
                    .run()
                )
                if len(admin_rows) <= 1:
                    raise LastAdminError("Cannot demote the last remaining admin")

            await UserTable.update(
                {
                    "role": role.value,
                    "updated_at": datetime.now(tz=timezone.utc),
                }
            ).where(UserTable.id == user_id).run()

        updated = await self._user_repo.get_by_id(user_id)
        assert updated is not None  # row existed inside the transaction
        return updated

    async def promote_user(self, user_id: str, role: UserRole) -> User:
        """Convenience wrapper for promotions; identical to set_role."""
        return await self.set_role(user_id, role)

    async def demote_user(self, user_id: str, role: UserRole) -> User:
        """Convenience wrapper for demotions; identical to set_role.

        The last-admin guard inside set_role enforces the business rule.
        """
        return await self.set_role(user_id, role)
