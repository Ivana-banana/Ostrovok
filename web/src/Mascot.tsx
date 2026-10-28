import { useEffect, useRef } from "react";
import { motionOk } from "./brand";

// The banana mascot: an inline SVG, every animation in styles.css («mascot»).
//   idle     — breathes, blinks, looks around; wiggles on hover
//   hello    — the empty chat: waves, the eyes follow the pointer
//   thinking — tossed like a boomerang: every toss has its own turns, flips and height
//   happy    — the trip is booked: jumps, the eyes become ^^
export type MascotState = "idle" | "hello" | "thinking" | "happy";

export function Mascot({ size = 30, state = "idle" }: { size?: number; state?: MascotState }) {
  const el = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const node = el.current;
    if (!node || !motionOk()) return;
    if (state === "hello") return followPointer(node);
    if (state === "thinking") return tossLoop(node.querySelector("svg"));
  }, [state]);
  return (
    <span ref={el} className={`mascot ${state}`} style={{ width: size, height: size }} aria-hidden="true">
      <svg width={size} height={size} viewBox="0 0 64 64">
        <g className="bw">
          <path className="arm arm-l" d="M27 43C24 44 21 45 19 48" />
          <path className="arm arm-r" d="M53 37C56 36 58 33 59 30" />
          <path
            className="skin"
            d="M28 9C45 10 56 22 55 37C54 51 43 59 27 59C22 59 18 57 19 53C25 48 27 41 27 31C27 22 26 16 23 12C23 10 25 9 28 9Z"
          />
          <path className="shine" d="M33 13.5C42 17 47.5 26 47.5 37" />
          <path className="ridge" d="M29 53C38 51 45 46 49.5 39" />
          <path className="stem" d="M23.5 11.5C22.5 8 23.5 4.5 27 3L30.5 4.5C28.5 6.5 28 8.5 28.5 10.5Z" />
          <ellipse className="tip" cx="20.5" cy="55" rx="2.3" ry="1.9" />
          <g className="face">
            <g className="eyes">
              <ellipse cx="35.5" cy="30" rx="3.3" ry="4.2" />
              <ellipse cx="45.5" cy="29" rx="3.3" ry="4.2" />
              <g className="glint">
                <circle cx="36.5" cy="28.3" r="1.2" />
                <circle cx="46.5" cy="27.3" r="1.2" />
              </g>
            </g>
            <path className="joy" d="M32.5 30.5Q35.5 26 38.5 30.5M42.5 29.5Q45.5 25 48.5 29.5" />
            <ellipse className="cheek" cx="32" cy="37" rx="2.5" ry="1.5" />
            <ellipse className="cheek" cx="50" cy="35.5" rx="2.5" ry="1.5" />
            <path className="mouth" d="M38 37C39.5 39.3 43 39.3 44.5 36.6" />
          </g>
        </g>
      </svg>
    </span>
  );
}

// The pupils look at the pointer: at most 2 units of the 64-unit drawing.
function followPointer(node: HTMLElement): () => void {
  const move = (e: PointerEvent) => {
    const r = node.getBoundingClientRect();
    const dx = e.clientX - (r.left + r.width / 2);
    const dy = e.clientY - (r.top + r.height / 2);
    const d = Math.hypot(dx, dy) || 1;
    const k = Math.min(1, d / 300) * 2;
    node.style.setProperty("--lx", `${((dx / d) * k).toFixed(2)}px`);
    node.style.setProperty("--ly", `${((dy / d) * k).toFixed(2)}px`);
  };
  window.addEventListener("pointermove", move);
  return () => window.removeEventListener("pointermove", move);
}

const rand = (a: number, b: number) => a + Math.random() * (b - a);
const pick = <T,>(xs: T[]) => xs[Math.floor(Math.random() * xs.length)];

// One toss: wind-up, flight with turns in the drawing plane and flips across it (scaleX
// through zero reads as a turn in depth), a springy catch. The banana may land mirrored.
function tossFrames(mirror: number): { frames: Keyframe[]; ms: number; mirror: number } {
  const dir = pick([-1, 1]);
  const turns = pick([1, 1, 2, 2, 3]);
  const flips = pick([0, 1, 1, 2]);
  const lift = rand(4, 9);
  const flight = 380 + turns * 230;
  const ms = 120 + flight + 520;
  const frames: Keyframe[] = [];
  const at = (t: number, rot: number, sx: number, sy: number, y: number) =>
    frames.push({
      offset: Math.min(1, t / ms),
      transform: `translateY(${y.toFixed(2)}px) rotate(${rot.toFixed(1)}deg) scale(${sx.toFixed(3)}, ${sy.toFixed(3)})`,
    });
  at(0, 0, mirror, 1, 0);
  at(120, -18 * dir, mirror * 1.06, 0.92, 1); // wind-up: lean back, squash
  for (let i = 1; i <= 24; i++) {
    const k = i / 24;
    const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    const rot = -18 * dir * (1 - e) + dir * 360 * turns * e;
    const sx = mirror * Math.cos(Math.PI * flips * e) * (1 + 0.12 * Math.sin(Math.PI * k));
    at(120 + flight * k, rot, sx, 1 + 0.12 * Math.sin(Math.PI * k), -lift * Math.sin(Math.PI * k));
  }
  const end = flips % 2 ? -mirror : mirror;
  for (let i = 1; i <= 8; i++) {
    const k = i / 8;
    const w = Math.exp(-5 * k) * Math.cos(k * Math.PI * 3.2);
    at(120 + flight + 520 * k, 10 * dir * w, end * (1 + 0.08 * w), 1 - 0.08 * w, 0);
  }
  return { frames, ms, mirror: end };
}

function tossLoop(svg: SVGSVGElement | null): (() => void) | undefined {
  if (!svg || typeof svg.animate !== "function") return;
  let mirror = 1;
  let anim: Animation | null = null;
  let timer = 0;
  let live = true;
  const next = () => {
    if (!live) return;
    const t = tossFrames(mirror);
    mirror = t.mirror;
    anim = svg.animate(t.frames, { duration: t.ms, fill: "forwards", easing: "linear" });
    anim.onfinish = () => {
      timer = window.setTimeout(next, rand(150, 650));
    };
  };
  timer = window.setTimeout(next, rand(0, 250));
  return () => {
    live = false;
    window.clearTimeout(timer);
    anim?.cancel();
  };
}
