// Exposed to renderer.html only (not to webviews).
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('app', {
  copyText: (text) => ipcRenderer.invoke('clipboard:write', text),
  readText: () => ipcRenderer.invoke('clipboard:read'),
  onBackendStatus: (cb) => ipcRenderer.on('backend:status', (_e, status) => cb(status)),
  onBackendRestarted: (cb) => ipcRenderer.on('backend:restarted', (_e, info) => cb(info)),
});
