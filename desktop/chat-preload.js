// Preload injected into the right-pane AI <webview>. Receives 'insert-and-send'
// from the host renderer, inserts the text into the site's chat input, and
// submits. Per-site tuning for ChatGPT, Gemini, and Claude with a generic
// fallback.
const { ipcRenderer } = require('electron');

function siteKey() {
  const h = location.hostname;
  if (h.includes('claude.ai')) return 'claude';
  if (h.includes('gemini.google.com')) return 'gemini';
  if (h.includes('chatgpt.com') || h.includes('openai.com')) return 'chatgpt';
  return 'generic';
}

const SITE = {
  chatgpt: {
    input: [
      'div#prompt-textarea',
      'textarea#prompt-textarea',
      'div[contenteditable="true"].ProseMirror',
    ],
    send: [
      'button[data-testid="send-button"]',
      'button#composer-submit-button',
      'button[aria-label="Send prompt"]',
      'button[aria-label*="Send" i]',
    ],
    order: ['exec', 'paste'],
  },
  gemini: {
    input: [
      'rich-textarea .ql-editor',
      'div.ql-editor[contenteditable="true"]',
    ],
    send: [
      'button.send-button',
      'button[aria-label="Send message"]',
      'button[aria-label*="Submit" i]',
      'button[aria-label*="Send" i]',
    ],
    order: ['paste', 'exec'],
  },
  claude: {
    input: [
      'div.ProseMirror[contenteditable="true"]',
      'div[contenteditable="true"][translate="no"]',
      'div[contenteditable="true"][role="textbox"]',
    ],
    send: [
      'button[aria-label="Send message"]',
      'button[aria-label="Send Message"]',
      'button[aria-label*="Send" i]',
      'button[type="submit"]',
    ],
    order: ['paste', 'exec'],
  },
  generic: {
    input: [
      'div[contenteditable="true"][role="textbox"]',
      'div[contenteditable="true"]',
      'textarea',
    ],
    send: [
      'button[data-testid="send-button"]',
      'button[aria-label*="Send" i]',
      'button[type="submit"]',
    ],
    order: ['exec', 'paste'],
  },
};

function visible(el) {
  return !!el && el.offsetParent !== null && !el.disabled;
}

function findFirst(selectors) {
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (visible(el)) return el;
  }
  return null;
}

function getText(el) {
  if (!el) return '';
  if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') return el.value || '';
  return el.innerText || el.textContent || '';
}

function clearEditor(el) {
  if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
    const proto = Object.getPrototypeOf(el);
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, '');
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return;
  }
  el.focus();
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  try { document.execCommand('delete', false); } catch (_) {}
}

function execInsert(el, text) {
  el.focus();
  if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
    const proto = Object.getPrototypeOf(el);
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, text);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return;
  }
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  document.execCommand('insertText', false, text);
}

function pasteInsert(el, text) {
  el.focus();
  const dt = new DataTransfer();
  dt.setData('text/plain', text);
  el.dispatchEvent(new ClipboardEvent('paste', {
    clipboardData: dt, bubbles: true, cancelable: true,
  }));
}

function dispatchEnter(el) {
  const opts = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true };
  el.dispatchEvent(new KeyboardEvent('keydown', opts));
  el.dispatchEvent(new KeyboardEvent('keypress', opts));
  el.dispatchEvent(new KeyboardEvent('keyup', opts));
}

async function insertAndSend(text) {
  const cfg = SITE[siteKey()];
  const input = findFirst(cfg.input);
  if (!input) return { ok: false, reason: 'no-input (' + siteKey() + ')' };

  const normalizeLen = (s) => s.replace(/\s+/g, ' ').trim().length;
  const expectedLen = normalizeLen(text);
  // Use tokens from the MIDDLE and END of the text. Head probes miss the
  // common Gemini/Quill case where the prompt lands but the tail is dropped.
  const tailProbe = text.slice(-Math.min(48, text.length)).replace(/\s+/g, ' ').trim();
  const midProbe  = text.slice(Math.floor(text.length / 2), Math.floor(text.length / 2) + 32)
                        .replace(/\s+/g, ' ').trim();

  function verify() {
    const got = getText(input).replace(/\s+/g, ' ').trim();
    if (tailProbe && !got.endsWith(tailProbe) && !got.includes(tailProbe)) return false;
    if (midProbe && !got.includes(midProbe)) return false;
    // Require at least 90% of expected length to reject heavy truncation.
    return got.length >= Math.floor(expectedLen * 0.9);
  }

  let inserted = false;
  for (const method of cfg.order) {
    try { clearEditor(input); } catch (_) {}
    await new Promise((r) => setTimeout(r, 40));
    try {
      if (method === 'exec') execInsert(input, text);
      else if (method === 'paste') pasteInsert(input, text);
    } catch (e) {
      console.warn('[chat-preload] insert method threw', method, e);
      continue;
    }
    // Quill/ProseMirror may take a tick to reconcile; poll briefly.
    for (let i = 0; i < 8; i++) {
      await new Promise((r) => setTimeout(r, 60));
      if (verify()) { inserted = true; break; }
    }
    if (inserted) break;
    console.warn('[chat-preload]', method, 'insert appeared truncated on', siteKey(),
      '— got', normalizeLen(getText(input)), 'of', expectedLen);
  }
  if (!inserted) return { ok: false, reason: 'insert-truncated (' + siteKey() + ')' };

  await new Promise((r) => setTimeout(r, 150));

  for (let i = 0; i < 8; i++) {
    const btn = findFirst(cfg.send);
    if (btn) { btn.click(); return { ok: true, via: 'button', site: siteKey() }; }
    await new Promise((r) => setTimeout(r, 80));
  }

  dispatchEnter(input);
  await new Promise((r) => setTimeout(r, 200));
  const after = getText(input).replace(/\s+/g, ' ').trim();
  if (tailProbe && !after.includes(tailProbe)) return { ok: true, via: 'enter', site: siteKey() };

  const btnLate = findFirst(cfg.send);
  if (btnLate) { btnLate.click(); return { ok: true, via: 'button-late', site: siteKey() }; }
  return { ok: false, reason: 'submit-not-detected (' + siteKey() + ')' };
}

ipcRenderer.on('insert-and-send', async (_e, text) => {
  if (typeof text !== 'string' || !text.length) {
    ipcRenderer.sendToHost('sendToAI:result', { ok: false, reason: 'empty' });
    return;
  }
  try {
    const res = await insertAndSend(text);
    ipcRenderer.sendToHost('sendToAI:result', res);
  } catch (e) {
    ipcRenderer.sendToHost('sendToAI:result', { ok: false, reason: String(e && e.message || e) });
  }
});
