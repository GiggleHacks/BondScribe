// Preload injected into the transcript <webview>. Forwards copy postMessages
// from the page to the host renderer via ipcRenderer.sendToHost.
const { ipcRenderer } = require('electron');

window.addEventListener('message', (ev) => {
  const d = ev.data;
  if (!d || typeof d !== 'object') return;
  if (d.type === 'copy' && typeof d.text === 'string' && d.text.length) {
    ipcRenderer.sendToHost('copy', d.text);
  }
});
