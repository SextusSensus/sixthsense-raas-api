import { useRaas } from "./state";

export function HowSection() {
  const { config } = useRaas();
  const hold = config?.hold_minutes ?? 10;
  const steps = [
    {
      title: "Pick an hour",
      text: `Choose a session type and an open time. We hold it for ${hold} minutes while you pay.`,
    },
    {
      title: "Pay in USDC",
      text: "Approve one USDC transfer in Phantom. Priced per hour, no subscription.",
    },
    {
      title: "Send your files",
      text: "Upload models, datasets or scripts. They stay encrypted until your session starts.",
    },
    {
      title: "Get your results",
      text: "Download what the K1 produced. Then your files are erased from the robot and our servers.",
    },
  ];
  return (
    <section aria-labelledby="how-title" className="rx-section" id="how">
      <div className="rx-section-head">
        <h2 id="how-title">How it works</h2>
        <p>From booking to results in four steps. No account, just your Phantom wallet.</p>
      </div>
      <ol className="rx-steps">
        {steps.map((s, i) => (
          <li className="rx-step" key={s.title}>
            <span className="rx-step-n">{i + 1}</span>
            <h3>{s.title}</h3>
            <p>{s.text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
