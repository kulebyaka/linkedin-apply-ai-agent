"""WebSocket bridge between the FastAPI backend and the Chrome MV3 extension.

The extension acts as a dumb DOM actuator in the user's real logged-in
browser. The backend orchestrates the multi-step Easy Apply form fill
deterministically over a single authenticated WebSocket per user, issuing
correlated RPC calls (``serialize_form`` → ``fill_field`` → ``click_button``
→ …) and awaiting their results.

Public surface:
    - ``SessionStore``: in-memory user_id → WebSocket registry.
    - ``WsRelay``: connection handler + correlated RPC dispatch.
    - ``BridgeTimeout`` / ``BridgeDisconnected``: typed RPC failures.
"""

from .session_store import SessionStore
from .ws_relay import BridgeDisconnected, BridgeError, BridgeTimeout, WsRelay

__all__ = [
    "SessionStore",
    "WsRelay",
    "BridgeError",
    "BridgeTimeout",
    "BridgeDisconnected",
]
