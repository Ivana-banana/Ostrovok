import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe as suite, expect, it } from "vitest";
import fixture from "../../tests/fixtures/chat_reply.json";
import { Board, EarlierChoices, Price, PriorityRow, TripParams } from "./Board";
import type { ChatReply, HotelOffer, Priority, TransportOffer } from "./contracts";
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

  it("TRAP-7: у цены реальных данных нет «синтетика», есть пометка источника (#547)", () => {
    const real = structuredClone(base);
    real.plan.offers = real.plan.offers.map((o) => ({ ...o, synthetic: false }));
    const doc = render(real);
    const row = [...doc.querySelectorAll("tr")].find((r) => r.textContent?.includes("Отель у Кремля"));
    expect(row?.querySelector(".synth:not(.synth-real)")).toBeNull();
    expect(row?.querySelector(".synth-real")?.textContent).toBe("101hotels");
  });

  it("TRAP-1: без модели плашки «без ИИ» нет", () => {
    expect(render({ ...base, llm: false }).body.textContent).not.toContain("без ИИ");
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

  it("сравнение транспорта: колонка «Питомцы» как у отелей (#475)", () => {
    const sec = (doc: Document) => doc.querySelector("[aria-label='Сравнение транспорта']");
    expect(sec(render(base))?.querySelector("thead")?.textContent).toContain("Питомцы");
    const rows = (s: typeof base) => [...(sec(render(s))?.querySelectorAll("tbody tr") ?? [])];
    expect(rows(base)[0].textContent).toContain("нет отметки");
    const offers = base.plan.offers.map((o) => (o.kind === "transport" ? { ...o, pets_allowed: true } : o));
    const pets = { ...base, plan: { ...base.plan, offers } };
    expect(rows(pets)[0].textContent).toContain("можно");
  });

  it("сравнение транспорта: у рейса отметка питомца — «уточнить у авиакомпании» (#575)", () => {
    const sec = (doc: Document) => doc.querySelector("[aria-label='Сравнение транспорта']");
    const rows = (s: typeof base) => [...(sec(render(s))?.querySelectorAll("tbody tr") ?? [])];
    const withMode = (mode: "flight" | "train") => {
      const offers = base.plan.offers.map((o) => (o.kind === "transport" ? { ...o, mode, pets_allowed: true } : o));
      return { ...base, plan: { ...base.plan, offers } };
    };
    const flight = rows(withMode("flight"))[0].textContent ?? "";
    expect(flight).toContain("уточнить у авиакомпании");
    expect(flight).not.toContain("можно");
    expect(rows(withMode("train"))[0].textContent).toContain("можно");
  });

  it("пересборка: «было → стало»", () => {
    const t = render(base).querySelector("[aria-label='Что изменилось']")?.textContent ?? "";
    expect(t).toContain("было 11–13 декабря → стало 13–15 декабря");
    expect(changeValue("None")).toBe("не задано");
    expect(changeValue("['Казань']")).toBe("Казань");
    expect(changeValue("2026-12-11")).toBe("11.12.2026");
  });

  it("ваучер: номер брони, названия позиций, итог и пометка об имитации", () => {
    const v = render(base).querySelector("[aria-label='Ваучер']")?.textContent ?? "";
    expect(v).toContain("OST-0001");
    expect(v).toContain("Ранний заезд");
    expect(v).toContain("2 500 ₽");
    expect(v).toContain("имитирован");
  });

  it("#549: внутренние offer_id не видны в тексте ваучера, таймлайна, корзины и вариантов", () => {
    const ids = { h1: "h-ayt-s0-ROn-20261107-7n-2ac5", t1: "t-spb-kut-flight-202702100830-2a", x1: "x-1cfd00a6119a" };
    let json = JSON.stringify(hotelBought);
    for (const [short, long] of Object.entries(ids)) json = json.replaceAll(`"${short}"`, `"${long}"`);
    const r = JSON.parse(json) as typeof hotelBought;
    r.plan.issues[0].fixes.push("x-not-in-plan-0001"); // a fix outside the plan: no raw id either
    r.plan.bundles[0].offer_ids.push("x-not-in-plan-0002");
    const doc = render(r);
    for (const label of ["Ваучер", "Таймлайн", "Корзина"]) {
      expect(doc.querySelector(`[aria-label='${label}']`)).not.toBeNull();
    }
    const text = doc.body.textContent ?? "";
    for (const id of [...Object.values(ids), "x-not-in-plan"]) expect(text).not.toContain(id);
    expect(doc.querySelector("[aria-label='Ваучер']")?.textContent).toContain("Ранний заезд");
    expect(doc.querySelector(`[data-offer-id='${ids.x1}']`)).not.toBeNull(); // the id stays a data attribute
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

  it("TRAP-7 (#567): итог корзины из реальных и синтетических цен — «часть цен — синтетика»", () => {
    const r = structuredClone(hotelBought);
    r.stage = "checkout";
    r.voucher = null;
    r.cart.items = r.cart.items.map((o) => (o.kind === "hotel" ? { ...o, synthetic: false } : o));
    const cart = render(r).querySelector("[aria-label='Корзина']");
    expect(cart?.querySelector("[data-testid='cart-total'] .synth")?.textContent).toBe("часть цен — синтетика");
    expect(cart?.textContent).toContain("часть цен — синтетика · оплата имитируется");
    expect(cart?.textContent).not.toContain("все цены — синтетика");
    expect(cart?.textContent).toContain("(часть цен — синтетика) · оплата имитируется");
  });

  it("TRAP-7 (#567): смешанный вариант поездки — итог «часть цен», у каждой суммы в минусах своя пометка бэкенда", () => {
    const r = structuredClone(base);
    r.plan.offers = r.plan.offers.map((o) => (o.kind === "hotel" ? { ...o, synthetic: false } : o));
    r.plan.bundles = r.plan.bundles.map((b) => ({ ...b, tradeoffs: ["дороже на 2 000 ₽ (часть цен — синтетика) (отель, 9 000 ₽ (101hotels))"] }));
    const card = render(r).querySelector("[aria-label='Варианты поездки целиком'] .card");
    const marks = [...(card?.querySelectorAll(".price") ?? [])].map((p) => p.querySelector(".synth")?.textContent);
    expect(marks).toEqual(["часть цен — синтетика", "часть цен — синтетика", "101hotels"]);
  });

  it("#41: бюджет отеля — своя строка в параметрах, не «Бюджет» на поездку", () => {
    const r = structuredClone(base);
    r.plan.spec.budget_rub = null;
    r.plan.spec.hotel_budget_rub = 40000;
    const doc = render(r);
    // #431: the hotel budget is the chip of the hotel filters (per night), not a read-only row
    const chips = [...doc.querySelectorAll("[aria-label='Фильтры отелей'] button")].map((b) => (b.textContent ?? "").replace(/\s/g, " "));
    expect(chips.some((t) => t.startsWith("Бюджет отеля") && t.includes("20 000 ₽ за ночь"))).toBe(true);
    const rows = [...doc.querySelectorAll("[aria-label='Параметры поездки'] dl > div")].map((d) => d.textContent ?? "");
    expect(rows.some((t) => t.startsWith("Бюджет"))).toBe(false);
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
    expect(real?.querySelector(".synth:not(.synth-real)")).toBeNull();
  });

  it("TRAP-7: реальная цена получает пометку источника, синтетическая остаётся «синтетика» (#547)", () => {
    const r = structuredClone(hotelBought);
    r.stage = "compare";
    // a real flight is Авиасейлс; a real train would be tutu (#567)
    r.plan.offers = r.plan.offers.map((o) =>
      o.kind === "hotel" ? { ...o, synthetic: false } : o.kind === "transport" ? { ...o, mode: "flight", synthetic: false } : o,
    );
    const doc = render(r);
    const chips = (label: string) =>
      [...doc.querySelectorAll(`[aria-label='${label}'] td.cost .synth`)].map((c) => c.textContent);
    const hotelChip = doc.querySelector("[aria-label='Сравнение отелей'] td.cost .synth");
    expect(hotelChip?.textContent).toBe("101hotels");
    expect(hotelChip?.classList.contains("synth-real")).toBe(true);
    expect(hotelChip?.getAttribute("title")).toBe("итог по цене «от» 101hotels; питание и тариф — оценка");
    expect(chips("Сравнение транспорта")).toEqual(["Авиасейлс"]);
    // the transfer stays synthetic; its price keeps «синтетика»
    r.plan.offers = r.plan.offers.map((o) => ({ ...o, synthetic: o.kind !== "hotel" && o.kind !== "transport" }));
    const cart = render({ ...r, stage: "checkout", voucher: null }).querySelector("[aria-label='Корзина']");
    expect([...(cart?.querySelectorAll(".synth") ?? [])].every((c) => c.textContent === "синтетика")).toBe(true);
  });

  it("TRAP-7: реальный поезд tutu.ru — чип «tutu», не «Авиасейлс» (#569)", () => {
    const r = structuredClone(hotelBought);
    r.stage = "compare";
    r.plan.offers = r.plan.offers.map((o) =>
      o.kind === "transport" ? { ...o, offer_id: `${o.offer_id}-tutu`, synthetic: false } : o,
    );
    const chip = render(r).querySelector("[aria-label='Сравнение транспорта'] td.cost .synth");
    expect(chip?.textContent).toBe("tutu");
    expect(chip?.getAttribute("title")).toContain("tutu.ru");
  });

  it("TRAP-7: ваучер с реальным отелем называет источник у цены (#547)", () => {
    const r = structuredClone(base);
    r.voucher!.items = r.plan.offers.filter((o) => o.kind === "hotel").map((o) => ({ ...o, synthetic: false }));
    const voucher = render(r).querySelector("[aria-label='Ваучер']");
    const marks = [...(voucher?.querySelectorAll(".synth") ?? [])].map((c) => c.textContent);
    expect(marks).toContain("101hotels");
    expect(marks).not.toContain("синтетика");
  });

  it("направления: карточки с кнопкой выбора на этапе inspire", () => {
    const doc = render({ ...base, stage: "inspire", voucher: null });
    const card = doc.querySelector("[aria-label='Направления'] .card");
    expect(card?.textContent).toContain("Казань");
    expect(card?.querySelector("button")?.textContent).toBe("Выбрать");
  });

  it("#451: под карточками направлений — «Показать ещё», только на этапе inspire", () => {
    const more = (stage: ChatReply["stage"]) =>
      render({ ...base, stage, voucher: null }).querySelector("[aria-label='Направления'] > button.more-dests");
    expect(more("inspire")?.textContent).toBe("Показать ещё");
    expect(more("compare")).toBeNull();
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

  // #597: the trip as in the issue screenshot: the road there, the stay, the road back.
  function roundTrip(): ChatReply {
    const r = structuredClone(base);
    r.stage = "assemble";
    r.voucher = null;
    r.cart.items = [];
    r.plan.issues = [];
    r.plan.offers.push(
      { kind: "transfer", offer_id: "tr2", title: "Такси на вокзал", depart_at: "2026-12-13T15:25:00+03:00", arrive_at: "2026-12-13T15:45:00+03:00", price_rub: 600, synthetic: true, hotel_offer_id: "h1", leg_offer_id: "t2" },
      { kind: "transport", offer_id: "t2", title: "Поезд 001", mode: "train", origin: "Казань", destination: "Москва", depart_at: "2026-12-13T16:20:00+03:00", arrive_at: "2026-12-14T05:05:00+03:00", changes: 0, price_rub: 9800, synthetic: true, pets_allowed: false },
    );
    r.plan.timeline = [
      { at: "2026-12-10T19:30:00+03:00", title: "Отправление: Москва → Казань", offer_id: "t1" },
      { at: "2026-12-11T07:10:00+03:00", title: "Прибытие: Казань", offer_id: "t1" },
      { at: "2026-12-11T07:30:00+03:00", title: "Такси с вокзала", offer_id: "tr1" },
      { at: "2026-12-11T07:50:00+03:00", title: "Приезд в отель, ждём заселения: Отель у Кремля", offer_id: "h1" },
      { at: "2026-12-11T14:00:00+03:00", title: "Заселение: Отель у Кремля", offer_id: "h1" },
      { at: "2026-12-13T12:00:00+03:00", title: "Выезд из отеля: Отель у Кремля", offer_id: "h1" },
      { at: "2026-12-13T15:25:00+03:00", title: "Такси на вокзал", offer_id: "tr2" },
      { at: "2026-12-13T16:20:00+03:00", title: "Отправление: Казань → Москва", offer_id: "t2" },
      { at: "2026-12-14T05:05:00+03:00", title: "Прибытие: Москва", offer_id: "t2" },
    ];
    return r;
  }
  const legs = (r: ChatReply) => [...render(r).querySelectorAll("[aria-label='Таймлайн'] details.leg")];
  const head = (d: Element) => d.querySelector("summary")?.textContent ?? "";

  it("#597: стыковки свёрнуты в этапы — дорога туда, проживание, дорога обратно", () => {
    const [there, stay, back, ...rest] = legs(roundTrip());
    expect(rest).toHaveLength(0);
    expect(head(there)).toContain("Дорога туда");
    expect(head(there)).toContain("10.12 19:30 → 11.12 07:10 (UTC+3)");
    expect(head(there)).toContain("Москва → Казань, поезд, 11 ч 40 мин");
    expect(head(stay)).toContain("Проживание");
    expect(head(stay)).toContain("11.12 07:30 → 13.12 15:25 (UTC+3)");
    expect(head(stay)).toContain("Отель у Кремля, 2 ночи");
    expect(stay.querySelectorAll(".timeline li")).toHaveLength(5);
    expect(head(back)).toContain("Дорога обратно");
    expect(head(back)).toContain("Казань → Москва");
    for (const d of [there, stay, back]) expect(d.hasAttribute("open")).toBe(false);
  });

  it("#597: этап с замечанием открыт и помечен, остальные свёрнуты", () => {
    const r = roundTrip();
    r.plan.issues = [{ severity: "warn", text: "прибытие 07:10 раньше заезда 14:00", fixes: ["x1"] }];
    const [there, stay, back] = legs(r);
    expect(stay.hasAttribute("open")).toBe(true);
    expect(head(stay)).toContain("есть замечание");
    expect(there.hasAttribute("open")).toBe(false);
    expect(back.hasAttribute("open")).toBe(false);
    // the issue itself stays visible under the stages
    expect(render(r).querySelector("[aria-label='Таймлайн'] .issue")?.textContent).toContain("раньше заезда");
  });

  it("#597: замена рейса открывает свою дорогу; ошибка без связи открывает все этапы", () => {
    const r = roundTrip();
    r.plan.offers.push({ ...(r.plan.offers.find((o) => o.offer_id === "t2") as TransportOffer), offer_id: "t3" });
    r.plan.issues = [{ severity: "warn", text: "поздний поезд", fixes: ["t3"] }];
    expect(legs(r).map((d) => d.hasAttribute("open"))).toEqual([false, false, true]);
    r.plan.issues = [{ severity: "error", text: "Обратный рейс отправляется раньше прибытия туда.", fixes: [] }];
    expect(legs(r).map((d) => d.hasAttribute("open"))).toEqual([true, true, true]);
  });

  it("#597: два отеля — два этапа проживания и переезд между ними", () => {
    const r = roundTrip();
    const h2 = { ...(r.plan.offers.find((o) => o.offer_id === "h1") as HotelOffer), offer_id: "h2", title: "Отель на Волге" };
    r.plan.offers.push(h2);
    r.plan.timeline.splice(
      7, 0,
      { at: "2026-12-13T13:00:00+03:00", title: "Отправление: Казань → Свияжск", offer_id: "t1" },
      { at: "2026-12-13T14:00:00+03:00", title: "Заселение: Отель на Волге", offer_id: "h2" },
    );
    const titles = legs(r).map((d) => d.querySelector("summary b")?.textContent);
    expect(titles).toEqual(["Дорога туда", "Проживание", "Переезд", "Проживание", "Дорога обратно"]);
  });

  it("#597: убранная позиция помечена в строке этапа", () => {
    const r = roundTrip();
    r.stage = "checkout";
    r.cart.items = r.plan.offers.filter((o) => ["t1", "t2", "h1", "tr2"].includes(o.offer_id)); // tr1 removed
    const [there, stay] = legs(r);
    expect(head(stay)).toContain("часть убрана из корзины");
    expect(head(there)).not.toContain("убран");
    r.cart.items = r.plan.offers.filter((o) => ["h1", "tr1", "tr2"].includes(o.offer_id)); // both roads removed
    expect(head(legs(r)[0])).toContain("убрано из корзины");
  });

  // Live DOM (clicks are needed); the box is removed by the next call.
  let box: HTMLDivElement | null = null;
  const row = (stage: "compare" | "assemble" | "booked", order: Priority[], onSend: (t: string) => void = noop) => {
    box?.remove();
    box = document.createElement("div");
    document.body.append(box);
    act(() => createRoot(box!).render(<PriorityRow order={order} stage={stage} busy={false} onSend={onSend} />));
    return box.querySelector<HTMLElement>("[aria-label='Приоритеты']");
  };
  const pressed = (sec: HTMLElement | null) =>
    [...(sec?.querySelectorAll("button[aria-pressed='true']") ?? [])].map((c) => c.textContent);

  it("#577: пустой порядок на сравнении — выбрана цена", () => {
    const sec = row("compare", []);
    expect(sec?.querySelector("h2")?.textContent).toBe("Что для вас важнее");
    expect(pressed(sec)).toEqual(["цена"]);
    expect(sec?.querySelectorAll("button").length).toBe(6);
  });

  it("#577: клик по приоритету делает его главным и шлёт полный порядок", () => {
    const sent: string[] = [];
    const sec = row("compare", ["price", "cancellation"], (t) => sent.push(t));
    expect(pressed(sec)).toEqual(["цена"]);
    const btn = (name: string) => [...sec!.querySelectorAll("button")].find((b) => b.textContent === name)!;
    act(() => btn("цена").click()); // already the main one: nothing
    expect(sent).toEqual([]);
    act(() => btn("отмена").click());
    expect(sent).toEqual(["Мои приоритеты по важности: отмена, цена."]);
  });

  it("#577: пустой порядок — клик шлёт только выбранный, и «цена» тоже", () => {
    const sent: string[] = [];
    const sec = row("compare", [], (t) => sent.push(t));
    const btn = (name: string) => [...sec!.querySelectorAll("button")].find((b) => b.textContent === name)!;
    act(() => btn("рейтинг").click());
    act(() => btn("цена").click());
    expect(sent).toEqual(["Мои приоритеты по важности: рейтинг.", "Мои приоритеты по важности: цена."]);
  });

  it("#577: после брони порядок виден, но не правится; пустой список скрыт", () => {
    const sec = row("booked", ["price", "cancellation"]);
    expect(pressed(sec)).toEqual(["цена"]);
    expect([...sec!.querySelectorAll("button")].every((b) => b.disabled)).toBe(true);
    expect(row("booked", [])).toBeNull();
  });

  it("S9: на сборке подсказка честно говорит, что смена вернёт к сравнению", () => {
    expect(row("assemble", ["price"])?.textContent).toContain("вернёт вас к сравнению");
    expect(row("compare", ["price"])?.textContent).not.toContain("вернёт");
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
    for (const label of ["Что изменилось"]) expect(more?.querySelector(`[aria-label='${label}']`)).not.toBeNull();
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
    expect(o.at("Корзина")).toBe(o.all.findIndex((e) => e.textContent?.includes("Подтвердить бронь")));
    expect(o.main).toBe(o.at("Корзина"));
  });

  it("#556: на сравнении без отеля в плане «Беру этот план» недоступна, подсказка — по фактам плана", () => {
    const r = { ...structuredClone(base), stage: "compare" as const, voucher: null };
    const buy = (x: ChatReply) => render(x).querySelector(".board > .next button.buy") as HTMLButtonElement;
    const hint = (x: ChatReply) => render(x).querySelector(".no-hotel")?.textContent;
    expect(buy(r).disabled).toBe(false);
    expect(hint(r)).toBeUndefined();
    r.plan.selected = r.plan.selected.filter((id) => id !== "h1");
    // no pet, hotels in the list, none picked
    r.plan.spec.pets = false;
    expect(buy(r).disabled).toBe(true);
    expect(buy(r).textContent).toBe("Беру этот план");
    expect(buy(r).getAttribute("title")).toBe(hint(r));
    expect(hint(r)).toBe("Выберите отель из списка");
    // pet, the hotels are filtered out
    r.plan.spec.pets = true;
    expect(hint(r)).toContain("Без питомца");
    // no pet, the hotel source gave no offer
    const dry = structuredClone(r);
    dry.plan.spec.pets = false;
    dry.plan.offers = dry.plan.offers.filter((o) => o.kind !== "hotel");
    expect(buy(dry).disabled).toBe(true);
    expect(hint(dry)).toContain("источник отелей не ответил");
    expect(hint(dry)).not.toContain("питомц");
    expect(buy(dry).getAttribute("title")).not.toContain("питомц");
    // another stage is not touched: «Положить в корзину» stays live
    expect((render({ ...r, stage: "assemble" }).querySelector(".board > .next button.buy") as HTMLButtonElement).disabled).toBe(false);
  });

  it("#450: варианты прошлого ответа остаются под ним, кнопки выбора живые", () => {
    const r = { ...structuredClone(base), stage: "inspire" as const, voucher: null };
    const doc = new DOMParser().parseFromString(
      renderToStaticMarkup(<EarlierChoices reply={r} current={r} busy={false} onSend={noop} />),
      "text/html",
    );
    const d = doc.querySelector("details.earlier");
    expect(d?.hasAttribute("open")).toBe(true) // #458: destination batches stay open;
    expect(d?.querySelectorAll("button[aria-label^='Выбрать направление']").length).toBe(r.plan.destinations.length);
    // «ещё» is understood only in the current block: an older block has no such button
    expect(doc.querySelector("button.more-dests")).toBeNull();
    const booked = renderToStaticMarkup(<EarlierChoices reply={{ ...r, stage: "booked" }} current={r} busy={false} onSend={noop} />);
    expect(booked).toBe("");
  });

  it("#450: кнопки старого блока живы только при том же этапе и тех же строках; иначе подпись и ни одной кнопки", () => {
    const old = { ...structuredClone(base), stage: "compare" as const, voucher: null };
    old.plan.selected = []; // nothing chosen yet: every row has its pick button
    const html = (cur: ChatReply, sent: string[] = []) =>
      new DOMParser().parseFromString(
        renderToStaticMarkup(<EarlierChoices reply={old} current={cur} busy={false} onSend={(t) => sent.push(t)} />),
        "text/html",
      );
    const picks = (d: Document) => d.querySelectorAll("details.earlier button").length;
    // the same stage and rows: the buttons are live
    expect(picks(html(old))).toBeGreaterThan(0);
    expect(html(old).querySelector("details.earlier .muted")?.textContent ?? "").not.toContain("План изменился");
    // the current stage is assemble: nothing to press, the caption sends the user to the current block
    const now = html({ ...structuredClone(old), stage: "assemble" });
    expect(picks(now)).toBe(0);
    expect(now.querySelector("details.earlier")?.textContent).toContain("План изменился — выберите в текущем блоке");
    // the same stage, other rows
    const other = structuredClone(old);
    other.plan.offers = [...other.plan.offers].reverse();
    expect(picks(html(other))).toBe(0);
  });

  it("TRAP-5: зона (UTC±N) стоит внутри строки транспорта, после города", () => {
    const doc = render({ ...structuredClone(base), stage: "compare", voucher: null });
    const rows = [...doc.querySelectorAll("[aria-label='Сравнение транспорта'] tbody tr")];
    expect(rows.length).toBeGreaterThan(0);
    for (const tr of rows) {
      const ends = [...tr.querySelectorAll("td[data-label='Отправление'], td[data-label='Прибытие']")];
      expect(ends).toHaveLength(2);
      for (const td of ends) expect(td.querySelector("small + .zone")?.textContent).toMatch(/\(UTC[+-]\d/);
    }
  });

  it("#450/#458: старый блок направлений жив, пока выбор места идёт; после выбора — подпись и 0 кнопок", () => {
    const old = { ...structuredClone(base), stage: "inspire" as const, voucher: null };
    const cur = { ...structuredClone(old), stage: "compare" as const };
    const d = new DOMParser().parseFromString(
      renderToStaticMarkup(<EarlierChoices reply={old} current={cur} busy={false} onSend={noop} />),
      "text/html",
    );
    expect(d.querySelectorAll("details.earlier button").length).toBe(0);
    expect(d.querySelector("details.earlier")?.textContent).toContain("План изменился — выберите в текущем блоке");
  });

  it("#450: клик по живой кнопке старого блока шлёт текст строки, которая в текущем плане та же", async () => {
    const old = { ...structuredClone(base), stage: "inspire" as const, voucher: null };
    const sent: string[] = [];
    const box = document.createElement("div");
    document.body.append(box);
    const root = createRoot(box);
    await act(async () =>
      root.render(<EarlierChoices reply={old} current={structuredClone(old)} busy={false} onSend={(t) => sent.push(t)} />),
    );
    const btn = box.querySelectorAll<HTMLButtonElement>("details.earlier button[aria-label^='Выбрать направление']")[0];
    await act(async () => btn.click());
    // #458: the name only, no number
    expect(sent).toEqual([old.plan.destinations[0].name]);
    act(() => root.unmount());
    box.remove();
  });
  it("#458: «Ранее предлагал» — имена прошлых пачек кнопками; клик шлёт имя; старый блок открыт и жив", async () => {
    const cur = { ...structuredClone(base), stage: "inspire" as const, voucher: null };
    cur.plan.spec.assumptions = [...cur.plan.spec.assumptions, "уже показаны: Кировск, Губаха, " + cur.plan.destinations[0].name];
    const sent: string[] = [];
    const box = document.createElement("div");
    document.body.append(box);
    const root = createRoot(box);
    await act(async () => root.render(<Board reply={cur} busy={false} removed={[]} onSend={(t) => sent.push(t)} onCart={noop} onCheckout={noop} />));
    const line = box.querySelector(".earlier-names");
    expect(line?.textContent).toContain("Ранее предлагал");
    expect([...line!.querySelectorAll("button")].map((b) => b.textContent)).toEqual(["Кировск", "Губаха"]);
    await act(async () => line!.querySelectorAll("button")[1].click());
    expect(sent).toEqual(["Губаха"]);
    // an old batch stays open and live after the list changed
    const old = structuredClone(cur);
    cur.plan.destinations = cur.plan.destinations.map((x) => ({ ...x, name: `${x.name} (другой)` }));
    const html = new DOMParser().parseFromString(
      renderToStaticMarkup(<EarlierChoices reply={old} current={cur} busy={false} onSend={noop} />),
      "text/html",
    );
    expect(html.querySelector("details.earlier")?.hasAttribute("open")).toBe(true);
    expect(html.querySelectorAll("details.earlier button[aria-label^='Выбрать направление']").length).toBe(old.plan.destinations.length);
    act(() => root.unmount());
    box.remove();
  });
});

suite("#547: безопасный запасной чип", () => {
  it("TRAP-7: Price без synthetic и без источника показывает «реальные данные», не голое число", () => {
    const html = renderToStaticMarkup(<Price value={1000} synthetic={false} />);
    expect(html).toContain("реальные данные");
    expect(renderToStaticMarkup(<Price value={1000} />)).toContain("синтетика");
  });
});
