// Electron main process for BondScribe.
// Spawns the Python/uvicorn backend on port 48011 and opens the window:
// live transcript pane + Claude / ChatGPT / Gemini panes.

const { app, BrowserWindow, ipcMain, clipboard, session, screen, nativeImage } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');
const net = require('net');
const fs = require('fs');

const ROOT = app.isPackaged
  ? path.resolve(__dirname, '..', '..', '..', '..')
  : path.resolve(__dirname, '..');
const BACKEND_PORT = 48011;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
const READY_PATH = '/api/health';

// Windows binds the taskbar icon to the AppUserModelID. Without an explicit one
// the running app gets a generic/blank (white box) taskbar icon, so set it
// before any window is created. The icon itself is loaded from a bundled PNG
// (see createWindow) — Electron renders blank from PNG-compressed .ico entries.
const APP_ID = 'com.bondscribe.desktop';
if (process.platform === 'win32') app.setAppUserModelId(APP_ID);
const APP_ICON = nativeImage.createFromPath(path.join(__dirname, 'icon.png'));

// --------------------------------------------------------------------------
// Logging — write to a file so we can debug when there's no console
// --------------------------------------------------------------------------
const LOG_FILE = path.join(ROOT, 'bondscribe.log');
function log(...args) {
  const line = `[${new Date().toISOString()}] ${args.join(' ')}\n`;
  try { fs.appendFileSync(LOG_FILE, line); } catch (_) {}
  try { process.stdout.write(line); } catch (_) {}
}
function logErr(...args) {
  const line = `[${new Date().toISOString()}] ERROR: ${args.join(' ')}\n`;
  try { fs.appendFileSync(LOG_FILE, line); } catch (_) {}
  try { process.stderr.write(line); } catch (_) {}
}

let mainWindow = null;
let pyProcess = null;
let isShuttingDown = false;
let restartTimestamps = [];
let restartGivenUp = false;
const RESTART_WINDOW_MS = 60_000;
const RESTART_MAX = 3;

function resolvePython() {
  const venvPy = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
  if (fs.existsSync(venvPy)) return venvPy;
  return 'python';
}

function checkPortFree(port) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once('error', () => resolve(false));
    srv.once('listening', () => srv.close(() => resolve(true)));
    srv.listen(port, '127.0.0.1');
  });
}

