// Helpers for the operator console (admin.html): API calls, Phantom sign-in, formatting.
export const $ = (sel, el = document) => el.querySelector(sel);

export async function api(path, { method = 'GET', json, body, headers = {} } = {}) {
  const res = await fetch(path, {
    method, credentials: 'same-origin',
    headers: json ? { 'Content-Type': 'application/json', ...headers } : headers,
    body: json ? JSON.stringify(json) : body,
  });
  const data = (res.headers.get('content-type') || '').includes('json') ? await res.json() : null;
  if (!res.ok) throw new Error(data?.detail || `Request failed (${res.status})`);
  return { status: res.status, data };
}

export const phantom = () => (window.phantom?.solana?.isPhantom ? window.phantom.solana : null);

export function openPhantom() {
  // On phones the extension does not exist; open this page inside Phantom's in-app browser instead.
  const mobile = /Android|iPhone|iPad/i.test(navigator.userAgent);
  location.href = mobile
    ? `https://phantom.app/ul/browse/${encodeURIComponent(location.href)}?ref=${encodeURIComponent(location.origin)}`
    : 'https://phantom.app/download';
}

export async function signIn() {
  const p = phantom();
  if (!p) { openPhantom(); throw new Error('Install Phantom to continue.'); }
  const { publicKey } = await p.connect();
  const wallet = publicKey.toString();
  const { data: ch } = await api('/api/auth/challenge', { method: 'POST', json: { wallet } });
  const { signature } = await p.signMessage(new TextEncoder().encode(ch.message), 'utf8');
  const sig = btoa(String.fromCharCode(...signature));
  const { data } = await api('/api/auth/verify', { method: 'POST', json: { wallet, nonce: ch.nonce, signature: sig } });
  return data;
}

export async function signOut() {
  await api('/api/auth/logout', { method: 'POST' });
  try { await phantom()?.disconnect(); } catch { /* already disconnected */ }
}

export const short = (a) => (a ? `${a.slice(0, 4)}…${a.slice(-4)}` : '');

const dtf = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
export const when = (unix) => (unix ? dtf.format(new Date(unix * 1000)) : '');

export function bytes(n) {
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function toast(msg, err = false) {
  const t = document.createElement('div');
  t.className = `toast${err ? ' err' : ''}`;
  t.setAttribute('role', err ? 'alert' : 'status');
  t.textContent = msg;
  document.body.append(t);
  setTimeout(() => t.remove(), err ? 7000 : 3500);
}

export function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, '');
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  e.append(...kids.flat().filter((k) => k != null && k !== false));
  return e;
}

// Upload with progress (fetch has no upload progress). Body is the raw file: the server encrypts
// the stream as it arrives, so plaintext never lands in a temp file.
export function put(url, file, onProgress, headers = {}) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('PUT', url);
    for (const [k, v] of Object.entries(headers)) x.setRequestHeader(k, v);
    x.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    x.onload = () => {
      let data = null;
      try { data = JSON.parse(x.responseText); } catch { /* non-JSON error page */ }
      x.status < 300 ? resolve(data) : reject(new Error(data?.detail || `Upload failed (${x.status})`));
    };
    x.onerror = () => reject(new Error('Upload interrupted. Check your connection and try again.'));
    x.send(file);
  });
}
