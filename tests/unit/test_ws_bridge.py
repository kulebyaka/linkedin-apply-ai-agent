"""Unit tests for the Easy Apply WebSocket bridge (SessionStore + WsRelay).

Uses a FakeWebSocket that emulates the starlette WebSocket surface the relay
touches (accept / receive_json / send_json / close) without any real network.
"""

from __future__ import annotations

import asyncio

import pytest

from src.bridge import BridgeDisconnected, BridgeTimeout, SessionStore, WsRelay

pytestmark = pytest.mark.asyncio


class FakeWebSocket:
    """Minimal in-memory WebSocket double.

    Inbound frames (client → server) are fed via ``feed()``; the receive loop
    blocks on an internal queue and raises a disconnect sentinel when ``feed``
    has been exhausted and ``disconnect()`` was called. Outbound frames
    (server → client) are recorded in ``sent``.
    """

    class _Disconnect(Exception):  # noqa: N818 — test sentinel, not a public error
        pass

    def __init__(self) -> None:
        self.accepted = False
        self.closed_code: int | None = None
        self.sent: list[dict] = []
        self._inbound: asyncio.Queue = asyncio.Queue()
        self.send_should_fail = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> dict:
        item = await self._inbound.get()
        if isinstance(item, type) and issubclass(item, BaseException):
            raise self._Disconnect()
        if item is self._Disconnect:
            raise self._Disconnect()
        return item

    async def send_json(self, data: dict) -> None:
        if self.send_should_fail:
            raise ConnectionResetError("socket broken")
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code

    # --- test helpers -----------------------------------------------------
    def feed(self, frame: dict) -> None:
        self._inbound.put_nowait(frame)

    def disconnect(self) -> None:
        self._inbound.put_nowait(self._Disconnect)


class FakeAuthService:
    """Decodes a few literal tokens for the relay handshake tests.

    - ``good-token`` → extension-scoped token (the only kind the bridge accepts).
    - ``session-token`` → a normal session JWT (no ``scope``); must be rejected.
    Anything else raises ``ValueError`` like a malformed/invalid JWT.
    """

    EXTENSION_SCOPE = "extension"

    def __init__(self, user_id: str = "user-1") -> None:
        self._user_id = user_id

    def decode_jwt(self, token: str) -> dict:
        if token == "good-token":
            return {
                "user_id": self._user_id,
                "email": "u@example.com",
                "scope": self.EXTENSION_SCOPE,
            }
        if token == "session-token":
            # A valid session JWT, but not scoped for the extension bridge.
            return {"user_id": self._user_id, "email": "u@example.com"}
        raise ValueError("Invalid JWT token")


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------
class TestSessionStore:
    async def test_register_and_get(self):
        store = SessionStore()
        ws = FakeWebSocket()
        prev = await store.register("u1", ws)
        assert prev is None
        assert await store.is_connected("u1") is True
        assert await store.get("u1") is ws

    async def test_newest_wins_returns_displaced(self):
        store = SessionStore()
        ws1, ws2 = FakeWebSocket(), FakeWebSocket()
        await store.register("u1", ws1)
        displaced = await store.register("u1", ws2)
        assert displaced is ws1
        assert await store.get("u1") is ws2

    async def test_unregister(self):
        store = SessionStore()
        ws = FakeWebSocket()
        await store.register("u1", ws)
        await store.unregister("u1")
        assert await store.is_connected("u1") is False
        assert await store.get("u1") is None

    async def test_unregister_only_if_matching_socket(self):
        """A stale disconnect must not evict a newer session."""
        store = SessionStore()
        ws1, ws2 = FakeWebSocket(), FakeWebSocket()
        await store.register("u1", ws1)
        await store.register("u1", ws2)  # ws2 is now active
        await store.unregister("u1", ws1)  # stale handler for ws1
        assert await store.get("u1") is ws2

    async def test_is_connected_false_for_unknown(self):
        store = SessionStore()
        assert await store.is_connected("nope") is False


# ---------------------------------------------------------------------------
# WsRelay — auth handshake
# ---------------------------------------------------------------------------
class TestWsRelayAuth:
    async def test_accepts_valid_token_and_registers(self):
        store = SessionStore()
        relay = WsRelay(store, FakeAuthService())
        ws = FakeWebSocket()
        ws.feed({"type": "auth", "token": "good-token"})
        ws.disconnect()

        await relay.handle_connection(ws)

        assert ws.accepted is True
        assert {"type": "ready"} in ws.sent
        # session is unregistered again after disconnect
        assert await store.is_connected("user-1") is False

    async def test_rejects_bad_token(self):
        store = SessionStore()
        relay = WsRelay(store, FakeAuthService())
        ws = FakeWebSocket()
        ws.feed({"type": "auth", "token": "nope"})

        await relay.handle_connection(ws)

        assert ws.closed_code == 4401
        assert {"type": "ready"} not in ws.sent
        assert await store.is_connected("user-1") is False

    async def test_rejects_missing_auth_frame(self):
        store = SessionStore()
        relay = WsRelay(store, FakeAuthService())
        ws = FakeWebSocket()
        ws.feed({"type": "rpc", "id": "x"})  # wrong first frame

        await relay.handle_connection(ws)

        assert ws.closed_code == 4401

    async def test_rejects_missing_token_field(self):
        store = SessionStore()
        relay = WsRelay(store, FakeAuthService())
        ws = FakeWebSocket()
        ws.feed({"type": "auth"})

        await relay.handle_connection(ws)

        assert ws.closed_code == 4401

    async def test_rejects_unscoped_session_token(self):
        """A normal session JWT (no extension scope) must not open the bridge."""
        store = SessionStore()
        relay = WsRelay(store, FakeAuthService())
        ws = FakeWebSocket()
        ws.feed({"type": "auth", "token": "session-token"})

        await relay.handle_connection(ws)

        assert ws.closed_code == 4401
        assert {"type": "ready"} not in ws.sent
        assert await store.is_connected("user-1") is False

    async def test_displaces_previous_session(self):
        store = SessionStore()
        relay = WsRelay(store, FakeAuthService())
        old = FakeWebSocket()
        await store.register("user-1", old)

        new = FakeWebSocket()
        new.feed({"type": "auth", "token": "good-token"})
        new.disconnect()
        await relay.handle_connection(new)

        # old socket was closed with the superseded code
        assert old.closed_code == 4000