async function startBackend() {
  const free = await checkPortFree(BACKEND_PORT);
  if (!free) {
    logErr(`port ${BACKEND_PORT} is already in use — not spawning backend`);
    restartGivenUp = true;
    if (mainWindow) {
      mainWindow.webContents.send('backend:restarted', { ok: false, reason: 'port-in-use', port: BACKEND_PORT });
    }
    return;
  }
  const py = resolvePython();
  log(`ROOT resolved to: ${ROOT}`);
  log(`spawning backend: ${py} -m uvicorn backend.server:app`);
  pyProcess = spawn(
    py,
    ['-m', 'uvicorn', 'backend.server:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
    { cwd: ROOT, windowsHide: true }
  );
  pyProcess.stdout.on('data', (d) => log(`[py] ${d}`));
  pyProcess.stderr.on('data', (d) => log(`[py] ${d}`));
  pyProcess.on('error', (err) => logErr(`failed to spawn backend: ${err.message}`));
  pyProcess.on('exit', (code) => {
    log(`backend exited with code ${code}`);
    pyProcess = null;
    if (isShuttingDown || restartGivenUp) return;

    const now = Date.now();
    restartTimestamps = restartTimestamps.filter((t) => now - t < RESTART_WINDOW_MS);
    restartTimestamps.push(now);

    if (restartTimestamps.length > RESTART_MAX) {
      restartGivenUp = true;
      logErr('backend crashed too many times — giving up');
      if (mainWindow) {
        mainWindow.webContents.send('backend:restarted', { ok: false, reason: 'thrash' });
      }
      return;
    }

    log(`restarting backend (attempt ${restartTimestamps.length}/${RESTART_MAX})`);
    setTimeout(() => {
      if (isShuttingDown) return;
      startBackend();
      if (mainWindow) {
        mainWindow.webContents.send('backend:restarted', { ok: true, attempt: restartTimestamps.length });
      }
    }, 500);
  });
}

function pingBackend() {
  return new Promise((resolve) => {
    const req = http.get(`${BACKEND_URL}${READY_PATH}`, (res) => {
      resolve(res.statusCode >= 200 && res.statusCode < 500);
      res.resume();
    });
    req.on('error', () => resolve(false));
    req.setTimeout(800, () => { req.destroy(); resolve(false); });
  });
}

async function waitForBackend(timeoutMs = 300_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await pingBackend()) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function killBackend() {
  if (!pyProcess) return;
  const pid = pyProcess.pid;
  log(`killing backend pid=${pid}`);
  if (process.platform === 'win32') {
    spawn('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true });
  } else {
    try { pyProcess.kill('SIGTERM'); } catch (_) {}
  }
  pyProcess = null;
}

const WINDOW_STATE_FILE = app.isPackaged
  ? path.join(app.getPath('userData'), '.window-state.json')
  : path.join(__dirname, '.window-state.json');

function loadWindowState() {
  try {
    const raw = fs.readFileSync(WINDOW_STATE_FILE, 'utf8');
    const s = JSON.parse(raw);
    if (s && Number.isFinite(s.width) && Number.isFinite(s.height)) return s;
  } catch (_) {}
  return { width: 1600, height: 1000 };
}

function saveWindowState() {
  if (!mainWindow) return;
  try {
    const b = mainWindow.getBounds();
    const state = { ...b, maximized: mainWindow.isMaximized() };
    fs.writeFileSync(WINDOW_STATE_FILE, JSON.stringify(state), 'utf8');
  } catch (_) {}
}

// Returns true if a meaningful chunk of the saved rectangle still falls on a
// currently-connected display. Guards against restoring the window onto a
// monitor that has since been unplugged (it would open off-screen).
function isVisibleOnSomeDisplay(b) {
  if (!Number.isFinite(b.x) || !Number.isFinite(b.y)) return false;
  return screen.getAllDisplays().some((d) => {
    const a = d.workArea;
    const ix = Math.max(0, Math.min(b.x + b.width, a.x + a.width) - Math.max(b.x, a.x));
    const iy = Math.max(0, Math.min(b.y + b.height, a.y + a.height) - Math.max(b.y, a.y));
    // require at least a 200x100 visible patch so the title bar is reachable
    return ix >= 200 && iy >= 100;
  });
}

// Center the given size on the display under the mouse cursor (falls back to
// primary), clamped to that display's work area.
function centeredBounds(width, height) {
  const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
  const w = Math.min(width, area.width);
  const h = Math.min(height, area.height);
  return {
    width: w,
    height: h,
    x: Math.round(area.x + (area.width - w) / 2),
    y: Math.round(area.y + (area.height - h) / 2),
  };
}

function createWindow() {
  const saved = loadWindowState();
  // If the saved position is off-screen (e.g. a monitor was disconnected),
  // recenter on a live display instead of opening into the void.
  const bounds = isVisibleOnSomeDisplay(saved)
    ? saved
    : { ...saved, ...centeredBounds(saved.width, saved.height) };
  mainWindow = new BrowserWindow({
    width: bounds.width,
    height: bounds.height,
    x: bounds.x,
    y: bounds.y,
    backgroundColor: '#0e1116',
    title: 'BondScribe',
    icon: APP_ICON,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true,
    },
  });
  if (!APP_ICON.isEmpty()) mainWindow.setIcon(APP_ICON); // ensure taskbar icon
  if (saved.maximized) mainWindow.maximize();
  ['resize', 'move', 'maximize', 'unmaximize', 'close'].forEach((ev) =>
    mainWindow.on(ev, saveWindowState)
  );

  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadFile(path.join(__dirname, 'renderer.html'));
  if (process.env.BONDSCRIBE_DEBUG === '1') {
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  }

  mainWindow.webContents.on('did-finish-load', async () => {
    mainWindow.webContents.send('backend:status', 'waiting');
    const ok = await waitForBackend();
    mainWindow.webContents.send('backend:status', ok ? 'ready' : 'timeout');
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

app.on('web-contents-created', (_e, contents) => {
  // The transcript webview repeatedly fails to load until the backend's STT
  // model finishes loading. Each failure prints a noisy stack trace from the
  // GUEST_VIEW_MANAGER_CALL handler. Swallow ECONNREFUSED for the localhost
  // backend URL — the renderer's did-fail-load already retries.
  contents.on('did-fail-load', (event, errorCode, _desc, validatedURL) => {
    if (errorCode === -102 && validatedURL && validatedURL.startsWith(BACKEND_URL)) {
      event.preventDefault();
    }
  });
});

app.on('ready', async () => {
  // Clear cached HTML so the transcript pane always loads the latest frontend
  const leftPartition = session.fromPartition('persist:general-left');
  await leftPartition.clearCache();

  session.defaultSession.setPermissionRequestHandler((webContents, permission, cb) => {
    cb(true);
  });

  startBackend();
  createWindow();
});

ipcMain.handle('clipboard:write', (_evt, text) => {
  if (typeof text === 'string' && text.length) {
    clipboard.writeText(text);
    return true;
  }
  return false;
});

ipcMain.handle('clipboard:read', () => clipboard.readText());

function fetchTranscript(timeoutMs = 2000) {
  return new Promise((resolve) => {
    const req = http.get(`${BACKEND_URL}/api/transcript`, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { data += c; });
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (_) { resolve(null); }
      });
    });
    req.on('error', () => resolve(null));
    req.setTimeout(timeoutMs, () => { req.destroy(); resolve(null); });
  });
}

function pad2(n) { return String(n).padStart(2, '0'); }

function formatStamp(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}_${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`;
}

async function saveTranscript() {
  const segs = await fetchTranscript();
  if (!segs || !Array.isArray(segs) || segs.length === 0) {
    log('no transcript to save');
    return;
  }
  const dir = path.join(ROOT, 'sessions');
  try { fs.mkdirSync(dir, { recursive: true }); } catch (_) {}
  const now = new Date();
  const stamp = formatStamp(now);
  const header = `# BondScribe Session — ${now.toISOString()}\n\n`;
  const md = header + segs.map((s) => s.text).join(' ') + '\n';
  try {
    fs.writeFileSync(path.join(dir, `${stamp}.md`), md, 'utf8');
    fs.writeFileSync(path.join(dir, `${stamp}.json`), JSON.stringify(segs, null, 2), 'utf8');
    log(`saved transcript to sessions/${stamp}.{md,json}`);
  } catch (e) {
    logErr('failed to save transcript', e);
  }
}

let quitInProgress = false;

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', (event) => {
  if (quitInProgress) return;
  event.preventDefault();
  quitInProgress = true;
  isShuttingDown = true;
  (async () => {
    try { await saveTranscript(); } catch (_) {}
    killBackend();
    app.quit();
  })();
});

process.on('exit', killBackend);
