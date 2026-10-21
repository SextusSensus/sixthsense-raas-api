import { useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  confirmPayment,
  downloadFile,
  fmtBytes,
  fmtRange,
  fmtStamp,
  friendly,
  payWithPhantom,
  pendingSignature,
  SESSION_TYPES,
  STATUS_LABEL,
  uploadFile,
  type Booking,
} from "@/lib/raas";

import { useRaas } from "./state";

const TONE: Record<Booking["status"], string> = {
  held: "warn",
  paid: "accent",
  active: "accent",
  complete: "ok",
  purged: "neutral",
  expired: "neutral",
  needs_refund: "warn",
  refunded: "neutral",
};

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

function Progress({ b }: { b: Booking }) {
  const steps: [string, number | null][] = [
    ["Reserved", b.created],
    ["Paid", b.paid_at],
    ["On the robot", b.started_at],
    ["Results", b.completed_at],
    ["Erased", b.purged_at],
  ];
  return (
    <ol aria-label="Session progress" className="rx-progress">
      {steps.map(([label, at]) => (
        <li data-done={at ? "" : undefined} key={label}>
          <span className="rx-progress-dot" />
          <b>{label}</b>
          <small>{at ? fmtStamp(at) : " "}</small>
        </li>
      ))}
    </ol>
  );
}

