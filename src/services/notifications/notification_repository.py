"""Notification Repository - Data Access Layer for persistent notifications.

CRUD over the notification table using Piccolo ORM on the shared SQLite engine
(the same engine wired up by SQLiteJobRepository / UserRepository). All queries
are user-scoped — notifications never cross users.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from src.models.notification import Notification

logger = logging.getLogger(__name__)


class NotificationRepository:
    """Repository for persistent, user-scoped notifications."""

    async def create(
        self,
        user_id: str,
        *,
        type: str,
        title: str,
        body: str | None = None,
        action_url: str | None = None,
    ) -> Notification:
        """Persist a new notification and return it."""
        from src.services.db.tables import NotificationTable

        notif = Notification(
            id=str(uuid.uuid4()),
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            action_url=action_url,
            read=False,
            created_at=datetime.now(tz=timezone.utc),
        )
        await NotificationTable.insert(
            NotificationTable(
                id=notif.id,
                user_id=notif.user_id,
                type=notif.type,
                title=notif.title,
                body=notif.body,
                action_url=notif.action_url,
                read=notif.read,
                created_at=notif.created_at,
            )
        ).run()
        logger.info("Created notification %s (%s) for user %s", notif.id, type, user_id)
        return notif

    async def list_for_user(
        self,
        user_id: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Notification]:
        """List a user's notifications, newest first."""
        from src.services.db.tables import NotificationTable

        query = NotificationTable.select().where(NotificationTable.user_id == user_id)
        if unread_only:
            query = query.where(NotificationTable.read == False)  # noqa: E712
        rows = (
            await query.order_by(NotificationTable.created_at, ascending=False)
            .limit(limit)
            .offset(offset)
            .run()
        )
        return [self._row_to_model(row) for row in rows]

    async def unread_count(self, user_id: str) -> int:
        """Return the number of unread notifications for the bell badge."""
        from src.services.db.tables import NotificationTable

        rows = (
            await NotificationTable.select(NotificationTable.id)
            .where(NotificationTable.user_id == user_id)
            .where(NotificationTable.read == False)  # noqa: E712
            .run()
        )
        return len(rows)

    async def mark_read(self, notification_id: str, user_id: str) -> bool:
        """Mark a single notification read. Returns False if not found/owned."""
        from src.services.db.tables import NotificationTable

        existing = (
            await NotificationTable.select(NotificationTable.id)
            .where(NotificationTable.id == notification_id)
            .where(NotificationTable.user_id == user_id)
            .first()
            .run()
        )
        if not existing:
            return False
        await (
            NotificationTable.update({NotificationTable.read: True})
            .where(NotificationTable.id == notification_id)
            .where(NotificationTable.user_id == user_id)
            .run()
        )
        return True

    async def mark_all_read(self, user_id: str) -> int:
        """Mark all of a user's notifications read. Returns the count updated."""
        from src.services.db.tables import NotificationTable

        unread = (
            await NotificationTable.select(NotificationTable.id)
            .where(NotificationTable.user_id == user_id)
            .where(NotificationTable.read == False)  # noqa: E712
            .run()
        )
        if not unread:
            return 0
        await (
            NotificationTable.update({NotificationTable.read: True})
            .where(NotificationTable.user_id == user_id)
            .where(NotificationTable.read == False)  # noqa: E712
            .run()
        )
        return len(unread)

    async def mark_read_by_type(self, user_id: str, type: str) -> int:
        """Mark all of a user's unread notifications of a given type read.

        Used when the user acts on the underlying event (e.g. accepts/rejects a
        filter-refinement proposal) so its notification clears. Returns count.
        """
        from src.services.db.tables import NotificationTable

        unread = (
            await NotificationTable.select(NotificationTable.id)
            .where(NotificationTable.user_id == user_id)
            .where(NotificationTable.type == type)
            .where(NotificationTable.read == False)  # noqa: E712
            .run()
        )
        if not unread:
            return 0
        await (
            NotificationTable.update({NotificationTable.read: True})
            .where(NotificationTable.user_id == user_id)
            .where(NotificationTable.type == type)
            .where(NotificationTable.read == False)  # noqa: E712
            .run()
        )
        return len(unread)

    # =========================================================================
    # Helpers
    # =========================================================================

    @staticmethod
    def _row_to_model(row: dict) -> Notification:
        created_at = row.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return Notification(
            id=row["id"],
            user_id=row["user_id"],
            type=row["type"],
            title=row["title"],
            body=row.get("body"),
            action_url=row.get("action_url"),
            read=bool(row.get("read")),
            created_at=created_at or datetime.now(tz=timezone.utc),
        )
