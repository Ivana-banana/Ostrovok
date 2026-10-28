import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe as suite, expect, it } from "vitest";
import { AgentText, StagePills } from "./Chat";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const steps = (stage: Parameters<typeof StagePills>[0]["stage"]) =>
  new DOMParser().parseFromString(renderToStaticMarkup(<StagePills stage={stage} />), "text/html");
const now = (stage: Parameters<typeof StagePills>[0]["stage"]) =>
  steps(stage).querySelector("[aria-current='step'] .label")?.textContent;

suite("чат", () => {
  it("этапы сверху: собранный план (assemble) — уже «Корзина»", () => {
    expect(now("clarify")).toBe("Уточнение");
    expect(now("select")).toBe("Вдохновение");
    expect(now("compare")).toBe("Сравнение");
    expect(now("assemble")).toBe("Корзина");
    expect(now("checkout")).toBe("Корзина");
    expect(now("booked")).toBe("Бронь");
    expect(now(null)).toBeUndefined();
  });

  it("этапы — прогресс, а не кнопки: номера, галочка у пройденных, подпись «Шаг N из 5»", () => {
    const doc = steps("compare");
    const items = [...doc.querySelectorAll("ol.stage li")];
    expect(items.map((li) => li.querySelector(".dot")?.textContent)).toEqual(["✓", "✓", "3", "4", "5"]);
    expect(items.map((li) => li.className)).toEqual(["done", "done", "now", "", ""]);
    expect(doc.querySelector("ol.stage button")).toBeNull();
    expect(doc.querySelector(".step-caption")?.textContent).toBe("Шаг 3 из 5 · Сравнение");
    expect((doc.querySelector(".step-bar i") as HTMLElement).style.width).toBe("60%");
    expect(steps(null).querySelector(".step-caption")).toBeNull();
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
