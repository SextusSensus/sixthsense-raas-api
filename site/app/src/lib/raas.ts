// Client helpers for the RAAS booking API (proxied same-origin at /api/*) and the Phantom wallet.
// Browser-only: call these from effects and event handlers, never during render.

export interface Config {
  price_usdc: string;
  treasury: string;
  mint: string;
  cluster: string;
  session_types: string[];
  operator_tz: string;
  max_hours: number;
  max_upload_mb: number;
  results_ttl_hours: number;
  hold_minutes: number;
  machine_id: string;
}

export interface Me {
  wallet: string | null;
  admin: boolean;
}

export interface Slot {
  start: number;
  free: boolean;
}

export interface FileRow {
  id: string;
  kind: "input" | "result";
  name: string;
  size: number;
  created: number;
}

export type Status =
  | "held"
  | "paid"
  | "active"
  | "complete"
  | "purged"
  | "expired"
  | "needs_refund"
  | "refunded";

export interface Booking {
  id: string;
  wallet: string;
  slot_start: number;
  hours: number;
  session_type: string;
  price_units: number;
  status: Status;
  brief: string;
  contact: string;
  created: number;
  hold_expires: number;
  pay_sig: string | null;
  paid_at: number | null;
  started_at: number | null;
  completed_at: number | null;
  robot_purged_at: number | null;
  purged_at: number | null;
  memo: string;
  amount_usdc: string;
  results_expire: number | null;
  files: FileRow[];
}

// Where the booking API lives. Empty = same origin (the Higgsfield site proxies /api/*). The Base44
// bundle is built with VITE_RAAS_API=https://raas-api.sixthsenselabs.xyz and calls it directly; that
// host is the same site as sixthsenselabs.xyz, so the session cookie stays first-party.
export const API_BASE: string = (import.meta.env.VITE_RAAS_API as string | undefined) ?? "";

export class ApiError extends Error {}

function detailOf(data: unknown): string | null {
  const detail = (data as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" ? detail : null;
}

export async function api<T>(
  path: string,
  init: { method?: string; json?: unknown } = {},
): Promise<{ status: number; data: T }> {
  const hasBody = init.json !== undefined;
  const res = await fetch(API_BASE + path, {
    method: init.method ?? "GET",
    credentials: API_BASE ? "include" : "same-origin",
    headers: hasBody ? { "Content-Type": "application/json" } : undefined,
    body: hasBody ? JSON.stringify(init.json) : undefined,
  });
  const isJson = (res.headers.get("content-type") ?? "").includes("json");
  const data: unknown = isJson ? await res.json() : null;
  if (!res.ok) {
    throw new ApiError(
      detailOf(data) ??
        (res.status >= 500
          ? "The booking service is unavailable. Try again in a minute."
          : "Check the details and try again."),
    );
  }
  return { status: res.status, data: data as T };
}

interface PhantomKey {
  toString(): string;
}

interface Phantom {
  isPhantom?: boolean;
  connect(): Promise<{ publicKey: PhantomKey }>;
  disconnect(): Promise<void>;
  signMessage(message: Uint8Array, display: "utf8"): Promise<{ signature: Uint8Array }>;
  signAndSendTransaction(tx: unknown): Promise<{ signature: string }>;
}

export function phantom(): Phantom | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { phantom?: { solana?: Phantom }; solana?: Phantom };
  // window.phantom.solana is current; window.solana is the legacy slot (other wallets can take it,
  // so it only counts when it says it is Phantom).
  const p = w.phantom?.solana ?? (w.solana?.isPhantom ? w.solana : undefined);
  return p?.isPhantom ? p : null;
}

/** Phantom injects its provider shortly after load; give it a moment before calling it missing. */
export async function findPhantom(waitMs = 1500): Promise<Phantom | null> {
  const until = Date.now() + waitMs;
  for (;;) {
    const p = phantom();
    if (p || Date.now() > until) return p;
    await new Promise((r) => setTimeout(r, 100));
  }
}

function missingPhantom(): ApiError {
  if (inFrame()) return new ApiError("Phantom cannot connect inside a preview frame. Open the site in its own tab.");
  openPhantom();
  return new ApiError("Phantom was not found in this browser. Install it from phantom.app, then refresh this page.");
}

