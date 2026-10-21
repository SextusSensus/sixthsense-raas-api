// Operator console: sign in (admin wallet via Phantom, or the admin token), edit the weekly
// availability grid, move bookings through their states, attach results, post revenue to peaqOS.
import { $, api, signIn, signOut, put, toast, el, when, bytes, short } from './common.js';

const TOKEN_KEY = 'raas_admin_token';
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const FILTERS = {
  all: () => true,
  action: (b) => ['paid', 'active', 'needs_refund'].includes(b.status),
  upcoming: (b) => b.slot_start + b.hours * 3600 > Date.now() / 1000,
  past: (b) => b.slot_start + b.hours * 3600 <= Date.now() / 1000,
};

let token = '';
try { token = sessionStorage.getItem(TOKEN_KEY) || ''; } catch { /* storage blocked: cookie mode only */ }
let cfg = {};
let slots = new Set(); // "weekday,hour" in the operator time zone, weekday 0 = Monday
let bookings = [];
let filter = 'all';
let timer = null;
let uploading = 0; // auto-refresh pauses while a result upload is in flight, so its progress bar survives
const refs = new Map(); // typed peaqOS refs by booking id, so a re-render does not wipe them

const auth = () => (token ? { Authorization: `Bearer ${token}` } : {});
async function adm(path, opts = {}) {
  const inside = !$('#console').hidden; // sign-in probes run outside and want the raw 'admin only'
  try {
    return await api(path, { ...opts, headers: { ...auth(), ...opts.headers } });
  } catch (e) {
    // Cookie expired or token revoked mid-session: drop back to sign-in instead of toasting every 30 s.
    if (e.message !== 'admin only' || !inside) throw e;
    if (!$('#console').hidden) { setToken(''); leave(); }
    throw new Error('Signed out: the admin session expired or the token was revoked.');
  }
}
const guard = (fn) => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };
const hh = (h) => `${String(h).padStart(2, '0')}:00`;
const trunc = (s, n = 60) => (s && s.length > n ? `${s.slice(0, n - 1)}…` : s || '');
const explorer = (sig) => `https://solscan.io/tx/${sig}${cfg.cluster === 'devnet' ? '?cluster=devnet' : ''}`;

function setToken(t) {
  token = t;
  try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage blocked */ }
}

// --- availability ---------------------------------------------------------------------------

function renderAvail() {
  const cells = [el('span', { 'aria-hidden': 'true' }), ...DAYS.map((d) => el('span', { class: 'dh' }, d))];
  for (let h = 0; h < 24; h++) {
    cells.push(el('span', {}, hh(h)));
    for (let d = 0; d < 7; d++) {
      const k = `${d},${h}`;
      cells.push(el('button', {
        type: 'button', class: slots.has(k) ? 'on' : null, 'aria-pressed': String(slots.has(k)),
        'aria-label': `${DAYS[d]} ${hh(h)}`,
        onclick: (e) => {
          if (!slots.delete(k)) slots.add(k);
          e.currentTarget.classList.toggle('on', slots.has(k));
          e.currentTarget.setAttribute('aria-pressed', String(slots.has(k)));
        },
      }));
    }
  }
  $('#avail').replaceChildren(...cells);
}

const loadAvail = guard(async () => {
  const { data } = await adm('/api/admin/availability');
  slots = new Set(data.map(([d, h]) => `${d},${h}`));
  renderAvail();
});

const saveAvail = guard(async () => {
  const body = { slots: [...slots].map((k) => k.split(',').map(Number)) };
  const { data } = await adm('/api/admin/availability', { method: 'PUT', json: body });
  toast(`Saved ${data.count} open hours`);
});

// --- bookings -------------------------------------------------------------------------------

