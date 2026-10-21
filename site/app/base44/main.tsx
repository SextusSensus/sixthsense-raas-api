// Standalone RAAS page for the Base44 site: same booking components as the Higgsfield build,
// a static hero instead of the scroll film, and API calls straight to raas-api.sixthsenselabs.xyz.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { BookSection } from "@/components/raas/book";
import { SiteFooter } from "@/components/raas/footer";
import { SiteHeader } from "@/components/raas/header";
import { HowSection } from "@/components/raas/how";
import { SessionsSection } from "@/components/raas/sessions";
import { RaasProvider } from "@/components/raas/state";
import { TrustSection } from "@/components/raas/trust";

import heroUrl from "./k1-hero.jpg";
import "./hero.css";

function Hero() {
  return (
    <section aria-labelledby="hero-title" className="rx-hero">
      <img
        alt="The Booster K1 humanoid robot standing in a dark studio"
        className="rx-hero-img"
        height={895}
        src={heroUrl}
        width={1600}
      />
      <div className="rx-hero-copy">
        <p className="rx-hero-kicker">Robotics as a service</p>
        <h1 id="hero-title">Rent a humanoid robot by the hour.</h1>
        <p className="rx-hero-lede">
          Book time on our Booster K1 for experiments, data capture, benchmarks or coursework. Pay in USDC with
          Phantom.
        </p>
        <ul className="rx-hero-tags">
          <li>Booster K1</li>
          <li>Live on Solana</li>
          <li>Leveraging peaqOS</li>
        </ul>
        <div className="rx-hero-actions">
          <a className="rx-btn rx-btn--primary" href="#book">
            Book an hour
          </a>
          <a className="rx-btn rx-btn--ghost" href="#how">
            How it works
          </a>
        </div>
      </div>
    </section>
  );
}

function RaasPage() {
  return (
    <RaasProvider>
      <div className="rx">
        <SiteHeader />
        <main>
          <Hero />
          <HowSection />
          <BookSection />
          <SessionsSection />
          <TrustSection />
        </main>
        <SiteFooter />
      </div>
    </RaasProvider>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("RAAS page needs a #root element");
createRoot(root).render(
  <StrictMode>
    <RaasPage />
  </StrictMode>,
);
