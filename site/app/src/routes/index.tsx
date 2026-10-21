import { createFileRoute } from "@tanstack/react-router";

import { BookSection } from "@/components/raas/book";
import { SiteFooter } from "@/components/raas/footer";
import { SiteHeader } from "@/components/raas/header";
import { HowSection } from "@/components/raas/how";
import { SessionsSection } from "@/components/raas/sessions";
import { RaasProvider } from "@/components/raas/state";
import { TrustSection } from "@/components/raas/trust";
import { ScrollScrub } from "@/components/scroll-scrub/scroll-scrub";
import { scrollScrubScenes, scrollScrubTheme } from "@/scroll-scrub-scenes";

export const Route = createFileRoute("/")({
  component: Index,
});

// The film tells the story; everything after it is the working booking app.
function Index() {
  return (
    <RaasProvider>
      <div className="rx">
        <SiteHeader />
        <main>
          <ScrollScrub scenes={scrollScrubScenes} theme={scrollScrubTheme} />
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
