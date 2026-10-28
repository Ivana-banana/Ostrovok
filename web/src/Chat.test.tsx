import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe as suite, expect, it } from "vitest";
import fixture from "../../tests/fixtures/chat_reply.json";
import { AgentText, Roadmap, StepCaption } from "./Chat";
import type { ChatReply, Stage } from "./contracts";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const booked = fixture as unknown as ChatReply;
const at = (stage: Stage | null) => (stage ? { ...structuredClone(booked), stage } : null);
const steps = (stage: Stage | null, doneAt: (number | null)[] = []) =>
  new DOMParser().parseFromString(
    renderToStaticMarkup(<Roadmap reply={at(stage)} doneAt={doneAt} onJump={() => {}} />),
    "text/html",
  );
const now = (stage: Stage | null) => steps(stage).querySelector("[aria-current='step'] .label")?.textContent;

suite("чат", () => {
  it("путь поездки: собранный план (assemble) — уже «Корзина»", () => {
    expect(now("clarify")).toBe("Уточнение");
    expect(now("select")).toBe("Вдохновение");
    expect(now("compare")).toBe("Сравнение");
    expect(now("assemble")).toBe("Корзина");
    expect(now("checkout")).toBe("Корзина");
    expect(now("booked")).toBe("Бронь");
    expect(now(null)).toBeUndefined();
  });

  it("#434: путь поездки — список шагов: номер, что делать, у пройденных галочка и итог из данных", () => {
    const doc = steps("compare", [0, 1, null, null, null]);
    const items = [...doc.querySelectorAll("ol.road li")];
    expect(items.map((li) => li.querySelector(".dot")?.textContent)).toEqual(["✓", "✓", "3", "4", "5"]);
    expect(items.map((li) => li.className)).toEqual(["done", "done", "now", "", ""]);
    expect(items[0].querySelector(".what")?.textContent).toBe("Расскажите, куда и когда");
    expect(items[0].querySelector(".result")?.textContent).toBe("2 взрослых · 11.12–13.12");
    expect(items[1].querySelector(".result")?.textContent).toBe("Казань");
    expect(items[2].querySelector(".nowline")?.textContent).toMatch(/^Сейчас: /);
    expect(items[3].querySelector(".result, .nowline")).toBeNull();
    // A done step is a link to its place in the chat; the others are not buttons.
    expect(items[0].querySelector("button")?.textContent).toBe("Уточнение");
    expect(items.slice(2).some((li) => li.querySelector("button"))).toBe(false);
  });

  it("#434: бронь — все шаги пройдены, итог корзины с пометкой «синтетика», номер брони", () => {
    const items = [...steps("booked").querySelectorAll("ol.road li")];
    expect(items.every((li) => li.classList.contains("done"))).toBe(true);
    expect(items[3].querySelector(".result .synth")?.textContent).toBe("синтетика");
    expect(items[4].querySelector(".result")?.textContent).toBe("Бронь OST-0001");
  });

  it("#434: узкий экран — подпись «Шаг N из 5» и полоса прогресса", () => {
    const doc = new DOMParser().parseFromString(
      renderToStaticMarkup(<StepCaption stage="compare" onOpen={() => {}} />),
      "text/html",
    );
    expect(doc.querySelector(".step-caption")?.textContent).toBe("Шаг 3 из 5 · Сравнение");
    expect((doc.querySelector(".step-bar i") as HTMLElement).style.width).toBe("60%");
    expect(renderToStaticMarkup(<StepCaption stage={null} onOpen={() => {}} />)).toBe("");
  });

  it("печать по словам: полный текст сразу доступен чтению с экрана, обрывки скрыты", async () => {
    const box = document.createElement("div");
    const root = createRoot(box);
    const text = "Нашёл четыре направления у тёплого моря в ноябре, от 96 400 ₽ за всех.";
    let done = false;
    await act(async () => root.render(<AgentText text={text} synthetic typing onDone={() => (done = true)} />));
    // the hidden whole text has the same «синтетика» marks as the shown one (TRAP-7)
    const sr = box.querySelector(".sr-only");
    expect(sr?.textContent).toBe(text.replace("₽", "₽синтетика"));
    expect(sr?.querySelector(".price .synth")?.textContent).toBe("синтетика");
    const typed = box.querySelector(".typing");
    expect(typed?.getAttribute("aria-hidden")).toBe("true");
    expect(typed?.textContent?.length ?? 0).toBeLessThan(text.length);
    await act(async () => new Promise((r) => setTimeout(r, 900)));
    expect(done).toBe(true);
    expect(box.querySelector(".sr-only")).toBeNull();
    expect(box.textContent).toBe(text.replace("₽", "₽синтетика"));
    act(() => root.unmount());
  });
});
