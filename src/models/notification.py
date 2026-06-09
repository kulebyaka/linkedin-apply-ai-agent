"""Pydantic model for persistent, user-scoped notifications.

Mirrors ``NotificationTable``. These are the "persistent" tier of the
two-tier notification system: acknowledgment-worthy events (e.g. a pending
filter-refinement proposal) that must survive navigation and reload until the
user marks them read. Ephemeral confirmations remain client-only toasts.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Notification(BaseModel):
    """A persistent, user-scoped notification."""

    id: str
    user_id: str
    type: str = Field(..., description="Notification kind, e.g. 'filter_refinement'.")
    title: str
    body: str | None = None
    action_url: str | None = Field(
        None, description="Optional in-app link, e.g. '/settings#filter'."
    )
    read: bool = False
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
