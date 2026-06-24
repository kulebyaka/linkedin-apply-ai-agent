"""Authenticated WebSocket relay with correlated RPC dispatch.

``WsRelay`` owns the server side of the extension bridge protocol:

Handshake (extension → server):
    {"type": "auth", "token": "<app JWT>"}
On success the server replies {"type": "ready"} and registers the session.
A bad/missing token closes the socket with policy-violation code 4401.

RPC (server → extension):
    {"type": "rpc", "id": "<corr-id>", "method": "<name>", "params": {...}}
Reply (extension → server):
    {"type": "result", "id": "<corr-id>", "result": {...}}
    (an "error" key may accompany/replace "result"; the relay passes the whole
    frame back to the caller, which decides how to interpret it.)

The receive loop resolves the pending ``Future`` registered under ``id``.
On disconnect every pending future for that user is failed with
``BridgeDisconnected`` so in-flight RPCs don't hang.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from src.services.auth.auth import AuthService

    from .session_store import SessionStore

logger = logging.getLogger(__name__)

# WebSocket close code for a failed auth handshake (4000-4999 = app-defined).
_WS_CLOSE_POLICY = 4401
# Close code used when a newer session displaces an older one for a user.
_WS_CLOSE_SUPERSEDED = 4000


class BridgeError(Exception):
    """Base class for bridge RPC failures."""


class BridgeTimeout(BridgeError):  # noqa: N818 — name is part of the bridge tool contract
    """Raised when an RPC does not receive a reply within the timeout."""


class BridgeDisconnected(BridgeError):  # noqa: N818 — name is part of the bridge tool contract
    """Raised when no session is connected or the socket drops mid-RPC."""


class WsRelay:
    """Manages extension WebSocket connections and correlated RPC calls."""

    def __init__(self, session_store: SessionStore, auth_service: AuthService) -> None:
        self._sessions = session_store
        self._auth = auth_service
        # Correlation-id → pending reply future.
        self._pending: dict[str, asyncio.Future] = {}
        # user_id → set of its in-flight correlation ids (for disconnect cleanup).
        self._pending_by_user: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()
        self._counter = itertools.count(1)

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    async def handle_connection(self, ws: WebSocket) -> None:
        """Accept a connection, authenticate it, then pump frames until close."""
        await ws.accept()

        user_id = await self._authenticate(ws)
        if user_id is None:
            return  # _authenticate already closed the socket

        await ws.send_json({"type": "ready"})
        displaced = await self._sessions.register(user_id, ws)
        if displaced is not None and displaced is not ws:
            await self._safe_close(displaced, _WS_CLOSE_SUPERSEDED)
        logger.info("Extension session registered for user %s", user_id)

        try:
            while True:
                frame = await ws.receive_json()
                await self._on_frame(frame)
        except Exception:  # noqa: BLE001 — WebSocketDisconnect + transport errors
            logger.debug("Extension session for user %s closed", user_id)
        finally:
            removed = await self._sessions.unregister(user_id, ws)
            # Only fail pending futures when *this* socket was the live session.
            # A socket that was already displaced by a newer one must not abort
            # the in-flight RPCs that now belong to the replacement session
            # (pending futures are keyed per-user, not per-socket).
            if removed:
                await self._fail_user_futures(user_id, BridgeDisconnected("extension disconnected"))

    async def _authenticate(self, ws: WebSocket) -> str | None:
        """Run the first-frame auth handshake; return user_id or None on reject."""
        try:
            first = await ws.receive_json()
        except Exception:  # noqa: BLE001
            await self._safe_close(ws, _WS_CLOSE_POLICY)
            return None

        if not isinstance(first, dict) or first.get("type") != "auth":
            await self._safe_close(ws, _WS_CLOSE_POLICY)
            return None

        token = first.get("token")
        if not token or not isinstance(token, str):
            await self._safe_close(ws, _WS_CLOSE_POLICY)
            return None

        try:
            claims = self._auth.decode_jwt(token)
        except ValueError:
            await self._safe_close(ws, _WS_CLOSE_POLICY)
            return None

        user_id = claims.get("user_id")
        if not user_id:
            await self._safe_close(ws, _WS_CLOSE_POLICY)
            return None
        return str(user_id)

    async def _on_frame(self, frame: Any) -> None:
        """Resolve the pending RPC future matching the frame's correlation id."""
        if not isinstance(frame, dict):
            return
        rid = frame.get("id")
        if rid is None:
            return
        async with self._lock:
            fut = self._pending.get(rid)
        if fut is not None and not fut.done():
            fut.set_result(frame)

    # ------------------------------------------------------------------ #
    # RPC dispatch
    # ------------------------------------------------------------------ #
    async def send_rpc(
        self,
        user_id: str,
        method: str,
        params: dict | None = None,
        timeout: float = 30.0,
    ) -> dict:
        """Send an RPC to ``user_id``'s session and await the reply frame.

        Returns the reply frame dict (containing ``result`` and/or ``error``).
        Raises ``BridgeDisconnected`` if no session exists or the socket drops,
        ``BridgeTimeout`` if no reply arrives within ``timeout`` seconds.
        """
        ws = await self._sessions.get(user_id)
        if ws is None:
            raise BridgeDisconnected(f"no active extension session for user {user_id}")

        rid = f"{user_id}:{next(self._counter)}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        async with self._lock:
            self._pending[rid] = fut
            self._pending_by_user.setdefault(user_id, set()).add(rid)

        try:
            try:
                await ws.send_json(
                    {"type": "rpc", "id": rid, "method": method, "params": params or {}}
                )
            except Exception as exc:  # noqa: BLE001 — transport failure during send
                raise BridgeDisconnected(f"failed to send RPC to user {user_id}: {exc}") from exc

            try:
                return await asyncio.wait_for(fut, timeout=timeout)
            except TimeoutError:
                raise BridgeTimeout(
                    f"RPC '{method}' timed out after {timeout}s for user {user_id}"
                ) from None
        finally:
            await self._discard_pending(user_id, rid)

    async def _discard_pending(self, user_id: str, rid: str) -> None:
        async with self._lock:
            self._pending.pop(rid, None)
            ids = self._pending_by_user.get(user_id)
            if ids is not None:
                ids.discard(rid)
                if not ids:
                    self._pending_by_user.pop(user_id, None)

    async def _fail_user_futures(self, user_id: str, exc: BridgeError) -> None:
        """Fail every pending future belonging to ``user_id``."""
        async with self._lock:
            rids = list(self._pending_by_user.get(user_id, set()))
            futures = [self._pending.get(rid) for rid in rids]
        for fut in futures:
            if fut is not None and not fut.done():
                fut.set_exception(exc)

    @staticmethod
    async def _safe_close(ws: WebSocket, code: int) -> None:
        try:
            await ws.close(code=code)
        except Exception:  # noqa: BLE001 — already closing/closed
            pass
