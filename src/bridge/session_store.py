"""In-memory registry of active extension WebSocket sessions.

Maps ``user_id`` → the single active ``WebSocket`` for that user. Only one
session per user is kept; the newest connection wins (a fresh browser tab /
extension reload supersedes the prior socket). All access is guarded by an
``asyncio.Lock`` so concurrent connect/disconnect events stay consistent.

This is intentionally process-local. The bridge is fail-fast: if no session
is registered when an apply fires, the workflow routes the job to
``needs_extension`` rather than queuing server-side. A multi-process
deployment would need a shared registry, but that is out of scope here.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.websockets import WebSocket


class SessionStore:
    """Thread-safe ``user_id`` → active ``WebSocket`` registry."""

    def __init__(self) -> None:
        self._sessions: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def register(self, user_id: str, ws: WebSocket) -> WebSocket | None:
        """Bind ``ws`` as the active session for ``user_id`` (newest wins).

        Returns the previously registered WebSocket for this user if one was
        displaced, else ``None``. The caller is responsible for closing a
        displaced socket.
        """
        async with self._lock:
            previous = self._sessions.get(user_id)
            self._sessions[user_id] = ws
            return previous

    async def unregister(self, user_id: str, ws: WebSocket | None = None) -> bool:
        """Remove the session for ``user_id``.

        If ``ws`` is provided, only unregister when it is still the active
        socket — this avoids a stale disconnect handler evicting a newer
        session that already replaced it.

        Returns ``True`` when this call actually removed a session (i.e. ``ws``
        was the active socket, or no ``ws`` was given and one existed), ``False``
        when it was a no-op because a newer socket had already displaced ``ws``.
        """
        async with self._lock:
            if ws is not None and self._sessions.get(user_id) is not ws:
                return False
            return self._sessions.pop(user_id, None) is not None

    async def is_connected(self, user_id: str) -> bool:
        """Return True if an active session exists for ``user_id``."""
        async with self._lock:
            return user_id in self._sessions

    async def get(self, user_id: str) -> WebSocket | None:
        """Return the active WebSocket for ``user_id`` or ``None``."""
        async with self._lock:
            return self._sessions.get(user_id)
