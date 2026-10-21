import { useRaas } from "./state";

const MACHINE_URL =
  "https://machines.peaq.xyz/machine/72070072199752035588865179268289351973289970295321663729669764070389137395940";

export function TrustSection() {
  const { config } = useRaas();
  const ttl = config?.results_ttl_hours ?? 72;
  return (
    <section aria-labelledby="trust-title" className="rx-section" id="trust">
      <div className="rx-section-head">
        <h2 id="trust-title">Your data, our robot</h2>
        <p>What happens to your files, and how to check the machine you are renting.</p>
      </div>
      <div className="rx-trust">
        <div className="rx-card">
          <h3>What happens to your files</h3>
          <ol className="rx-ordered">
            <li>Encrypted the moment they arrive, with a key unique to your booking.</li>
            <li>Delivered to the robot only while your session is running.</li>
            <li>Removed from the robot as soon as the session ends.</li>
            <li>The key is destroyed {ttl} hours after your results are ready. Encrypted server backups age out within 7 days.</li>
          </ol>
        </div>
        <div className="rx-card">
          <h3>On-chain identity</h3>
          <p className="rx-card-lede">Live on Solana, leveraging peaqOS for the machine identity and bond.</p>
          <dl className="rx-spec">
            <div>
              <dt>Machine</dt>
              <dd>Booster K1</dd>
            </div>
            <div>
              <dt>DID</dt>
              <dd className="rx-mono">did:peaq:7207…5940</dd>
            </div>
            <div>
              <dt>Home chain</dt>
              <dd>Solana</dd>
            </div>
            <div>
              <dt>Bond</dt>
              <dd>Basic tier, active until Oct 12, 2027</dd>
            </div>
            <div>
              <dt>Activated</dt>
              <dd>Oct 12, 2026</dd>
            </div>
          </dl>
          <a className="rx-link" href={MACHINE_URL} rel="noreferrer" target="_blank">
            View the live record on peaqOS
          </a>
        </div>
      </div>
    </section>
  );
}
