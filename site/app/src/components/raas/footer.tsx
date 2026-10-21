export function SiteFooter() {
  return (
    <footer className="rx-footer">
      <span>© {new Date().getFullYear()} Sixth Sense Labs</span>
      <nav aria-label="Footer">
        <a href="https://sixthsenselabs.xyz">sixthsenselabs.xyz</a>
        <a href="https://sixthsenselabs.xyz/TermsOfService">Terms</a>
        <a href="https://sixthsenselabs.xyz/PrivacyPolicy">Privacy</a>
        <a href="https://x.com/Sextus_Sensus">X</a>
      </nav>
    </footer>
  );
}
