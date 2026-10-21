# Sixth Sense Labs RAAS: design brief

**Design read:** For small labs, solo robotics developers and college courses who need real humanoid
hardware for an hour, not a purchase order. Register: a precise lab instrument. Calm, exact, trustworthy,
crypto-native without hype.

**Concept spine:** The duty log. The robot's hours are the product, so the site reads like the K1's own
service log: the film is the K1 waking on a dark bench, the chapters are the rental lifecycle (book, send,
run, return, erase), and the booking grid is the log's open pages.

**Delivery tier:** cinema (Lenis-free: the scrub engine owns scroll-to-video time; transform-only chrome motion).

**Locked palette** (the user's existing sixthsenselabs.xyz brand tokens; explicit brand colors override the
near-black + orange ban):
- ink-900 `#121110` page, ink-850 `#1c1a18` panel, ink-800 `#24211f` well, ink-700 `#3a3632` hatch
- bone-100 `#ede9e3` text, ink-400 `#a39d94` muted, ink-500 `#8c867d` faint
- signal `#e8771b` the one accent (selection, primary action, completed lifecycle steps), ok `#8fbf7f` for erase receipts
Defense: matches the parent site so RAAS reads as the same company, and orange echoes the K1's copper joint caps.

**Locked type:** Martian Grotesk variable (the brand face; width axis used: 125-150% light for display,
100% for body) + Martian Mono for real data only (times, amounts, IDs, signatures). No serif.

Animation mode: animated-website

**Journey shape:** single-shot. One subject (the K1) seen ever more closely; no distinct worlds.

**Journey (chapters over the one film):**
1. Wake. Focal: K1 silhouette, rim-lit in darkness. "Rent a humanoid robot by the hour." Our Booster K1, on real hardware, booked in one-hour blocks and paid in USDC.
2. Book. Focal: camera starts orbit, key light rises on the chest plate. "Pick an hour." Open hours come straight from the robot's schedule. Hold a slot for ten minutes while you pay with Phantom.
3. Send. Focal: push past the shoulder toward the head and visor. "Send your files." Models, datasets, scripts. Encrypted the moment they arrive and delivered to the robot only while your session runs.
4. Run. Focal: joints catch the light, the hand begins to rise. "We run it on the K1." An operator supervises every session: data capture, benchmarking, coursework, teleop later.
5. Return. Focal: full three-quarter front view, lit, hand raised. "Results back. Then we erase it." Files leave the robot when the session ends; your vault key is destroyed after you download.

**World grammar (byte-identical preamble for every frame):** cinematic product film still, seamless matte
charcoal studio fading to darkness, single warm key from upper left, thin bone-white rim from behind right,
faint orange glow on a polished dark concrete floor, 50mm lens at chest height, shallow depth of field,
locked exposure, no text, no logos.

**Camera:** one continuous slow orbit about 150 degrees, rear-left to front-right, with a gentle push-in;
the robot stays centered; the right hand rises slowly in the last third.

**Mobile framing:** subject in the center-safe third at all times; mobile encode capped at 720p.

**Delivery budget:** <=32 MiB desktop clip, <=16 MiB mobile clip.

**Direction update (user request mid-build):** "make the UI way better and professional, keep it simple and
modern like most services". The page after the film is now a conventional, clean booking product: a consistent
button system (primary / ghost / text) replaces the bespoke per-CTA garments, cards with one radius, Calendly-style
day + time pickers, a sticky checkout summary, and a sessions dashboard. The film stays as the hero.

**Section plan (after the journey):**
1. How it works: four-step card row (a real sequence, so numbered).
2. Book the K1: session type options, day strip + time slots + duration stepper, details, sticky summary with total and pay action.
3. Your sessions: dashboard cards with five-step progress (Reserved, Paid, On the robot, Results, Erased), pay + hold timer, file drop, results, erase receipt.
4. Your data, our robot: two cards, the erase sequence and the on-chain identity spec.
5. Footer.
Eyebrow budget: 2 (one used: the film's first kicker).

**Anatomy note:** the K1 has no hands, its forearms end in rounded nubs. The generated film's last seconds show a
raised hand, so the film is cut at 11.25 s, before the arm lifts. Never prompt or show hands or fingers.

**Asset plan:** one Kling 3.0 pro 15 s take (start + end keyframes from the K1 reference) cut to 11.25 s and sliced
into five seam-exact chapter clips with desktop/mobile encodes and exact-frame posters; storyboard and three section
boards in refs/; generated monogram for favicon, touch icon, manifest icons; OG card composed from the film's
final frame in the brand type (no extra generation).

**Buttons:** Connect Phantom (ghost), Continue to payment (primary, block), Pay N USDC with Phantom (primary),
Release hold (text), file drop field, Download links (text link), Book an hour (ghost).
