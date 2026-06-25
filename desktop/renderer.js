// Renderer for the two-pane window:
//   - Left: transcript UI (http://127.0.0.1:8010/) with webview-preload for copy
//   - Right: ChatGPT
// One vertical divider resizes the panes.

const LEFT_URL = 'http://127.0.0.1:48011/';
const BACKEND_POLL_MS = 500;

const leftView = document.getElementById('left');
const chatgptView = document.getElementById('chatgpt');
const loadingEl = document.getElementById('loading');
const toastEl = document.getElementById('toast');
const divider1 = document.getElementById('divider1');
const leftPane = document.getElementById('leftPane');
const wrap = document.getElementById('wrap');

const BASE_URL = window.location.href.replace(/\/[^/]*$/, '/');
const PRELOAD_URL = BASE_URL + 'webview-preload.js';
const CHAT_PRELOAD_URL = BASE_URL + 'chat-preload.js';
leftView.setAttribute('preload', PRELOAD_URL);
chatgptView.setAttribute('preload', CHAT_PRELOAD_URL);
console.log('[renderer] preload URL:', PRELOAD_URL);
console.log('[renderer] chat preload URL:', CHAT_PRELOAD_URL);

function loadLeft() {
  console.log('[renderer] loading left webview ->', LEFT_URL);
  try {
    const p = leftView.loadURL(LEFT_URL);
    if (p && typeof p.catch === 'function') p.catch(() => {});
  } catch (e) { console.error('[renderer] loadURL threw', e); }
}

leftView.addEventListener('dom-ready', () => {
  const url = leftView.getURL();
  if (!url || url === 'about:blank' || url === '') loadLeft();
});

chatgptView.addEventListener('dom-ready', () => {
  const url = chatgptView.getURL();
  if (!url || url === 'about:blank' || url === '') {
    try { chatgptView.loadURL('https://chatgpt.com/'); } catch (_) {}
  }
}, { once: true });

function setLoadingMessage(text) {
  loadingEl.innerHTML = '<span class="spinner"></span>' + text;
}

let modelReady = false;
let webviewLoaded = false;

function maybeHideOverlay() {
  if (modelReady && webviewLoaded) loadingEl.classList.add('hidden');
}

leftView.addEventListener('did-finish-load', () => {
  const url = leftView.getURL();
  if (url && url.startsWith('http://127.0.0.1:')) {
    webviewLoaded = true;
    if (!modelReady) setLoadingMessage('Loading speech model… (first run downloads it — this can take a few minutes)');
    maybeHideOverlay();
  }
});

window.app.onBackendStatus((status) => {
  if (status === 'waiting') setLoadingMessage('Starting transcription backend…');
  else if (status === 'timeout') setLoadingMessage('Backend slow to start — still trying…');
});

async function pollHealth() {
  try {
    const res = await fetch('http://127.0.0.1:48011/api/health', { cache: 'no-store' });
    if (res.ok) {
      const data = await res.json();
      if (data.status === 'ready') {
        modelReady = true;
        maybeHideOverlay();
        return;
      }
      if (data.status === 'error') {
        setLoadingMessage('Backend error: ' + (data.error || 'unknown'));
        return;
      }
      if (data.status === 'loading-models' && webviewLoaded) {
        setLoadingMessage('Loading speech model… (first run downloads it — this can take a few minutes)');
      }
    }
  } catch (_) { /* backend not up yet */ }
  setTimeout(pollHealth, 1000);
}
pollHealth();

leftView.addEventListener('did-fail-load', (ev) => {
  if (ev.errorCode === -3) return;
  console.warn('[left] load failed', ev.errorCode, ev.errorDescription, ev.validatedURL);
  setTimeout(loadLeft, BACKEND_POLL_MS);
});

// --- Auto-copy bridge: left webview -> main process -> OS clipboard ---
leftView.addEventListener('ipc-message', async (ev) => {
  if (ev.channel === 'copy' && ev.args && ev.args[0]) {
    const text = String(ev.args[0]);
    const ok = await window.app.copyText(text);
    if (ok) showToast(text.length > 60 ? 'copied ✓' : `copied: "${text.slice(0, 60)}"`);
  }
});

// --- "Send to AI" button: read clipboard + inject into right webview ---
const sendAiBtn = document.getElementById('sendAiBtn');
const SEND_LABEL = 'Send to AI ▸';
let sendBusy = false;

function setSendState(state, label) {
  sendAiBtn.classList.remove('sending', 'ok', 'err');
  if (state) sendAiBtn.classList.add(state);
  sendAiBtn.textContent = label || SEND_LABEL;
}

sendAiBtn.addEventListener('click', async () => {
  if (sendBusy) return;
  const text = await window.app.readText();
  if (!text || !text.length) {
    setSendState('err', 'Clipboard empty');
    setTimeout(() => setSendState(null), 1200);
    return;
  }
  sendBusy = true;
  setSendState('sending', 'Sending…');
  try { chatgptView.send('insert-and-send', text); }
  catch (e) {
    console.warn('[renderer] failed to send to chat webview', e);
    sendBusy = false;
    setSendState('err', 'Failed');
    setTimeout(() => setSendState(null), 1500);
  }
});

chatgptView.addEventListener('ipc-message', (ev) => {
  if (ev.channel !== 'sendToAI:result') return;
  sendBusy = false;
  const res = ev.args && ev.args[0];
  if (res && res.ok) {
    setSendState('ok', 'Sent ✓');
    setTimeout(() => setSendState(null), 1200);
  } else {
    const reason = res && res.reason ? res.reason : 'unknown';
    console.warn('[renderer] sendToAI failed:', reason);
    setSendState('err', 'Could not inject — paste manually');
    setTimeout(() => setSendState(null), 2000);
  }
});

