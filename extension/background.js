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
// Mirrors the content script's `userExplicitlyConnected` gate. The server opens
// it with `begin_session` and closes it with `end_session`. We track it here too
// because the content script is re-injected on demand (and on every navigation),
// which resets its module-level flag — we must re-assert the gate after any fresh
// injection or all mutating primitives would silently block mid-apply.
let sessionActive = false;
// How long to wait for a navigated page to finish loading before driving it.
const NAVIGATE_LOAD_TIMEOUT_MS = 15000;

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
    // The session is server-driven; a dropped socket ends it. The next apply run
    // re-opens the gate with a fresh `begin_session`.
    sessionActive = false;
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
function isLinkedInUrl(url) {
  try {
    const u = new URL(url);
    return (
      u.protocol === 'https:' &&
      (u.hostname === 'linkedin.com' || u.hostname.endsWith('.linkedin.com'))
    );
  } catch (_e) {
    return false;
  }
}

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
  // A fresh injection starts with the mutation gate closed. If the server has an
  // open session, re-assert it so the next mutating RPC isn't silently blocked.
  if (sessionActive) {
    try {
      await chrome.tabs.sendMessage(tabId, { method: 'begin_session', params: {} });
    } catch (_e) {
      /* re-assert is best-effort */
    }
  }
}

// Resolve once the tab finishes loading (or after a timeout fallback) so the
// page exists before we drive it. chrome.tabs.update returns immediately, well
// before the navigated page is ready.
function waitForTabLoad(tabId) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    };
    const listener = (updatedTabId, info) => {
      if (updatedTabId === tabId && info.status === 'complete') finish();
    };
    chrome.tabs.onUpdated.addListener(listener);
    setTimeout(finish, NAVIGATE_LOAD_TIMEOUT_MS);
  });
}

async function routeRpc(method, params) {
  // Track the server-driven session gate (also delivered to the content script
  // via the normal sendMessage path below / re-asserted on injection).
  if (method === 'begin_session') sessionActive = true;
  else if (method === 'end_session') sessionActive = false;

  // Screenshot pixels come from the tab capture API, not the content script.
  if (method === 'capture_visible') {
    const dataUrl = await chrome.tabs.captureVisibleTab(undefined, { format: 'png' });
    return { screenshot_b64: dataUrl };
  }

  const tab = await findLinkedInTab();
  if (!tab) return { error: 'no LinkedIn tab open' };

  // Background may navigate before driving the form. Wait for the load to
  // complete so the Easy Apply button exists when the next RPC fires.
  // Defense in depth: only ever navigate the LinkedIn tab to a LinkedIn URL so
  // a stray/malformed target can't redirect the user's browser elsewhere.
  if (method === 'navigate' && params.url) {
    if (!isLinkedInUrl(params.url)) {
      return { error: 'refusing to navigate to non-LinkedIn URL' };
    }
    const loaded = waitForTabLoad(tab.id);
    await chrome.tabs.update(tab.id, { url: params.url });
    await loaded;
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
      if (msg.appOrigin) await chrome.storage.local.set({ appOrigin: msg.appOrigin });
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
