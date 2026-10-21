import { useState } from "react";

import { shortAddr } from "@/lib/raas";

import { useRaas } from "./state";

export function SiteHeader() {
  const { me, signIn, signOut } = useRaas();
  const [busy, setBusy] = useState(false);

  async function connect() {
    setBusy(true);
    await signIn();
    setBusy(false);
  }

  return (
    <header className="rx-header">
      <a className="rx-brand" href="/">
        <span>Sixth Sense Labs</span>
        <span className="rx-brand-tag">RAAS</span>
      </a>
      <nav aria-label="Page sections" className="rx-nav">
        <a href="#how">How it works</a>
        <a href="#book">Book</a>
        <a href="#sessions">Sessions</a>
        <a href="#trust">Data and identity</a>
      </nav>
      {me.wallet ? (
        <button className="rx-wallet" onClick={() => void signOut()} title="Disconnect wallet" type="button">
          <span aria-hidden="true" className="rx-wallet-dot" />
          {shortAddr(me.wallet)}
        </button>
      ) : (
        <button className="rx-btn rx-btn--ghost" disabled={busy} onClick={() => void connect()} type="button">
          {busy ? "Connecting…" : "Connect Phantom"}
        </button>
      )}
    </header>
  );
}
