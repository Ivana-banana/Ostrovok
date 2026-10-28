import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe as suite, expect, it, vi } from "vitest";
import { ROUTES, Thinking } from "./Chat";
import fixture from "../../tests/fixtures/chat_reply.json";
import type { ChatReply } from "./contracts";
import { Mascot, moodOf } from "./Mascot";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const html = (el: React.ReactElement) =>
  new DOMParser().parseFromString(renderToStaticMarkup(el), "text/html").body;

afterEach(() => vi.unstubAllGlobals());

suite("маскот", () => {
  it("банан нарисован в SVG: тело, грань, глаза, руки; без рта и звезды; состояние — класс", () => {
    const b = html(<Mascot size={30} state="happy" />);
    const m = b.querySelector(".mascot");
    expect(m?.classList.contains("happy")).toBe(true);
    expect(m?.getAttribute("aria-hidden")).toBe("true");
    expect(b.querySelector("svg .body")).not.toBeNull();
    expect(b.querySelector("svg .shade")).not.toBeNull();
    expect(b.querySelectorAll("svg .eyes ellipse")).toHaveLength(2);
    expect(b.querySelectorAll("svg .arm")).toHaveLength(2);
    expect(b.querySelector("svg .mouth, svg .star")).toBeNull();
    expect(b.querySelector("img")).toBeNull();
  });

  it("«думает»: банан или самолётик носом вперёд по одному из маршрутов", () => {
    const banana = html(<Thinking variant="banana" />);
    expect(banana.querySelector(".mascot.thinking")).not.toBeNull();
    expect(banana.querySelector(".route")).toBeNull();
    const plane = html(<Thinking variant="plane" />);
    expect(plane.querySelector(".mascot")).toBeNull();
    expect(ROUTES).toContain(plane.querySelector(".route .path path")?.getAttribute("d"));
  });

  it("без согласия на движение банан не подбрасывается (prefers-reduced-motion)", async () => {
    const animate = vi.fn((_frames: Keyframe[], _opts?: KeyframeAnimationOptions) => ({ cancel() {}, onfinish: null }));
    const had = Element.prototype.animate;
    Element.prototype.animate = animate as unknown as Element["animate"];
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    const box = document.createElement("div");
    const root = createRoot(box);
    await act(async () => root.render(<Mascot state="thinking" />));
    await act(async () => new Promise((r) => setTimeout(r, 300)));
    expect(animate).not.toHaveBeenCalled();

    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    await act(async () => root.render(<Mascot state="idle" />));
    await act(async () => root.render(<Mascot state="thinking" />));
    await act(async () => new Promise((r) => setTimeout(r, 300)));
    expect(animate).toHaveBeenCalledTimes(1);
    const [frames] = animate.mock.calls[0];
    expect(frames[0].offset).toBe(0);
    expect(frames.at(-1)?.offset).toBe(1);
    await act(async () => root.unmount());
    Element.prototype.animate = had;
  });

  it("эмоция по ответу: вопрос — любопытство, варианты — доволен, «не нашёл» и сбой — озадачен, бронь — радость", () => {
    const base = fixture as unknown as ChatReply;
    const r = (over: Partial<ChatReply>): ChatReply => ({ ...base, ...over });
    expect(moodOf(null, false)).toBe("idle");
    expect(moodOf(r({ stage: "booked" }), false)).toBe("happy");
    expect(moodOf(r({ stage: "clarify", questions: [{ text: "Откуда вы поедете?", options: ["Из Москвы"] }] }), false)).toBe("curious");
    expect(moodOf(r({ stage: "compare", questions: [], reply: "Нашёл 5 отелей." }), false)).toBe("glad");
    expect(moodOf(r({ stage: "select", questions: [], reply: "Направления «Атлантида» нет в каталоге." }), false)).toBe("puzzled");
    expect(moodOf(r({ stage: "compare", questions: [] }), true)).toBe("puzzled");
  });

  it("у эмоций есть что показать: брови и глаза-дуги в рисунке", () => {
    const b = html(<Mascot state="curious" />);
    expect(b.querySelectorAll("svg .brow")).toHaveLength(2);
    expect(b.querySelector("svg .joy")).not.toBeNull();
    expect(b.querySelector(".mascot")?.classList.contains("curious")).toBe(true);
  });
});
