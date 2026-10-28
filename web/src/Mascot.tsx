import { useEffect, useRef } from "react";
import { motionOk } from "./brand";
import type { ChatReply } from "./contracts";

// The banana mascot: a flat banana with a peel facet (concept 6 of ostrovok-coord/mascot/v7-flat),
// an inline SVG; every animation in styles.css («mascot»).
//   idle     — sways a little, blinks; wiggles on hover
//   hello    — the empty chat: waves, the eyes follow the pointer
//   thinking — tossed like a boomerang: every toss has its own turns, flips and height
//   happy    — the trip is booked: a short jump, both arms up, eyes ^ ^
// Moods of the last answer: no mouth, only eyes, brows and a tilt.
//   curious  — the agent asks: head tilt, one brow up
//   glad     — the agent found options: eyes ^ ^
//   puzzled  — nothing found or the request failed: brows up in the middle, eyes lower
export type MascotState = "idle" | "hello" | "thinking" | "happy" | "curious" | "glad" | "puzzled";

const NOT_FOUND = /нет в каталоге|не нашёл|не найд/i;

// The mood of the avatar next to the last agent answer.
export function moodOf(r: ChatReply | null, failed: boolean): MascotState {
  if (failed) return "puzzled";
  if (!r) return "idle";
  if (r.stage === "booked") return "happy";
  const found = r.plan.offers.length > 0 || r.plan.destinations.length > 0;
  if (NOT_FOUND.test(r.reply) && !found) return "puzzled"; // «X нет в каталоге. По вашему выбору — Y» found Y
  if (r.questions.length) return "curious";
  return "glad";
}

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
          <g transform="translate(-2.58 -1.724) scale(.0665)">
            <path className="arm arm-l" d="M409 518C374 557 329 618 322 663C314 703 331 728 359 703C374 685 389 577 409 518Z" />
            <path
              className="arm arm-r"
              d="M675 546C696 581 718 628 719 665C720 698 703 727 682 730C664 733 655 718 656 694C658 664 659 638 640 600Z"
            />
            <path
              className="body"
              d="M437 173C459 253 432 407 410 515C395 588 370 677 357 744C345 806 359 876 389 918C415 955 466 957 506 945C573 927 626 865 656 793C684 726 695 648 692 563C690 438 678 344 637 270C605 213 556 178 508 148L496 142Z"
            />
            <path
              className="shade"
              d="M508 148C556 178 605 213 637 270C678 344 690 438 692 563C695 648 684 726 656 793C637 839 608 880 573 909C614 855 634 799 633 742C633 684 611 629 592 587C611 535 632 474 640 429C614 323 566 210 508 148Z"
            />
            <path
              className="stem"
              d="M437 177C426 143 409 131 389 112C375 99 389 87 413 74C431 64 455 57 463 63C470 69 475 114 496 142C484 160 461 173 437 177Z"
            />
            <path
              className="outline"
              d="M463 63C470 69 475 114 496 142C505 153 549 174 586 213C661 289 690 398 692 563C695 648 684 726 656 793C626 865 573 927 506 945C466 957 415 955 389 918C359 876 345 806 357 744C370 677 395 588 410 515C432 407 459 253 437 173C426 143 409 131 389 112C375 99 389 87 413 74C431 64 455 57 463 63Z"
            />
            <path
              className="outline facet"
              d="M508 148C566 210 614 323 640 429C632 474 611 535 592 587C611 629 633 684 633 742C634 799 614 855 573 909"
            />
            <g className="brows">
              <path className="brow brow-l" d="M450 345H501" />
              <path className="brow brow-r" d="M534 345H585" />
            </g>
            <path className="joy" d="M455 408Q475.5 372 496 408M539.5 408Q560 372 580.5 408" />
            <g className="eyes">
              <ellipse cx="475.5" cy="395.5" rx="14" ry="20" />
              <ellipse cx="560" cy="395.5" rx="14" ry="20" />
            </g>
          </g>
        </g>
      </svg>
    </span>
  );
}

// The eyes look at the pointer: at most 2 units of the 64-unit drawing (30 in the scaled group).
function followPointer(node: HTMLElement): () => void {
  const move = (e: PointerEvent) => {
    const r = node.getBoundingClientRect();
    const dx = e.clientX - (r.left + r.width / 2);
    const dy = e.clientY - (r.top + r.height / 2);
    const d = Math.hypot(dx, dy) || 1;
    const k = Math.min(1, d / 300) * 30;
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