function showToast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toastEl.classList.remove('show'), 900);
}

// --- Draggable divider ---
let dragging = false;
const dragMask = document.getElementById('dragMask');

divider1.addEventListener('mousedown', (e) => {
  dragging = true;
  divider1.classList.add('dragging');
  dragMask.classList.add('active');
  e.preventDefault();
});

function endDrag() {
  if (!dragging) return;
  divider1.classList.remove('dragging');
  dragMask.classList.remove('active');
  dragging = false;
  try { localStorage.setItem('ui.leftPaneW', String(leftPane.getBoundingClientRect().width)); } catch (_) {}
}
window.addEventListener('mouseup', endDrag);
window.addEventListener('blur', endDrag);
dragMask.addEventListener('mouseup', endDrag);

window.addEventListener('mousemove', (e) => {
  if (!dragging) return;
  const DIV = 6;
  const MIN = 150;
  const total = wrap.clientWidth;
  const leftW = Math.max(MIN, Math.min(total - MIN - DIV, e.clientX));
  leftPane.style.flex = `0 0 ${leftW}px`;
});

// --- Restore saved pane width ---
const savedLeftW = parseFloat(localStorage.getItem('ui.leftPaneW'));
if (!Number.isNaN(savedLeftW) && savedLeftW > 100) {
  leftPane.style.flex = `0 0 ${savedLeftW}px`;
}

// --- Backend restart notifications ---
if (window.app && window.app.onBackendRestarted) {
  window.app.onBackendRestarted((info) => {
    if (info && info.ok) {
      showToast(`Backend restarted (${info.attempt}/3)`);
      setTimeout(() => leftView.reload(), 3000);
    } else if (info && info.reason === 'port-in-use') {
      setLoadingMessage(`Port ${info.port} already in use — close the other app and press F5`);
      loadingEl.classList.remove('hidden');
    } else {
      showToast('Backend crashed — press F5 to retry');
    }
  });
}

// --- Keyboard shortcuts ---
window.addEventListener('keydown', (e) => {
  if (e.key === 'F5') {
    leftView.reload(); chatgptView.reload();
    e.preventDefault();
  } else if (e.ctrlKey && e.shiftKey && (e.key === 'L' || e.key === 'l')) {
    leftView.reload(); e.preventDefault();
  } else if (e.ctrlKey && e.shiftKey && (e.key === 'G' || e.key === 'g')) {
    chatgptView.reload(); e.preventDefault();
  }
});

chatgptView.addEventListener('did-fail-load', (ev) => {
  if (ev.errorCode !== -3) console.warn('[chatgpt] load failed', ev);
});

// --- Address bar + nav controls ---
const HOME_URL = 'https://chatgpt.com/';
const AI_SHORTCUTS = [
  { id: 'chatgpt', url: 'https://chatgpt.com/',        hosts: ['chatgpt.com', 'chat.openai.com'] },
  { id: 'gemini',  url: 'https://gemini.google.com/',  hosts: ['gemini.google.com'] },
  { id: 'claude',  url: 'https://claude.ai/new',       hosts: ['claude.ai'] },
];
const urlBar = document.getElementById('urlBar');
const navBack = document.getElementById('navBack');
const navForward = document.getElementById('navForward');
const navReload = document.getElementById('navReload');
const navHome = document.getElementById('navHome');

function normalizeUrl(s) {
  const v = s.trim();
  if (!v) return null;
  if (/^[a-z]+:\/\//i.test(v)) return v;
  if (/^[\w.-]+\.[a-z]{2,}(\/.*)?$/i.test(v)) return 'https://' + v;
  return 'https://www.google.com/search?q=' + encodeURIComponent(v);
}

function syncNav() {
  try {
    navBack.disabled = !chatgptView.canGoBack();
    navForward.disabled = !chatgptView.canGoForward();
  } catch (_) {}
  try {
    const u = chatgptView.getURL();
    if (u && document.activeElement !== urlBar) urlBar.value = u;
    let host = '';
    try { host = new URL(u).hostname; } catch (_) {}
    document.querySelectorAll('.ai-shortcut').forEach((btn) => {
      const id = btn.dataset.ai;
      const match = AI_SHORTCUTS.find((s) => s.id === id);
      btn.classList.toggle('active', !!(match && match.hosts.some((h) => host.endsWith(h))));
    });
  } catch (_) {}
}

document.querySelectorAll('.ai-shortcut').forEach((btn) => {
  btn.addEventListener('click', () => {
    const url = btn.dataset.url;
    if (url) chatgptView.loadURL(url);
  });
});

navBack.addEventListener('click', () => { if (chatgptView.canGoBack()) chatgptView.goBack(); });
navForward.addEventListener('click', () => { if (chatgptView.canGoForward()) chatgptView.goForward(); });
navReload.addEventListener('click', () => chatgptView.reload());
navHome.addEventListener('click', () => chatgptView.loadURL(HOME_URL));

urlBar.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    const u = normalizeUrl(urlBar.value);
    if (u) chatgptView.loadURL(u);
  }
});
urlBar.addEventListener('focus', () => urlBar.select());

chatgptView.addEventListener('did-navigate', syncNav);
chatgptView.addEventListener('did-navigate-in-page', syncNav);
chatgptView.addEventListener('did-finish-load', syncNav);
chatgptView.addEventListener('dom-ready', syncNav);
