import { renderToStaticMarkup } from "react-dom/server";
import { describe as suite, expect, it } from "vitest";
import fixture from "../../tests/fixtures/chat_reply.json";
import { Board } from "./Board";
import type { ChatReply } from "./contracts";
import { changeValue, localTime } from "./format";

const base = fixture as unknown as ChatReply;
const noop = () => {};
// The fixture cart holds only the transfer; the hotel is outside it, so its fixes are hidden.
const hotelBought = structuredClone(base);
hotelBought.cart.items = [...base.cart.items, ...base.plan.offers.filter((o) => o.offer_id === "h1")];

function render(r: ChatReply): Document {
  const html = renderToStaticMarkup(
    <Board reply={r} busy={false} removed={[]} onSend={noop} onCart={noop} onCheckout={noop} />,
  );
  return new DOMParser().parseFromString(html, "text/html");
}

suite("доска поездки", () => {
  it("#321: машина с ночёвками — часы за рулём вместо «в пути», без повтора в минусах", () => {
    const r = structuredClone(base);
    const [d] = r.plan.destinations;
    r.plan.destinations = [
      { ...d, travel_hours: 33, tradeoffs: ["за рулём 18 ч, 1 ночёвка в пути", "от аэропорта 197 км"] },
      { ...d, name: "Без ночёвок", travel_hours: 7.5, tradeoffs: ["дорога 7.5 ч"] },
    ];
    const cards = [...render(r).querySelectorAll(".dest")].map((c) => c.textContent ?? "");
    expect(cards[0]).toContain("за рулём 18 ч, 1 ночёвка в пути");
    expect(cards[0]).not.toContain("в пути 33");
    expect(cards[0]).toContain("Минусы: от аэропорта 197 км");
    expect(cards[0].match(/за рулём/g)).toHaveLength(1);
    expect(cards[1]).toContain("в пути 7");
    expect(cards[1]).toContain("Минусы: дорога 7.5 ч");
  });
  it("TRAP-7: у каждой синтетической цены есть пометка «синтетика»", () => {
    const doc = render(hotelBought);
    const prices = [...doc.querySelectorAll(".price")];
    // hotel, transport, destination, bundle, fix in the issue, cart item + total, voucher item + total
    expect(prices.length).toBeGreaterThanOrEqual(9);
    for (const p of prices) expect(p.querySelector(".synth")?.textContent).toBe("синтетика");
    // a ₽ sign outside a labelled price is a price without the label
    const bare = doc.body.innerHTML.replace(/<span class="price">.*?<\/span><\/span>/g, "");
    expect(bare).not.toMatch(/\d ₽<\/(td|p|li|span)>/);
  });

  it("#225: пакеты без транспорта не называются «поездка целиком»", () => {
    expect(render(base).body.textContent).toContain("Варианты поездки целиком");
    const r = structuredClone(base);
    r.plan.offers = r.plan.offers.filter((o) => o.kind !== "transport");
    const text = render(r).body.textContent ?? "";
    expect(text).not.toContain("Варианты поездки целиком");
    expect(text).toContain("дороги в плане нет");
  });

  it("TRAP-7: каждая сумма в ₽ на доске стоит рядом с «синтетика»", () => {
    const r = structuredClone(hotelBought);
    r.plan.changes = [
      { what: "итог", before: "22 500 ₽", after: "23 000 ₽" },
      { what: "отель", before: "6 400 ₽ за ночь", after: "7 200 ₽ за ночь" },
      { what: "budget_rub", before: "100 000 ₽", after: "80 000 ₽" },
      { what: "бюджет отеля", before: "12 000 ₽", after: "10 000 ₽" },
    ];
    r.plan.explanations = [
      { offer_id: "h1", why: "дешевле на 1 100 ₽", tradeoffs: ["дороже на 3 300 ₽"], risks: ["штраф 2 000 ₽"] },
    ];
    r.plan.bundles = r.plan.bundles.map((b) => ({ ...b, tradeoffs: ["дороже на 900 ₽", "не укладывается в бюджет 80 000 ₽: поездка целиком 91 000 ₽", "отель не укладывается в бюджет отеля 10 000 ₽: 12 500 ₽ (синтетика)"] }));
    r.plan.destinations = r.plan.destinations.map((d) => ({ ...d, why: `от 5 000 ₽ в сезон` }));
    r.plan.issues = r.plan.issues.map((i) => ({ ...i, text: `${i.text} (ранний заезд 1 200 ₽)` }));
    const text = render(r).body.textContent ?? "";
    const amounts = [...text.matchAll(/\d[\d \u00a0]*₽/g)];
    expect(amounts.length).toBeGreaterThanOrEqual(15);
    expect(text).not.toMatch(/синтетика\s*\(?синтетика/); // a sum that has the label is not marked twice
    for (const m of amounts) {
      const before = text.slice(Math.max(0, m.index! - 48), m.index!);
      const after = text.slice(m.index! + m[0].length, m.index! + m[0].length + 24);
      // a budget is the number the user typed, not a price of ours
      const userBudget = /бюджет[а-я]*( отеля)?:? ?(было )?$|бюджет[^→]*→ стало $/i;
      if (userBudget.test(before)) {
        expect(after, `${before}${m[0]}${after}`).not.toMatch(/^\s*\(?синтетика/);
        continue;
      }
      expect(after, `${before}${m[0]}${after}`).toMatch(/^\s*\(?синтетика/);
    }
  });

  it("TRAP-7: цена реальных данных без пометки", () => {
    const real = structuredClone(base);
    real.plan.offers = real.plan.offers.map((o) => ({ ...o, synthetic: false }));
    const doc = render(real);
    const row = [...doc.querySelectorAll("tr")].find((r) => r.textContent?.includes("Отель у Кремля"));
    expect(row?.querySelector(".synth")).toBeNull();
  });

  it("TRAP-1: плашка «без ИИ», когда модель недоступна", () => {
    expect(render({ ...base, llm: false }).body.textContent).toContain("без ИИ");
    expect(render({ ...base, llm: true }).body.textContent).not.toContain("без ИИ");
  });

  it("TRAP-5: время событий местное, как в ISO, без перевода в пояс браузера", () => {
    const tz = process.env.TZ;
    process.env.TZ = "America/New_York";
    try {
      expect(localTime("2026-12-11T07:10:00+03:00")).toBe("11.12 07:10");
      const text = render(hotelBought).querySelector("[aria-label='Таймлайн']")?.textContent ?? "";
      expect(text).toContain("11.12 07:10");
      expect(text).toContain("прибытие 07:10 раньше заезда 14:00");
      expect(text).toContain("Ранний заезд"); // the fix offer is shown by its offer_id
    } finally {
      process.env.TZ = tz;
    }
  });

  it("#39: у отеля несколько тарифов — в таблице одна строка, выбранный тариф виден", () => {
    const r = structuredClone(base);
    const hotels = r.plan.offers.filter((o) => o.kind === "hotel");
    const first = hotels[0];
    if (first.kind !== "hotel") throw new Error("fixture has no hotel");
    const [low, high] = [
      { ...first, offer_id: "h-low", meal: "RO" as const, price_rub: 1000 },
      { ...first, offer_id: "h-high", meal: "AI" as const, price_rub: 2000 },
    ];
    const rest = r.plan.offers.filter((o) => o.kind !== "hotel" || o.title !== first.title);
    r.plan.offers = [low, high, ...rest];
    const rows = (sel: string[]) => {
      r.plan.selected = sel;
      const doc = render(r);
      return [...doc.querySelectorAll("[aria-label='Сравнение отелей'] tbody tr")].filter((tr) =>
        tr.textContent?.includes(first.title),
      );
    };
    expect(rows([])).toHaveLength(1); // the first tariff of the hotel is its row
    expect(rows([])[0].textContent).toContain("без питания");
    expect(rows(["h-high"])).toHaveLength(1); // the selected tariff is its row
    expect(rows(["h-high"])[0].textContent).toContain("всё включено");
  });

  it("таблица сравнения отелей: не меньше 5 признаков", () => {
    const head = render(base).querySelector("[aria-label='Сравнение отелей'] thead")?.textContent;
    for (const col of ["Цена за всё", "Отмена", "Питание", "До центра", "Рейтинг", "Звёзды"])
      expect(head).toContain(col);
  });

  it("сравнение транспорта: время прибытия и время в пути", () => {
    const t = render(base).querySelector("[aria-label='Сравнение транспорта']")?.textContent ?? "";
    expect(t).toContain("11.12 07:10");
    expect(t).toContain("11 ч 40 мин");
    expect(t).toContain("поезд");
  });

  it("пересборка: «было → стало»", () => {
    const t = render(base).querySelector("[aria-label='Что изменилось']")?.textContent ?? "";
    expect(t).toContain("было 11–13 декабря → стало 13–15 декабря");
    expect(changeValue("None")).toBe("не задано");
    expect(changeValue("['Казань']")).toBe("Казань");
    expect(changeValue("2026-12-11")).toBe("11.12.2026");
  });

  it("ваучер: номер брони, позиции с offer_id, итог и пометка об имитации", () => {
    const v = render(base).querySelector("[aria-label='Ваучер']")?.textContent ?? "";
    expect(v).toContain("OST-0001");
    expect(v).toContain("x1");
    expect(v).toContain("2 500 ₽");
    expect(v).toContain("имитирован");
  });

  it("ваучер настоящей брони: статус вместо пометки об имитации (#376)", () => {
    const r = structuredClone(base);
    r.voucher = { ...r.voucher!, simulated: false, status: "created" };
    const v = render(r).querySelector("[aria-label='Ваучер']")?.textContent ?? "";
    expect(v).not.toContain("имитирован");
    expect(v).toContain("Статус брони: создана, ждёт подтверждения");
  });

  it("корзина: предупреждение о бюджете до подтверждения", () => {
    const r = structuredClone(base);
    r.stage = "checkout";
    r.voucher = null;
    r.plan.spec.budget_rub = 500;
    const cart = render(r).querySelector("[aria-label='Корзина']")?.textContent ?? "";
    expect(cart).toContain("больше бюджета");
    expect(cart).toContain("Подтвердить бронь на 600 ₽ (синтетика) · оплата имитируется");
  });

  it("#41: бюджет отеля — своя строка в параметрах, не «Бюджет» на поездку", () => {
    const r = structuredClone(base);
    r.plan.spec.budget_rub = null;
    r.plan.spec.hotel_budget_rub = 40000;
    const rows = [...render(r).querySelectorAll("[aria-label='Параметры поездки'] dl > div")].map(
      (d) => d.textContent ?? "",
    );
    expect(rows.some((t) => t.startsWith("Бюджет отеля") && t.includes("40 000 ₽"))).toBe(true);
    expect(rows.some((t) => t.startsWith("Бюджет") && !t.startsWith("Бюджет отеля"))).toBe(false);
  });

  it("#41: корзина с отелем дороже бюджета отеля — предупреждение до подтверждения", () => {
    const r = structuredClone(hotelBought);
    r.stage = "checkout";
    r.voucher = null;
    r.plan.spec.hotel_budget_rub = 15000; // the hotel costs 19 600 ₽
    const cart = render(r).querySelector("[aria-label='Корзина']");
    const note = cart?.querySelector("[role='note']");
    expect(note?.textContent).toContain("Отель дороже бюджета отеля 15 000 ₽");
    expect(note?.querySelector(".price")?.textContent).toBe("4 600 ₽синтетика");
    r.plan.spec.hotel_budget_rub = 40000;
    expect(render(r).querySelector("[aria-label='Корзина'] [role='note']")).toBeNull();
  });

  it("TRAP-7: превышение бюджета и кнопка подтверждения помечены по данным корзины", () => {
    const r = structuredClone(base);
    r.stage = "checkout";
    r.voucher = null;
    r.plan.spec.budget_rub = 500;
    const note = render(r).querySelector("[aria-label='Корзина'] [role='note']");
    expect(note?.querySelector(".price")?.textContent).toBe("100 ₽синтетика");
    r.cart.items = r.cart.items.map((o) => ({ ...o, synthetic: false }));
    const real = render(r).querySelector("[aria-label='Корзина']");
    expect(real?.querySelector("button.primary")?.textContent).toBe(
      "Подтвердить бронь на 600 ₽ · оплата имитируется",
    );
    expect(real?.querySelector(".synth")).toBeNull();
  });

  it("направления: карточки с кнопкой выбора на этапе inspire", () => {
    const doc = render({ ...base, stage: "inspire", voucher: null });
    const card = doc.querySelector("[aria-label='Направления'] .card");
    expect(card?.textContent).toContain("Казань");
    expect(card?.querySelector("button")?.textContent).toBe("Выбрать");
  });

  it("таймлайн: события убранной из корзины позиции помечены, после «Вернуть» — обычный вид", () => {
    const r = structuredClone(base);
    r.stage = "checkout";
    r.voucher = null;
    const full = r.plan.offers.filter((o) => o.offer_id === "t1");
    r.cart.items = full; // the cart holds t1; h1 was removed but stays in plan.selected
    r.plan.timeline.push({ at: "2026-12-11T14:00:00+03:00", title: "Заселение", offer_id: "h1" });
    const items = () => [...render(r).querySelectorAll(".timeline li")];
    const [arrival, checkin] = items();
    expect(arrival.textContent).not.toContain("убрано из корзины");
    expect(checkin.textContent).toContain("Заселение");
    expect(checkin.textContent).toContain("убрано из корзины");
    expect(checkin.classList.contains("gone")).toBe(true);
    // «Вернуть»: the hotel is in the cart again
    r.cart.items = r.plan.offers.filter((o) => o.offer_id === "t1" || o.offer_id === "h1");
    for (const li of items()) expect(li.textContent).not.toContain("убрано из корзины");
  });

  it("таймлайн: после брони помечены события позиций, которых нет в купленной корзине", () => {
    const r = structuredClone(base); // stage booked
    r.plan.timeline.push({ at: "2026-12-11T14:00:00+03:00", title: "Заселение", offer_id: "h1" });
    r.cart.items = r.plan.offers.filter((o) => o.offer_id === "t1"); // only the train was bought
    const [arrival, checkin] = [...render(r).querySelectorAll(".timeline li")];
    expect(arrival.textContent).not.toContain("убрано из корзины");
    expect(checkin.textContent).toContain("убрано из корзины");
  });

  it("таймлайн: пока корзины нет (assemble), пометок нет", () => {
    const r = structuredClone(base);
    r.cart.items = [];
    const doc = render({ ...r, stage: "assemble", voucher: null });
    expect(doc.querySelector(".timeline")?.textContent).not.toContain("убрано из корзины");
  });

  it("таймлайн: доп. услуга убранного отеля не предлагается, услуга имеющегося — да", () => {
    const r = structuredClone(base);
    r.stage = "checkout";
    r.voucher = null;
    const issue = () => render(r).querySelector("[aria-label='Таймлайн'] .issue");
    r.cart.items = r.plan.offers.filter((o) => o.offer_id === "t1"); // hotel removed
    expect(issue()).toBeNull();
    r.cart.items = r.plan.offers.filter((o) => o.offer_id === "t1" || o.offer_id === "h1");
    expect(issue()?.textContent).toContain("Ранний заезд");
    expect(issue()?.querySelector("button")?.textContent).toBe("В корзину");
  });

  it("S9: приоритеты чипами по порядку; на сравнении есть кнопки правки", () => {
    const r = { ...structuredClone(base), stage: "compare" as const, voucher: null };
    r.plan.spec.priorities = ["price", "cancellation"];
    const sec = render(r).querySelector("[aria-label='Приоритеты']");
    const chips = [...(sec?.querySelectorAll(".chip") ?? [])].map((c) => c.querySelector("span")?.textContent);
    expect(chips).toEqual(["1. цена", "2. отмена"]);
    const up = sec?.querySelector("button[aria-label='Поднять приоритет цена']") as HTMLButtonElement;
    expect(up.disabled).toBe(true); // the first one cannot go up
    expect(sec?.querySelector("button[aria-label='Опустить приоритет цена']")).not.toBeNull();
    expect(sec?.querySelector("button[aria-label='Добавить приоритет рейтинг']")).not.toBeNull();
    expect(sec?.querySelector("button[aria-label='Добавить приоритет цена']")).toBeNull();
  });

  it("S9: после брони чипы видны, но порядок не правится; пустой список скрыт", () => {
    const r = structuredClone(base); // stage booked
    r.plan.spec.priorities = ["price", "cancellation"];
    const sec = render(r).querySelector("[aria-label='Приоритеты']");
    expect(sec?.textContent).toContain("отмена");
    expect(sec?.querySelectorAll("button").length).toBe(0);
    r.plan.spec.priorities = [];
    expect(render(r).querySelector("[aria-label='Приоритеты']")).toBeNull();
  });

  it("S9: приоритеты не заданы на сравнении — подсказка и добавление", () => {
    const r = { ...structuredClone(base), stage: "compare" as const, voucher: null };
    r.plan.spec.priorities = [];
    const sec = render(r).querySelector("[aria-label='Приоритеты']");
    expect(sec?.textContent).toContain("Порядок не задан");
    expect(sec?.querySelectorAll("button[aria-label^='Добавить']").length).toBe(6);
  });

  it("S9: на сборке подсказка честно говорит, что сдвиг вернёт к сравнению", () => {
    const hint = (stage: "compare" | "assemble") => {
      const r = { ...structuredClone(base), stage, voucher: null };
      r.plan.spec.priorities = ["price", "cancellation"];
      return render(r).querySelector("[aria-label='Приоритеты']")?.textContent ?? "";
    };
    expect(hint("assemble")).toContain("вернёт вас к сравнению");
    expect(hint("compare")).not.toContain("вернёт");
  });
});
