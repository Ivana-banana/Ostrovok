import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import fixture from "../../tests/fixtures/chat_reply.json";
import type { ChatReply, TripSpec } from "./contracts";
import { HotelFilters } from "./HotelFilters";

const spec = (fixture as unknown as ChatReply).plan.spec;
const with_ = (over: Partial<TripSpec>): TripSpec => ({ ...spec, ...over });
const empty = with_({ meal: null, refundable: null, hotel_budget_rub: null, nights: 5 });

// «10 000 ₽» holds a no-break space
const plain = (t: string) => t.replace(/\s/g, " ").trim();

async function mount(s: TripSpec, disabled = false) {
  const sent: string[] = [];
  const box = document.createElement("div");
  document.body.append(box);
  const root = createRoot(box);
  await act(async () =>
    root.render(<HotelFilters spec={s} disabled={disabled} onSend={(t) => sent.push(t)} />),
  );
  const button = (text: string) =>
    [...box.querySelectorAll("button")].find((b) => plain(b.textContent ?? "").startsWith(text))!;
  const done = () => {
    act(() => root.unmount());
    box.remove();
  };
  return { box, sent, button, done };
}

describe("#431: фильтры отелей чипами", () => {
  it("без фильтров чипы говорят «любое»; с фильтрами показывают выбор", async () => {
    const a = await mount(empty);
    expect(plain(a.box.textContent ?? "")).toContain("Питаниелюбое");
    expect(plain(a.box.textContent ?? "")).toContain("Условия отменылюбая");
    expect(plain(a.box.textContent ?? "")).toContain("Бюджет отеляне задан");
    a.done();
    const b = await mount(with_({ meal: "AI", refundable: true, hotel_budget_rub: 40000, nights: 5 }));
    expect(plain(b.box.textContent ?? "")).toContain("Питаниевсё включено");
    expect(plain(b.box.textContent ?? "")).toContain("Условия отменыбесплатная");
    expect(plain(b.box.textContent ?? "")).toContain("Бюджет отеля8 000 ₽ за ночь");
    b.done();
  });

  it("питание: клик по варианту шлёт фразу в чат и закрывает список", async () => {
    const t = await mount(empty);
    await act(async () => t.button("Питание").click());
    expect(t.box.querySelector("[aria-label='Изменить: питание']")).not.toBeNull();
    await act(async () => t.button("Завтрак и ужин").click());
    expect(t.sent).toEqual(["С полупансионом"]);
    expect(t.box.querySelector("[aria-label='Изменить: питание']")).toBeNull();
    t.done();
  });

  it("питание: выбранный вариант повторно не шлётся", async () => {
    const t = await mount(with_({ meal: "BB" }));
    await act(async () => t.button("Питание").click());
    await act(async () => t.button("Завтрак").click());
    expect(t.sent).toEqual([]);
    t.done();
  });

  it("отмена: клик шлёт «С бесплатной отменой»; включённый фильтр недоступен", async () => {
    const t = await mount(empty);
    await act(async () => t.button("Условия отмены").click());
    expect(t.sent).toEqual(["С бесплатной отменой"]);
    t.done();
    const on = await mount(with_({ refundable: true }));
    expect(on.button("Условия отмены").disabled).toBe(true);
    expect(on.button("Условия отмены").getAttribute("aria-pressed")).toBe("true");
    on.done();
  });

  it("бюджет отеля: сумма за ночь уходит фразой «Отель до N ₽ за ночь»", async () => {
    const t = await mount(empty);
    await act(async () => t.button("Бюджет отеля").click());
    await act(async () => t.button("до 10 000").click());
    await act(async () => t.button("Готово").click());
    expect(t.sent).toEqual(["Отель до 10000 ₽ за ночь"]);
    t.done();
  });

  it("бюджет отеля без числа ночей: сумма за все ночи", async () => {
    const t = await mount(with_({ ...empty, nights: null }));
    await act(async () => t.button("Бюджет отеля").click());
    await act(async () => t.button("до 60 000").click());
    await act(async () => t.button("Готово").click());
    expect(t.sent).toEqual(["Отель до 60000 ₽ за все ночи"]);
    t.done();
  });

  it("бюджет отеля: «Отмена» и та же сумма ничего не шлют", async () => {
    const t = await mount(with_({ hotel_budget_rub: 40000, nights: 5 }));
    await act(async () => t.button("Бюджет отеля").click());
    await act(async () => t.button("Готово").click());
    await act(async () => t.button("Бюджет отеля").click());
    // another sum is typed, then cancelled: nothing is sent
    await act(async () => t.box.querySelector<HTMLButtonElement>("form .options button")!.click());
    await act(async () => t.button("Отмена").click());
    expect(t.sent).toEqual([]);
    expect(t.button("Готово")).toBeUndefined(); // the editor is closed
    t.done();
  });

  it("чипы недоступны, пока агент занят", async () => {
    const t = await mount(empty, true);
    expect([...t.box.querySelectorAll("button")].every((b) => b.disabled)).toBe(true);
    t.done();
  });
});
