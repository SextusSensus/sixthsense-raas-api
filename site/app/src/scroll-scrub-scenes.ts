/**
 * Scroll film for the RAAS page: ONE continuous take of the K1, sliced into five seam-exact
 * segments (consecutive frames of the same clip) so each chapter gets its own band of scroll.
 * Every poster is the exact first frame of the encoded clip beside it.
 */
import type {
  ScrollScrubScene,
  ScrollScrubTheme,
} from "@/components/scroll-scrub/scroll-scrub";

export const scrollScrubTheme: ScrollScrubTheme = {
  accent: "#e8771b",
  background: "#121110",
  ink: "#ede9e3",
  muted: "#a39d94",
};

const clip = (n: number) => ({
  clip: `/assets/world/scene-0${n}.mp4`,
  mobileClip: `/assets/world/scene-0${n}-mobile.mp4`,
  mobilePoster: `/assets/world/scene-0${n}-mobile-poster.jpg`,
  poster: `/assets/world/scene-0${n}-poster.jpg`,
});

export const scrollScrubScenes: ScrollScrubScene[] = [
  {
    ...clip(1),
    id: "rent",
    label: "Rent",
    kicker: "Robotics as a service",
    title: "Rent a humanoid robot by the hour.",
    body: "Book time on our Booster K1 for experiments, data capture, benchmarks or coursework. Pay in USDC with Phantom.",
    tags: ["Booster K1", "Live on Solana", "Leveraging peaqOS"],
    align: "left",
  },
  {
    ...clip(2),
    id: "hardware",
    label: "Hardware",
    title: "Real hardware, not a simulator.",
    body: "A 95 cm humanoid with 22 degrees of freedom, a stereo depth camera and an onboard NVIDIA Jetson Orin.",
    align: "right",
  },
  {
    ...clip(3),
    id: "files",
    label: "Files",
    title: "Send your files.",
    body: "Models, datasets and scripts are encrypted the moment they arrive and reach the robot only during your session.",
    align: "left",
  },
  {
    ...clip(4),
    id: "operator",
    label: "Operator",
    title: "An operator runs every session.",
    body: "We supervise the K1 on site while your experiment runs, so you get real-world results without the risk.",
    tags: ["Data capture", "Benchmarking", "Coursework"],
    align: "right",
  },
  {
    ...clip(5),
    id: "results",
    label: "Results",
    title: "Results back. Files erased.",
    body: "Download your results, then everything you sent is wiped from the robot and our servers.",
    align: "left",
  },
];
