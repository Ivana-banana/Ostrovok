import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe as suite, expect, it } from "vitest";
import { AgentText, StagePills } from "./Chat";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const now = (stage: Parameters<typeof StagePills>[0]["stage"]) =>
  new DOMParser().parseFromString(renderToStaticMarkup(<StagePills stage={stage} />), "text/html")
    .querySelector("[aria-current='step']")?.textContent;

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