function BookingCard({ b }: { b: Booking }) {
  const { config, refresh, notify, payNow, setPayNow } = useRaas();
  const [paying, setPaying] = useState(false);
  const [upload, setUpload] = useState<{ name: string; progress: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const now = useNow(b.status === "held");

  const pay = useCallback(async () => {
    if (!config || paying) return;
    setPaying(true);
    try {
      const sig = pendingSignature(b.id) ?? (await payWithPhantom(b, config));
      notify("Payment sent. Waiting for Solana to confirm.");
      await confirmPayment(b.id, sig);
      notify("Paid. Your hour is booked.");
    } catch (e) {
      notify(friendly(e), true);
    } finally {
      setPaying(false);
      await refresh();
    }
  }, [b, config, notify, paying, refresh]);

  // Start payment right after "Continue to payment", and resume one interrupted by a refresh.
  useEffect(() => {
    if (b.status !== "held" || !config) return;
    if (payNow === b.id) {
      setPayNow(null);
      void pay();
    } else if (pendingSignature(b.id)) {
      void pay();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payNow, b.id, b.status, config]);

  async function send(files: FileList | null) {
    if (!files?.length) return;
    for (const file of Array.from(files)) {
      setUpload({ name: file.name, progress: 0 });
      try {
        await uploadFile(b.id, file, (progress) => setUpload({ name: file.name, progress }));
      } catch (e) {
        notify(`${file.name}: ${friendly(e)}`, true);
        break;
      }
    }
    setUpload(null);
    await refresh();
  }

  async function release() {
    try {
      await api(`/api/bookings/${b.id}/cancel`, { method: "POST" });
    } catch (e) {
      notify(friendly(e), true);
    } finally {
      await refresh();
    }
  }

  const inputs = b.files.filter((f) => f.kind === "input");
  const results = b.files.filter((f) => f.kind === "result");
  const holdLeft = Math.max(0, b.hold_expires - now);
  const holdTotal = (config?.hold_minutes ?? 10) * 60;
  const canUpload = b.status === "paid" || b.status === "active";

  return (
    <article className="rx-card rx-booking" id={`booking-${b.id}`}>
      <header className="rx-booking-head">
        <div>
          <h3>{SESSION_TYPES[b.session_type]?.title ?? b.session_type}</h3>
          <p>{fmtRange(b.slot_start, b.hours)}</p>
        </div>
        <span className="rx-badge" data-tone={TONE[b.status]}>
          {STATUS_LABEL[b.status]}
        </span>
      </header>

      <Progress b={b} />

      {b.status === "held" ? (
        <div className="rx-pay">
          <div className="rx-pay-row">
            <button className="rx-btn rx-btn--primary" disabled={paying} onClick={() => void pay()} type="button">
              {paying ? "Confirming payment…" : `Pay ${b.amount_usdc} USDC with Phantom`}
            </button>
            <button className="rx-btn rx-btn--text" disabled={paying} onClick={() => void release()} type="button">
              Release hold
            </button>
          </div>
          <div aria-hidden="true" className="rx-hold-bar">
            <span style={{ transform: `scaleX(${holdTotal ? holdLeft / holdTotal : 0})` }} />
          </div>
          <p className="rx-fine">
            Hold ends in {Math.floor(holdLeft / 60)}:{String(Math.floor(holdLeft % 60)).padStart(2, "0")}.
          </p>
        </div>
      ) : null}

      {canUpload ? (
        <div className="rx-files">
          <label
            className="rx-drop"
            data-active={dragging ? "" : undefined}
            onDragLeave={() => setDragging(false)}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              void send(e.dataTransfer.files);
            }}
          >
            <input multiple onChange={(e) => void send(e.target.files)} ref={inputRef} type="file" />
            <b>Drop files for the robot</b>
            <span>or click to choose. Up to {config?.max_upload_mb ?? 2048} MB per session.</span>
          </label>
          <div>
            <h4>Your files</h4>
            {inputs.length === 0 && !upload ? <p className="rx-fine">Nothing uploaded yet.</p> : null}
            <ul className="rx-file-list">
              {inputs.map((f) => (
                <li key={f.id}>
                  <span>{f.name}</span>
                  <span className="rx-mono">{fmtBytes(f.size)}</span>
                </li>
              ))}
            </ul>
            {upload ? (
              <div className="rx-upload">
                <span>{upload.name}</span>
                <progress max={1} value={upload.progress} />
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {results.length > 0 && b.status === "complete" ? (
        <div className="rx-results">
          <h4>Results</h4>
          <ul className="rx-file-list">
            {results.map((f) => (
              <li key={f.id}>
                <button className="rx-link" onClick={() => void downloadFile(b.id, f.id)} type="button">
                  Download {f.name}
                </button>
                <span className="rx-mono">{fmtBytes(f.size)}</span>
              </li>
            ))}
          </ul>
          {b.results_expire ? (
            <p className="rx-fine">Available until {fmtStamp(b.results_expire)}, then permanently erased.</p>
          ) : null}
        </div>
      ) : null}

      {b.status === "purged" ? (
        <div className="rx-receipt">
          <b>Erase receipt</b>
          <span>
            Removed from the robot: {b.robot_purged_at ? fmtStamp(b.robot_purged_at) : "never delivered"}
          </span>
          <span>Encryption key destroyed: {b.purged_at ? fmtStamp(b.purged_at) : "pending"}</span>
        </div>
      ) : null}

      {b.status === "needs_refund" ? (
        <p className="rx-note">
          Your payment arrived after the hold expired. We will refund it to the paying wallet.
        </p>
      ) : null}
      {b.status === "refunded" ? <p className="rx-note">Refunded to the paying wallet.</p> : null}

      <footer className="rx-booking-foot">
        <span className="rx-mono">Booking {b.id}</span>
        {b.pay_sig ? (
          <a
            href={`https://solscan.io/tx/${b.pay_sig}${config?.cluster === "devnet" ? "?cluster=devnet" : ""}`}
            rel="noreferrer"
            target="_blank"
          >
            View payment on Solscan
          </a>
        ) : null}
      </footer>
    </article>
  );
}

export function SessionsSection() {
  const { me, bookings, signIn } = useRaas();
  return (
    <section aria-labelledby="sessions-title" className="rx-section" id="sessions">
      <div className="rx-section-head">
        <h2 id="sessions-title">Your sessions</h2>
        <p>Pay, upload files and download results here. Status updates on its own.</p>
      </div>
      {!me.wallet ? (
        <div className="rx-card rx-blank">
          <p>Connect Phantom to see your sessions.</p>
          <button className="rx-btn rx-btn--ghost" onClick={() => void signIn()} type="button">
            Connect Phantom
          </button>
        </div>
      ) : bookings.length === 0 ? (
        <div className="rx-card rx-blank">
          <p>No sessions yet.</p>
          <a className="rx-btn rx-btn--ghost" href="#book">
            Book an hour
          </a>
        </div>
      ) : (
        <div className="rx-bookings">
          {bookings.map((b) => (
            <BookingCard b={b} key={b.id} />
          ))}
        </div>
      )}
    </section>
  );
}
