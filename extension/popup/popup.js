/**
 * Popup UI: connection status, Connect (opens /extension-auth), Pause/Resume,
 * and the last apply result. No profile form — the apply profile lives in the
 * app's Settings page, which is the single source of truth.
 */

// The app origin to open for the JWT handoff. Overridable via storage so the
// same build works against localhost and the deployed host.
const DEFAULT_APP_ORIGIN = 'http://localhost:5173';

const dot = document.getElementById('dot');
const statusLine = document.getElementById('statusLine');
const connectBtn = document.getElementById('connectBtn');
const pauseBtn = document.getElementById('pauseBtn');
const lastApply = document.getElementById('lastApply');

let paused = false;

function render(connected, isPaused, message) {
  paused = isPaused;
  dot.className = 'dot ' + (connected ? 'dot--on' : 'dot--off');
  statusLine.textContent = message || (connected ? 'Connected' : 'Disconnected');
  pauseBtn.textContent = isPaused ? 'Resume' : 'Pause';
}

async function appOrigin() {
  const { appOrigin } = await chrome.storage.local.get('appOrigin');
  return appOrigin || DEFAULT_APP_ORIGIN;
}

connectBtn.addEventListener('click', async () => {
  const origin = await appOrigin();
  chrome.tabs.create({ url: origin + '/extension-auth' });
});

pauseBtn.addEventListener('click', () => {
  const action = paused ? 'resume' : 'pause';
  chrome.runtime.sendMessage({ action }, (res) => {
    if (res) render(!paused && res.paused === false ? true : false, res.paused, null);
    refresh();
  });
});

function refresh() {
  chrome.runtime.sendMessage({ action: 'getStatus' }, (res) => {
    if (res) render(res.connected, res.paused, null);
  });
}

// Live updates pushed from the background worker.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === 'status') {
    render(msg.connected, msg.paused, msg.message);
  }
  if (msg && msg.type === 'lastApply') {
    lastApply.textContent = msg.text || 'No applications yet.';
  }
});

// Restore last-apply summary, then poll current status.
chrome.storage.local.get('lastApply', ({ lastApply: last }) => {
  if (last) lastApply.textContent = last;
});
refresh();