export function openPhantom(): void {
  // Phones have no extension: reopen this page inside Phantom's in-app browser instead.
  const mobile = /Android|iPhone|iPad/i.test(navigator.userAgent);
  const here = window.location.href;
  if (mobile) {
    window.location.href = `https://phantom.app/ul/browse/${encodeURIComponent(here)}?ref=${encodeURIComponent(window.location.origin)}`;
    return;
  }
  // Desktop: keep the booking page (and the renter's picks) open; the install page gets its own tab.
  window.open("https://phantom.app/download", "_blank", "noopener");
}

export function inFrame(): boolean {
  try {
    return window.self !== window.top;
  } catch {
    return true; // cross-origin parent: definitely framed
  }
}

// Phantom rejects with plain {code, message} objects, not Error instances.
export function friendly(e: unknown): string {
  const err = e as { message?: unknown; code?: unknown } | null;
  const msg = typeof err?.message === "string" ? err.message : String(e);
  if (err?.code === 4001 || /reject|cancel|declin/i.test(msg)) return "Cancelled in Phantom.";
  if (err?.code === -32603 || /unexpected error/i.test(msg)) {
    return inFrame()
      ? "Phantom cannot connect inside a preview frame. Open the site in its own tab."
      : "Phantom could not connect. Unlock Phantom, refresh the page and try again.";
  }
  return msg && msg !== "[object Object]" ? msg : "Something went wrong. Try again.";
}

export async function signIn(): Promise<Me> {
  const p = await findPhantom();
  if (!p) throw missingPhantom();
  const { publicKey } = await p.connect();
  const wallet = publicKey.toString();
  const { data: ch } = await api<{ nonce: string; message: string }>("/api/auth/challenge", {
    method: "POST",
    json: { wallet },
  });
  const { signature } = await p.signMessage(new TextEncoder().encode(ch.message), "utf8");
  const sig = btoa(String.fromCharCode(...signature));
  const { data } = await api<Me>("/api/auth/verify", {
    method: "POST",
    json: { wallet, nonce: ch.nonce, signature: sig },
  });
  return data;
}

export async function signOut(): Promise<void> {
  await api("/api/auth/logout", { method: "POST" });
  try {
    await phantom()?.disconnect();
  } catch {
    // already disconnected
  }
}

// The payment signature is remembered so a refresh mid-confirmation still finishes the booking
// instead of leaving a paid hold to expire.
const payKey = (id: string) => `raas-pay-${id}`;

export function pendingSignature(id: string): string | null {
  try {
    return localStorage.getItem(payKey(id));
  } catch {
    return null;
  }
}

function rememberSignature(id: string, sig: string | null): void {
  try {
    if (sig) localStorage.setItem(payKey(id), sig);
    else localStorage.removeItem(payKey(id));
  } catch {
    // storage blocked: confirmation still polls during this visit
  }
}

const TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";
const MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";

/** Build one USDC transfer + memo, have Phantom sign and send it. Returns the tx signature. */
export async function payWithPhantom(b: Booking, cfg: Config): Promise<string> {
  const p = await findPhantom();
  if (!p) throw missingPhantom();
  const [{ PublicKey, Transaction, TransactionInstruction }, { Buffer }] = await Promise.all([
    import("@solana/web3.js"),
    import("buffer"),
  ]);
  const { publicKey } = await p.connect();
  const payer = new PublicKey(publicKey.toString());
  if (payer.toBase58() !== b.wallet) {
    throw new ApiError("Switch Phantom to the wallet that made this booking.");
  }
  const token = new PublicKey(TOKEN_PROGRAM);
  const mint = new PublicKey(cfg.mint);
  const ata = (owner: InstanceType<typeof PublicKey>) =>
    PublicKey.findProgramAddressSync(
      [owner.toBuffer(), token.toBuffer(), mint.toBuffer()],
      new PublicKey(ATA_PROGRAM),
    )[0];
  // SPL Token transferChecked: [12, amount u64 LE, decimals u8]
  const data = Buffer.alloc(10);
  data.writeUInt8(12, 0);
  data.writeBigUInt64LE(BigInt(b.price_units), 1);
  data.writeUInt8(6, 9);
  const tx = new Transaction().add(
    new TransactionInstruction({
      programId: token,
      data,
      keys: [
        { pubkey: ata(payer), isSigner: false, isWritable: true },
        { pubkey: mint, isSigner: false, isWritable: false },
        { pubkey: ata(new PublicKey(cfg.treasury)), isSigner: false, isWritable: true },
        { pubkey: payer, isSigner: true, isWritable: false },
      ],
    }),
    new TransactionInstruction({
      programId: new PublicKey(MEMO_PROGRAM),
      keys: [{ pubkey: payer, isSigner: true, isWritable: false }],
      data: Buffer.from(b.memo, "utf8"),
    }),
  );
  const { data: bh } = await api<{ blockhash: string }>("/api/blockhash");
  tx.recentBlockhash = bh.blockhash;
  tx.feePayer = payer;
  const { signature } = await p.signAndSendTransaction(tx);
  rememberSignature(b.id, signature);
  return signature;
}

