/**
 * LinkedIn Apply AI Agent — background service worker.
 *
 * Owns the WebSocket bridge to the server and routes each server RPC to the
 * active LinkedIn tab's content script. It is the only component that talks to
 * the network; it holds NO application logic beyond transport + routing.
 *
 * Flow (see docs/plans/ARCHITECTURE-browser-agent.md §8):
 *   1. /extension-auth posts the app JWT via onMessageExternal -> stored in
 *      chrome.storage.session (cleared on browser close).
 *   2. We open `wss://<app>/ws/extension` and send {"type":"auth","token":...}.
 *   3. Server replies {"type":"ready"}, then drives the form with
 *      {"type":"rpc","id","method","params"} frames.
 *   4. We inject content_script.js on demand, forward the method, and reply
 *      {"type":"result","id","result"|"error"}.
 */

// Default endpoints. The app origin (for the WS) is overridable from storage so
// the same build works against localhost and the deployed host.
const DEFAULTS = {
  wsUrl: 'ws://localhost:8000/ws/extension',
};

let socket = null;
let connected = false;
let reconnectDelay = 1000; // backoff, capped below
const MAX_RECONNECT_DELAY = 30000;
let paused = false;

// ---- Token storage (session-scoped) ---------------------------------------
async function getToken() {
  const { authToken } = await chrome.storage.session.get('authToken');
  return authToken || null;
}
async function setToken(token) {
  await chrome.storage.session.set({ authToken: token });
}
async function getWsUrl() {
  const { wsUrl } = await chrome.storage.local.get('wsUrl');
  return wsUrl || DEFAULTS.wsUrl;
}

// ---- Popup status relay -----------------------------------------------------
function relayStatus(extra) {
  const status = Object.assign({ type: 'status', connected, paused }, extra || {});
  chrome.runtime.sendMessage(status).catch(() => {});
}

// ---- WebSocket lifecycle ----------------------------------------------------
async function connect() {
  const token = await getToken();
  if (!token) {
    relayStatus({ message: 'Not authenticated — open Connect.' });
    return;
  }
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const url = await getWsUrl();
  try {
    socket = new WebSocket(url);
  } catch (e) {
    scheduleReconnect();
    return;
  }

  socket.addEventListener('open', () => {
    socket.send(JSON.stringify({ type: 'auth', token }));
  });

  socket.addEventListener('message', (event) => {
    let frame;
    try {
      frame = JSON.parse(event.data);
    } catch (_e) {
      return;
    }
    handleFrame(frame);
  });

  socket.addEventListener('close', () => {
    connected = false;
    relayStatus({ message: 'Disconnected' });
    scheduleReconnect();
  });

  socket.addEventListener('error', () => {
    try {
      socket.close();
    } catch (_e) {
      /* ignore */
    }
  });
}

function scheduleReconnect() {
  setTimeout(() => {
    connect();
  }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
}

async function handleFrame(frame) {
  if (frame.type === 'ready') {
    connected = true;
    reconnectDelay = 1000; // reset backoff
    relayStatus({ message: 'Connected' });
    return;
  }
  if (frame.type === 'rpc') {
    if (paused) {
      reply(frame.id, null, 'extension paused by user');
      return;
    }
    try {
      const result = await routeRpc(frame.method, frame.params || {});
      if (result && result.error) {
        reply(frame.id, null, result.error);
      } else {
        reply(frame.id, result, null);
      }
    } catch (e) {
      reply(frame.id, null, (e && e.message) || String(e));
    }
  }
}

function reply(id, result, error) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const frame = { type: 'result', id };
  if (error) frame.error = error;
  else frame.result = result;
  socket.send(JSON.stringify(frame));
}

// ---- RPC routing to the active LinkedIn tab --------------------------------
async function findLinkedInTab() {
  const tabs = await chrome.tabs.query({ url: 'https://www.linkedin.com/*' });
  // Prefer the active+focused tab if it's LinkedIn; else the first match.
  const active = tabs.find((t) => t.active);
  return active || tabs[0] || null;
}

async function ensureContentScript(tabId) {
  // Probe; if the actuator isn't there, inject it on demand.
  try {
    const res = await chrome.tabs.sendMessage(tabId, { method: 'take_screenshot' });
    if (res) return;
  } catch (_e) {
    /* not injected yet */
  }
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ['content_script.js'],
  });
}

async function routeRpc(method, params) {
  // Screenshot pixels come from the tab capture API, not the content script.
  if (method === 'capture_visible') {
    const dataUrl = await chrome.tabs.captureVisibleTab(undefined, { format: 'png' });
    return { screenshot_b64: dataUrl };
  }

  const tab = await findLinkedInTab();
  if (!tab) return { error: 'no LinkedIn tab open' };

  // Background may navigate before driving the form.
  if (method === 'navigate' && params.url) {
    await chrome.tabs.update(tab.id, { url: params.url });
    return { navigated: true };
  }

  await ensureContentScript(tab.id);
  return chrome.tabs.sendMessage(tab.id, { method, params });
}

// ---- External message: token handoff from /extension-auth ------------------
chrome.runtime.onMessageExternal.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (msg && msg.type === 'SET_TOKEN' && msg.token) {
      await setToken(msg.token);
      if (msg.wsUrl) await chrome.storage.local.set({ wsUrl: msg.wsUrl });
      reconnectDelay = 1000;
      connect();
      sendResponse({ ok: true });
      return;
    }
    if (msg && msg.type === 'PING') {
      sendResponse({ ok: true, connected });
      return;
    }
    sendResponse({ ok: false, error: 'unknown external message' });
  })();
  return true;
});

// ---- Internal messages: popup controls -------------------------------------
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (!msg || !msg.action) {
      sendResponse({ ok: false });
      return;
    }
    if (msg.action === 'getStatus') {
      sendResponse({ connected, paused });
    } else if (msg.action === 'pause') {
      paused = true;
      relayStatus({ message: 'Paused' });
      sendResponse({ ok: true, paused });
    } else if (msg.action === 'resume') {
      paused = false;
      relayStatus({ message: connected ? 'Connected' : 'Reconnecting' });
      if (!connected) connect();
      sendResponse({ ok: true, paused });
    } else if (msg.action === 'reconnect') {
      reconnectDelay = 1000;
      connect();
      sendResponse({ ok: true });
    } else {
      sendResponse({ ok: false, error: 'unknown action' });
    }
  })();
  return true;
});

// Try to connect on worker startup (token may already be in session storage).
chrome.runtime.onStartup.addListener(() => connect());
connect();
