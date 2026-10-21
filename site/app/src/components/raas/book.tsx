import { useMemo, useState } from "react";

import { api, dayKey, fmtRange, fmtTime, friendly, SESSION_TYPES, type Booking, type Slot } from "@/lib/raas";

import { useRaas } from "./state";

interface Day {
  key: string;
  first: number;
  slots: Slot[];
  open: number;
}

function groupByDay(slots: Slot[]): Day[] {
  const days = new Map<string, Day>();
  for (const s of slots) {
    const key = dayKey(s.start);
    const day = days.get(key) ?? { key, first: s.start, slots: [], open: 0 };
    day.slots.push(s);
    if (s.free) day.open++;
    days.set(key, day);
  }
  return [...days.values()];
}

export function BookSection() {
  const { config, me, slots, loaded, loadError, signIn, refresh, notify, setPayNow } = useRaas();
  const days = useMemo(() => groupByDay(slots), [slots]);
  const free = useMemo(() => new Set(slots.filter((s) => s.free).map((s) => s.start)), [slots]);

  const [pickedDay, setPickedDay] = useState<string | null>(null);
  const [start, setStart] = useState<number | null>(null);
  const [hours, setHours] = useState(1);
  const [type, setType] = useState("operator-run");
  const [brief, setBrief] = useState("");
  const [contact, setContact] = useState("");
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);

  const day = days.find((d) => d.key === pickedDay) ?? days.find((d) => d.open > 0) ?? null;
  const fits = (s: number, h: number) => Array.from({ length: h }, (_, i) => s + i * 3600).every((t) => free.has(t));
  const valid = start !== null && fits(start, hours);
  const maxHours = config?.max_hours ?? 8;
  const total = config ? Number(config.price_usdc) * hours : 0;
  const offered = config?.session_types ?? ["operator-run"];

  async function hold() {
    if (!me.wallet && !(await signIn())) return;
    if (start === null || !valid || !agree) return;
    setBusy(true);
    try {
      const { data } = await api<Booking>("/api/bookings", {
        method: "POST",
        json: { slot_start: start, hours, session_type: type, brief, contact, waiver: true },
      });
      setStart(null);
      setAgree(false);
      await refresh();
      setPayNow(data.id);
      document.getElementById("sessions")?.scrollIntoView({ behavior: "smooth" });
    } catch (e) {
      notify(friendly(e), true);
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="book-title" className="rx-section" id="book">
      <div className="rx-section-head">
        <h2 id="book-title">Book the K1</h2>
        <p>
          {config ? `${config.price_usdc} USDC per hour.` : "Priced per hour."} Times are shown in your time zone.
        </p>
      </div>

      <div className="rx-book">
        <div className="rx-card rx-book-main">
          <fieldset className="rx-field">
            <legend>1. Session type</legend>
            <div className="rx-options">
              {Object.entries(SESSION_TYPES).map(([id, t]) => {
                const available = offered.includes(id);
                return (
                  <label className="rx-option" data-disabled={available ? undefined : ""} key={id}>
                    <input
                      checked={type === id}
                      disabled={!available}
                      name="session-type"
                      onChange={() => setType(id)}
                      type="radio"
                      value={id}
                    />
                    <span>
                      <b>{t.title}</b>
                      <small>{t.text}</small>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <fieldset className="rx-field">
            <legend>2. Date and time</legend>
            {!loaded ? (
              <p className="rx-empty">Loading open hours…</p>
            ) : loadError ? (
              <p className="rx-empty">{loadError}</p>
            ) : days.length === 0 ? (
              <p className="rx-empty">
                No open hours in the next two weeks.{" "}
                <a href="https://sixthsenselabs.xyz/EmailRequest">Ask us for a time</a>.
              </p>
            ) : (
              <>
                <div aria-label="Day" className="rx-days" role="radiogroup">
                  {days.map((d) => {
                    const date = new Date(d.first * 1000);
                    return (
                      <button
                        aria-checked={day?.key === d.key}
                        className="rx-day"
                        disabled={d.open === 0}
                        key={d.key}
                        onClick={() => {
                          setPickedDay(d.key);
                          setStart(null);
                        }}
                        role="radio"
                        type="button"
                      >
                        <span>{date.toLocaleDateString(undefined, { weekday: "short" })}</span>
                        <b>{date.getDate()}</b>
                        <small>{d.open ? `${d.open} open` : "Full"}</small>
                      </button>
                    );
                  })}
                </div>
                <div className="rx-duration">
                  <span>Duration</span>
                  <div className="rx-stepper">
                    <button
                      aria-label="One hour less"
                      disabled={hours <= 1}
                      onClick={() => setHours((h) => h - 1)}
                      type="button"
                    >
                      −
                    </button>
                    <output>{hours === 1 ? "1 hour" : `${hours} hours`}</output>
                    <button
                      aria-label="One hour more"
                      disabled={hours >= maxHours}
                      onClick={() => setHours((h) => h + 1)}
                      type="button"
                    >
                      +
                    </button>
                  </div>
                </div>
                <div aria-label="Start time" className="rx-times" role="radiogroup">
                  {day?.slots.map((s) => (
                    <button
                      aria-checked={start === s.start}
                      className="rx-time"
                      disabled={!fits(s.start, hours)}
                      key={s.start}
                      onClick={() => setStart(s.start)}
                      role="radio"
                      type="button"
                    >
                      {fmtTime(s.start)}
                    </button>
                  ))}
                </div>
              </>
            )}
          </fieldset>

          <fieldset className="rx-field">
            <legend>3. Details</legend>
            <label className="rx-input">
              <span>What should we run?</span>
              <textarea
                maxLength={2000}
                onChange={(e) => setBrief(e.target.value)}
                placeholder="Goal, steps, what data you need back"
                rows={4}
                value={brief}
              />
            </label>
            <label className="rx-input">
              <span>Contact for the session (optional)</span>
              <input
                maxLength={200}
                onChange={(e) => setContact(e.target.value)}
                placeholder="Email, Telegram or Discord"
                type="text"
                value={contact}
              />
            </label>
          </fieldset>
        </div>

        <aside aria-label="Booking summary" className="rx-card rx-summary">
          <h3>Summary</h3>
          <dl>
            <div>
              <dt>Machine</dt>
              <dd>Booster K1</dd>
            </div>
            <div>
              <dt>Session</dt>
              <dd>{SESSION_TYPES[type]?.title}</dd>
            </div>
            <div>
              <dt>When</dt>
              <dd>{start !== null ? fmtRange(start, hours) : "Pick a time"}</dd>
            </div>
            <div>
              <dt>Duration</dt>
              <dd>{hours === 1 ? "1 hour" : `${hours} hours`}</dd>
            </div>
          </dl>
          <div className="rx-total">
            <span>Total</span>
            <b>{config ? `${total} USDC` : "…"}</b>
          </div>
          <label className="rx-check">
            <input checked={agree} onChange={(e) => setAgree(e.target.checked)} type="checkbox" />
            <span>
              I agree to the <a href="https://sixthsenselabs.xyz/TermsOfService">rental terms</a> and understand an
              operator supervises every session.
            </span>
          </label>
          <button
            className="rx-btn rx-btn--primary rx-btn--block"
            disabled={busy || (Boolean(me.wallet) && (!valid || !agree))}
            onClick={() => void hold()}
            type="button"
          >
            {busy ? "Holding your slot…" : me.wallet ? "Continue to payment" : "Connect Phantom to book"}
          </button>
          <p className="rx-fine">
            Your slot is held for {config?.hold_minutes ?? 10} minutes while you approve the USDC payment in Phantom.
          </p>
        </aside>
      </div>
    </section>
  );
}
