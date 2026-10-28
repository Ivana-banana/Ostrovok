import { renderToStaticMarkup } from "react-dom/server";
import { describe as suite, expect, it } from "vitest";
import fixture from "../../tests/fixtures/chat_reply.json";
import { Board, TripParams } from "./Board";
import type { ChatReply } from "./contracts";
import { changeValue, localTime } from "./format";

const base = fixture as unknown as ChatReply;
const noop = () => {};
// The fixture cart holds only the transfer; the hotel is outside it, so its fixes are hidden.
const hotelBought = structuredClone(base);
hotelBought.cart.items = [...base.cart.items, ...base.plan.offers.filter((o) => o.offer_id === "h1")];

function render(r: ChatReply): Document {
  // As in the chat (App, #434): the strip of parameters above, the block of the stage below.
  const html = renderToStaticMarkup(
    <>
      <TripParams spec={r.plan.spec} locked={false} onSend={noop} />
      <Board reply={r} busy={false} removed={[]} onSend={noop} onCart={noop} onCheckout={noop} />
    </>,
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
      expect(text).toContain("11.12 07:10 (UTC+3)");
      expect(text).toContain("прибытие 07:10 раньше заезда 14:00");
      expect(text).toContain("Ранний заезд"); // the fix offer is shown by its offer_id
    } finally {
      process.env.TZ = tz;
    }
  });

  it("#439: у отелей и направлений есть номера, у рейсов — зона времени", () => {
    const doc = render(base);
    const hotels = doc.querySelector("[aria-label='Сравнение отелей']");
    expect(hotels?.querySelector("th.num")?.textContent).toBe("№");
    expect(hotels?.querySelector("tbody td.no")?.textContent).toBe("1");
    const dest = doc.querySelector("[aria-label='Направления']");
    if (dest) expect(dest.querySelector(".dest-no")?.textContent).toBe("1. ");
    expect(doc.body.textContent).toMatch(/\(UTC[+-]\d/);
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

  it("#348: питомец и возраст детей — в «Гости» ваучера и в корзине", () => {
    const r = structuredClone(base);
    r.plan.spec = { ...r.plan.spec, adults: 2, children_ages: [1, 2, 5], pets: true };
    r.voucher!.spec = { ...r.voucher!.spec, adults: 2, children_ages: [1, 2, 5], pets: true };
    const want = "2 взр., дети: 1 год, 2 года, 5 лет, с питомцем";
    const doc = render(r);
    expect(doc.querySelector("[aria-label='Ваучер']")?.textContent).toContain(want);
    expect(doc.querySelector("[aria-label='Корзина']")?.textContent).toContain(want);
  });

  it("#367: ваучер показывает гостя отдельной строкой или честную пометку", () => {
    const r = structuredClone(base);
    r.voucher = { ...r.voucher!, guest: { name: "Иван Петров", contact: "ivan@example.com" } };
    const line = render(r).querySelector("[aria-label='Ваучер'] .guest-line")?.textContent;
    expect(line).toBe("Гость: Иван Петров, ivan@example.com");
    r.voucher = { ...r.voucher!, guest: { name: "Иван Петров", contact: "" } };
    expect(render(r).querySelector(".guest-line")?.textContent).toBe("Гость: Иван Петров");
    r.voucher = { ...r.voucher!, guest: null };
    expect(render(r).querySelector(".guest-line")?.textContent).toBe(
      "Гость: имя не введено (имитация)",
    );
    r.voucher = { ...r.voucher!, simulated: false };
    expect(render(r).querySelector(".guest-line")?.textContent).toBe("Гость: имя не введено");
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

  it("#434: блок этапа — карточки этапа открыты, уже выбранное свёрнуто в «Варианты»", () => {
    const at = (stage: ChatReply["stage"]) => render({ ...structuredClone(base), stage, voucher: null });
    const folded = (doc: Document, label: string) =>
      doc.querySelector(`[aria-label='${label}']`)?.closest("details") ?? null;
    const compare = at("compare");
    expect(folded(compare, "Сравнение отелей")).toBeNull();
    expect(folded(compare, "Сравнение транспорта")).toBeNull();
    expect(folded(compare, "Направления")?.querySelector("summary")?.textContent).toContain("Варианты");
    const checkout = at("checkout");
    expect(folded(checkout, "Корзина")).toBeNull();
    expect(folded(checkout, "Таймлайн")).toBeNull();
    expect(folded(checkout, "Сравнение отелей")?.hasAttribute("open")).toBe(false);
    expect(folded(checkout, "Сравнение транспорта")?.hasAttribute("open")).toBe(false);
    const inspire = at("inspire");
    expect(folded(inspire, "Направления")).toBeNull();
  });

  it("#434: приоритеты, допущения и «Что изменилось» — в компактном блоке «Подробнее»", () => {
    const r = { ...structuredClone(base), stage: "compare" as const, voucher: null };
    const more = render(r).querySelector("details.more:not(.options)");
    expect(more?.querySelector("summary")?.textContent).toContain("важнее: цена → отмена");
    for (const label of ["Приоритеты", "Что изменилось"]) expect(more?.querySelector(`[aria-label='${label}']`)).not.toBeNull();
    // A replanned trip opens the block: the user sees what changed without a click.
    expect(more?.hasAttribute("open")).toBe(r.plan.changes.length > 0);
    r.plan.changes = [];
    expect(render(r).querySelector("details.more:not(.options)")?.hasAttribute("open")).toBe(false);
  });

  it("#450: главное действие этапа — последнее в блоке, после отелей и транспорта, с суммой и «синтетика»", () => {
    const order = (stage: ChatReply["stage"], mainLabel: string) => {
      const doc = render({ ...hotelBought, stage, voucher: null });
      const all = [...doc.querySelectorAll(".board > *")];
      const at = (label: string) => all.findIndex((e) => e.getAttribute("aria-label") === label);
      return { doc, all, at, main: all.findIndex((e) => e.textContent?.includes(mainLabel) && e.querySelector("button.buy, button.primary")) };
    };
    const a = order("assemble", "Положить в корзину");
    expect(a.at("Таймлайн")).toBeLessThan(a.at("Сравнение отелей"));
    expect(a.at("Сравнение отелей")).toBeLessThan(a.at("Сравнение транспорта"));
    expect(a.at("Сравнение транспорта")).toBeLessThan(a.main);
    expect(a.at("Корзина")).toBeLessThan(a.main);
    expect(a.main).toBe(a.all.length - ([...a.doc.querySelectorAll(".board > details")].length) - 1);
    const buy = a.doc.querySelector(".board > .next button.buy");
    expect(buy?.textContent).toContain("Положить в корзину · ");
    expect(buy?.querySelector(".price .synth")?.textContent).toBe("синтетика");
    const c = order("compare", "Беру этот план");
    expect(c.at("Сравнение транспорта")).toBeLessThan(c.main);
    expect(c.at("Таймлайн")).toBeLessThan(c.main);
    const o = order("checkout", "Подтвердить бронь");
    expect(o.at("Таймлайн")).toBeLessThan(o.at("Корзина"));
  });
});