# ---------------------------------------------------------------------------
# WsRelay — RPC dispatch
# ---------------------------------------------------------------------------
class TestWsRelayRpc:
    async def _connect(self, relay: WsRelay, store: SessionStore) -> FakeWebSocket:
        """Start a connection handler in the background, return the live socket."""
        ws = FakeWebSocket()
        ws.feed({"type": "auth", "token": "good-token"})
        task = asyncio.create_task(relay.handle_connection(ws))
        # let the handshake run
        for _ in range(20):
            if await store.is_connected("user-1"):
                break
            await asyncio.sleep(0)
        assert await store.is_connected("user-1")
        ws._handler_task = task  # type: ignore[attr-defined]
        return ws

    async def test_rpc_correlation_and_result(self):
        store = SessionStore()
        relay = WsRelay(store, FakeAuthService())
        ws = await self._connect(relay, store)

        async def responder():
            # wait for the rpc frame to be sent, then reply with matching id
            for _ in range(50):
                rpc = next((f for f in ws.sent if f.get("type") == "rpc"), None)
                if rpc is not None:
                    ws.feed({"type": "result", "id": rpc["id"], "result": {"ok": True}})
                    return
                await asyncio.sleep(0)

        asyncio.create_task(responder())
        frame = await relay.send_rpc("user-1", "serialize_form", {}, timeout=2)
        assert frame["result"] == {"ok": True}

        ws.disconnect()
        await ws._handler_task  # type: ignore[attr-defined]

    async def test_rpc_disconnected_when_no_session(self):
        store = SessionStore()
        relay = WsRelay(store, FakeAuthService())
        with pytest.raises(BridgeDisconnected):
            await relay.send_rpc("ghost", "serialize_form", {}, timeout=1)

    async def test_rpc_timeout(self):
        store = SessionStore()
        relay = WsRelay(store, FakeAuthService())
        ws = await self._connect(relay, store)

        with pytest.raises(BridgeTimeout):
            await relay.send_rpc("user-1", "serialize_form", {}, timeout=0.05)

        ws.disconnect()
        await ws._handler_task  # type: ignore[attr-defined]

    async def test_disconnect_fails_pending_rpc(self):
        store = SessionStore()
        relay = WsRelay(store, FakeAuthService())
        ws = await self._connect(relay, store)

        async def kill_after_send():
            for _ in range(50):
                if any(f.get("type") == "rpc" for f in ws.sent):
                    ws.disconnect()
                    return
                await asyncio.sleep(0)

        asyncio.create_task(kill_after_send())
        with pytest.raises(BridgeDisconnected):
            await relay.send_rpc("user-1", "serialize_form", {}, timeout=2)

        await ws._handler_task  # type: ignore[attr-defined]

    async def test_displacement_fails_in_flight_rpc(self):
        """A newer session displacing the old one must fail the old socket's
        in-flight RPCs with BridgeDisconnected, not leave them hanging until
        BridgeTimeout (which the apply bridge would treat as a hard failure)."""
        store = SessionStore()
        relay = WsRelay(store, FakeAuthService())
        old = await self._connect(relay, store)

        rpc_task = asyncio.create_task(relay.send_rpc("user-1", "serialize_form", {}, timeout=5))
        # Wait until the RPC has been dispatched to the (soon-to-be) old socket.
        for _ in range(50):
            if any(f.get("type") == "rpc" for f in old.sent):
                break
            await asyncio.sleep(0)
        assert any(f.get("type") == "rpc" for f in old.sent)

        # A new session connects and displaces the old one.
        new = FakeWebSocket()
        new.feed({"type": "auth", "token": "good-token"})
        new_task = asyncio.create_task(relay.handle_connection(new))

        with pytest.raises(BridgeDisconnected):
            await rpc_task

        assert old.closed_code == 4000  # superseded

        old.disconnect()
        new.disconnect()
        await old._handler_task  # type: ignore[attr-defined]
        await new_task

    async def test_rpc_send_failure_is_disconnected(self):
        store = SessionStore()
        relay = WsRelay(store, FakeAuthService())
        ws = await self._connect(relay, store)
        ws.send_should_fail = True

        with pytest.raises(BridgeDisconnected):
            await relay.send_rpc("user-1", "serialize_form", {}, timeout=1)

        ws.send_should_fail = False
        ws.disconnect()
        await ws._handler_task  # type: ignore[attr-defined]