// ponytail: token mode pulls the whole file into memory before saving; sign in with the wallet
// for multi-GB inputs, that path is a plain link the browser streams.
const download = guard(async (f) => {
  const res = await fetch(`/api/admin/files/${f.id}`, { headers: auth() });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail || `Download failed (${res.status})`);
  const url = URL.createObjectURL(await res.blob());
  const a = el('a', { href: url, download: f.name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
});

function fileItem(f) {
  const name = token
    ? el('button', { type: 'button', class: 'btn small', onclick: () => download(f) }, f.name)
    : el('a', { href: `/api/admin/files/${f.id}`, target: '_blank', rel: 'noopener' }, f.name);
  return el('li', {}, el('span', {}, el('span', { class: 'dim' }, `${f.kind} `), name), el('span', { class: 'dim mono' }, bytes(f.size)));
}

function action(b, label, act, warn, primary) {
  return el('button', {
    type: 'button', class: `btn small${primary ? ' primary' : ''}`,
    onclick: guard(async (e) => {
      const btn = e.currentTarget;
      if (warn && !window.confirm(warn)) return;
      btn.disabled = true;
      try {
        await adm(`/api/admin/bookings/${b.id}/${act}`, { method: 'POST' });
        toast(`${label}: ${b.id}`);
        await refresh(); // complete/purge also change the peaqOS list
      } finally { btn.disabled = false; }
    }),
  }, label);
}

function uploader(b) {
  const input = el('input', { type: 'file', hidden: true, 'aria-label': `Result file for booking ${b.id}` });
  const prog = el('progress', { max: '1', value: '0', hidden: true, 'aria-label': 'Upload progress' });
  const btn = el('button', { type: 'button', class: 'btn small', onclick: () => input.click() }, 'Upload results');
  input.addEventListener('change', guard(async () => {
    const file = input.files[0];
    if (!file) return;
    btn.disabled = true; prog.hidden = false; uploading++;
    try {
      const url = `/api/admin/bookings/${b.id}/results?name=${encodeURIComponent(file.name)}`;
      await put(url, file, (p) => { prog.value = p; }, auth());
      toast(`Attached ${file.name} to ${b.id}`);
      await loadBookings();
    } finally { uploading--; btn.disabled = false; prog.hidden = true; input.value = ''; }
  }));
  return [btn, input, prog];
}

function row(b) {
  const acts = [];
  if (b.status === 'paid') acts.push(action(b, 'Start', 'start', null, true));
  if (b.status === 'active') acts.push(action(b, 'Complete', 'complete', null, true));
  if (b.status === 'active' || b.status === 'complete') acts.push(...uploader(b));
  if (['paid', 'needs_refund', 'expired'].includes(b.status)) {
    acts.push(action(b, 'Mark refunded', 'refunded', `Mark ${b.id} refunded? Its slot is released and its files are purged.`));
  }
  // The server refuses purge on held/paid/active (refund or complete first) and keeps the status of
  // needs_refund, so only finished sessions and pending refunds offer it.
  if (['complete', 'needs_refund'].includes(b.status)) {
    acts.push(action(b, 'Purge now', 'purge', `Purge ${b.id} now? Files are crypto-shredded; this cannot be undone.`));
  }
  return el('tr', {},
    el('td', {}, when(b.slot_start)),
    el('td', {}, String(b.hours)),
    el('td', {}, b.session_type),
    el('td', { class: 'mono', title: b.wallet }, short(b.wallet)),
    el('td', {}, el('span', { class: `status ${b.status}` }, b.status)),
    el('td', {}, `${b.amount_usdc} USDC`),
    el('td', { title: b.brief || null }, trunc(b.brief)),
    el('td', {}, b.contact || ''),
    el('td', {}, b.files.length ? el('ul', { class: 'files' }, b.files.map(fileItem)) : el('span', { class: 'dim' }, '—')),
    el('td', {}, acts));
}

function renderBookings() {
  const rows = bookings.filter(FILTERS[filter]).map(row);
  $('#bk-body').replaceChildren(...(rows.length ? rows : [el('tr', {}, el('td', { colspan: '10', class: 'dim' }, 'No bookings here.'))]));
  for (const btn of $('#filters').querySelectorAll('button[data-f]')) {
    btn.classList.toggle('primary', btn.dataset.f === filter);
    btn.setAttribute('aria-pressed', String(btn.dataset.f === filter));
  }
}

const loadBookings = guard(async () => {
  bookings = (await adm('/api/admin/bookings')).data;
  renderBookings();
});

// --- peaqOS ---------------------------------------------------------------------------------

const loadPeaq = guard(async () => {
  const { data } = await adm('/api/admin/peaq-pending');
  const rows = data.map((p) => {
    const ref = el('input', {
      type: 'text', placeholder: 'peaqOS ref', 'aria-label': `peaqOS reference for ${p.id}`,
      value: refs.get(p.id) ?? null, oninput: () => refs.set(p.id, ref.value),
    });
    const btn = el('button', {
      type: 'button', class: 'btn small primary',
      onclick: guard(async () => {
        btn.disabled = true;
        try {
          await adm(`/api/admin/bookings/${p.id}/peaq-posted`, { method: 'POST', json: { ref: ref.value.trim() } });
          refs.delete(p.id);
          toast(`Posted ${p.id}`);
          await loadPeaq();
        } finally { btn.disabled = false; }
      }),
    }, 'Mark posted');
    return el('tr', {},
      el('td', { class: 'mono' }, p.id),
      el('td', {}, when(p.ts)),
      el('td', {}, `$${(p.usd_cents / 100).toFixed(2)}`),
      el('td', { class: 'mono' }, el('a', { href: explorer(p.pay_sig), target: '_blank', rel: 'noopener', title: p.pay_sig }, short(p.pay_sig))),
      el('td', {}, ref),
      el('td', {}, btn));
  });
  $('#peaq-body').replaceChildren(...(rows.length ? rows : [el('tr', {}, el('td', { colspan: '6', class: 'dim' }, 'Nothing to post.'))]));
});

// --- session --------------------------------------------------------------------------------

const refresh = () => Promise.all([loadBookings(), loadPeaq()]);
// A re-render would yank keyboard focus (or a half-typed ref) out of the tables; wait for it to move on.
const busy = () => uploading || document.hidden || $('#console').querySelector('tbody :focus-visible');

function enter(who) {
  $('#who').textContent = who === 'token' ? 'admin token' : short(who);
  $('#who').title = who === 'token' ? '' : who;
  $('#signout').hidden = false;
  $('#signin').hidden = true;
  $('#console').hidden = false;
  $('#avail-h').focus();
  loadAvail();
  refresh();
  clearInterval(timer);
  timer = setInterval(() => { if (!busy()) refresh(); }, 30_000);
}

function leave() {
  clearInterval(timer);
  bookings = [];
  refs.clear();
  for (const id of ['#bk-body', '#peaq-body', '#avail']) $(id).replaceChildren(); // no stale data left in the DOM
  $('#who').textContent = '';
  $('#signout').hidden = true;
  $('#console').hidden = true;
  $('#signin').hidden = false;
  $('#connect').focus();
}

$('#connect').addEventListener('click', guard(async () => {
  const me = await signIn();
  if (!me.admin) {
    await signOut();
    throw new Error(`${short(me.wallet)} is not an admin wallet`);
  }
  enter(me.wallet);
}));

$('#token-form').addEventListener('submit', guard(async (e) => {
  e.preventDefault();
  const t = $('#token').value.trim();
  if (!t) return;
  token = t;
  try { await adm('/api/admin/availability'); } catch (err) {
    token = '';
    throw new Error(err.message === 'admin only' ? 'Token rejected' : err.message);
  }
  setToken(t);
  $('#token').value = '';
  enter('token');
}));

$('#signout').addEventListener('click', guard(async () => {
  setToken('');
  leave();
  await signOut(); // also drops the wallet cookie, if any
}));

$('#preset').addEventListener('click', () => {
  slots = new Set();
  for (let d = 0; d < 5; d++) for (let h = 9; h < 17; h++) slots.add(`${d},${h}`);
  renderAvail();
});
$('#clear').addEventListener('click', () => { slots = new Set(); renderAvail(); });
$('#save-avail').addEventListener('click', saveAvail);
$('#refresh').addEventListener('click', refresh);
for (const btn of $('#filters').querySelectorAll('button[data-f]')) {
  btn.addEventListener('click', () => { filter = btn.dataset.f; renderBookings(); });
}

guard(async () => {
  cfg = (await api('/api/config')).data;
  $('#tz').textContent = cfg.operator_tz;
  $('#machine').textContent = cfg.machine_id;
  if (token) {
    try { await adm('/api/admin/availability'); return enter('token'); } catch (e) {
      setToken('');
      toast(`Stored token rejected: ${e.message}`, true);
    }
  }
  const { data: me } = await api('/api/me');
  if (me.admin) enter(me.wallet);
  else if (me.wallet) toast(`${short(me.wallet)} is signed in but is not an admin wallet`, true);
})();