/** Poll the server until it has verified the finalized transaction. */
export async function confirmPayment(id: string, signature: string): Promise<Booking> {
  for (let i = 0; i < 40; i++) {
    try {
      const r = await api<Booking>(`/api/bookings/${id}/pay`, { method: "POST", json: { signature } });
      if (r.status === 200) {
        rememberSignature(id, null);
        return r.data;
      }
    } catch (e) {
      if (!(e instanceof ApiError) || !/unavailable|unreachable|RPC/i.test(e.message)) {
        rememberSignature(id, null);
        throw e;
      }
    }
    await new Promise((r) => setTimeout(r, 3000));
  }
  throw new ApiError("Payment sent. Solana has not finalized it yet; this page finishes it when you come back.");
}

/** Uploads go straight to the booking server on a signed link (no 100 MB proxy cap). */
export async function uploadFile(id: string, file: File, onProgress: (fraction: number) => void): Promise<void> {
  const { data } = await api<{ url: string }>(
    `/api/bookings/${id}/upload-link?name=${encodeURIComponent(file.name)}`,
    { method: "POST" },
  );
  await new Promise<void>((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("PUT", data.url);
    x.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    x.onload = () => {
      if (x.status < 300) return resolve();
      let detail: string | null = null;
      try {
        detail = detailOf(JSON.parse(x.responseText));
      } catch {
        // not JSON
      }
      reject(new ApiError(detail ?? `Upload failed (${x.status}).`));
    };
    x.onerror = () => reject(new ApiError("Upload interrupted. Check your connection and try again."));
    x.send(file);
  });
}

export async function downloadFile(bookingId: string, fileId: string): Promise<void> {
  const { data } = await api<{ url: string }>(`/api/bookings/${bookingId}/files/${fileId}/link`);
  window.location.assign(data.url);
}

export const SESSION_TYPES: Record<string, { title: string; text: string }> = {
  "operator-run": {
    title: "Operator-run",
    text: "Send your plan and files. Our operator runs it on the K1 and returns the results.",
  },
  "remote-view": {
    title: "Remote view",
    text: "Watch the K1 live from your browser while our operator runs your session.",
  },
  teleop: {
    title: "Teleop",
    text: "Drive the K1 yourself with an operator on site. Coming soon.",
  },
};

export const STATUS_LABEL: Record<Status, string> = {
  held: "Awaiting payment",
  paid: "Booked",
  active: "In progress",
  complete: "Results ready",
  purged: "Completed and erased",
  expired: "Expired",
  needs_refund: "Refund pending",
  refunded: "Refunded",
};

const dayFmt = () => new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
const timeFmt = () => new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

export const fmtDay = (unix: number) => dayFmt().format(new Date(unix * 1000));
export const fmtTime = (unix: number) => timeFmt().format(new Date(unix * 1000));
export const fmtStamp = (unix: number) => `${fmtDay(unix)}, ${fmtTime(unix)}`;
export const fmtRange = (start: number, hours: number) =>
  `${fmtDay(start)}, ${fmtTime(start)} to ${fmtTime(start + hours * 3600)}`;
export const shortAddr = (a: string) => `${a.slice(0, 4)}…${a.slice(-4)}`;

export function fmtBytes(n: number): string {
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function dayKey(unix: number): string {
  const d = new Date(unix * 1000);
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}
