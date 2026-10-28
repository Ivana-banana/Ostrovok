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

  it("текст агента сразу отформатирован: без печати по словам, «синтетика» у цены есть (TRAP-7)", async () => {
    const box = document.createElement("div");
    const root = createRoot(box);
    const text = "Нашёл четыре направления у тёплого моря в ноябре, от 96 400 ₽ за всех. Вода 26 °C.";
    await act(async () => root.render(<AgentText text={text} synthetic />));
    expect(box.querySelector(".typing")).toBeNull();
    expect(box.querySelector(".sr-only")).toBeNull();
    expect(box.querySelector(".price .synth")?.textContent).toBe("синтетика");
    expect(box.querySelector("mark")?.textContent).toBe("26 °C");
    expect(box.textContent).toBe(text.replace("₽", "₽синтетика"));
    act(() => root.unmount());
  });

  it("#468: предложение — список, «синтетика» у каждой цены, детали свёрнуты под «Подробнее»", async () => {
    const box = document.createElement("div");
    const root = createRoot(box);
    const text = [
      "Казань, 11.12–13.12, 2 ноч., 2 взр.",
      "Цены — синтетика.",
      "Климат: в декабре днём ≈ -5.3 °C.",
      "Предлагаю:",
      "Отель: Казантель, Казань, 2★, без питания, невозвратный — 7 900 ₽",
      "Туда: поезд Москва → Казань, 10.12 18:50 → 11.12 07:10 (UTC+3) — 7 800 ₽",
      "Итого 15 700 ₽.",
      "Почему: поездка целиком 15 700 ₽.",
      "Напишите «беру» или номер отеля.",
    ].join("\n");
    await act(async () => root.render(<AgentText text={text} synthetic />));
    const items = [...box.querySelectorAll("ul.offer li")];
    expect(items.map((li) => [...li.querySelectorAll(".pt-row, .pt-sub")].map((x) => x.textContent))).toEqual([
      ["Отель Казантель, Казань7 900 ₽синтетика", "2★ · без питания · невозвратный"],
      ["Туда · поезд7 800 ₽синтетика", "Москва → Казань 10.12 18:50 → 11.12 07:10 · UTC+3"],
      ["Итого15 700 ₽синтетика"],
    ]);
    expect(items.map((li) => li.querySelector("svg.pt-ic") !== null)).toEqual([true, true, false]);
    // #478: route and time are one grid of 2 rows × 2 columns, the right words stand in one column
    const grid = items[1].querySelectorAll(".pt-route");
    expect(grid).toHaveLength(1);
    expect([...grid[0].children].map((c) => c.className || "cell")).toEqual(["pt-from", "cell", "pt-from", "pt-time"]);
    // the arrow is a drawing; the text «→» stays for a screen reader and a copy
    expect(items[1].querySelectorAll(".pt-line[aria-hidden='true']")).toHaveLength(2);
    // TRAP-7: one chip at each price of the list, none at the facts
    expect([...box.querySelectorAll("ul.offer .price")].map((x) => x.textContent)).toEqual([
      "7 900 ₽синтетика",
      "7 800 ₽синтетика",
      "15 700 ₽синтетика",
    ]);
    const more = box.querySelector("details.more") as HTMLDetailsElement;
    expect(more.open).toBe(false);
    expect([...more.querySelectorAll("p")].map((p) => p.textContent?.split(" ")[0])).toEqual(["Цены", "Климат:", "Почему:"]);
    expect(more.querySelector("mark")?.textContent).toBe("5.3 °C");
    expect(box.lastElementChild?.textContent).toBe("Напишите «беру» или номер отеля.");
    act(() => root.unmount());
  });
});
