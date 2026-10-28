import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe as suite, expect, it } from "vitest";
import fixture from "../../tests/fixtures/chat_reply.json";
import { badges, optionsOf } from "./Alternatives";
import { AgentText } from "./Chat";
import type { ChatReply, HotelOffer, TransportOffer } from "./contracts";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const base = fixture as unknown as ChatReply;
const baseHotel = base.plan.offers.find((o) => o.kind === "hotel") as HotelOffer;
const baseLeg = base.plan.offers.find((o) => o.kind === "transport") as TransportOffer;

const hotel = (id: string, title: string, price: number, rating: number | null, km: number | null): HotelOffer => ({
  ...baseHotel,
  offer_id: id,
  title,
  city: "Казань",
  price_rub: price,
  rating,
  distance_center_km: km,
  synthetic: true,
});

// A leg there (Москва → Казань) or back; `hours` of travel.
const leg = (id: string, back: boolean, price: number, hours: number): TransportOffer => ({
  ...baseLeg,
  offer_id: id,
  title: `Рейс ${id}`,
  origin: back ? "Казань" : "Москва",
  destination: back ? "Москва" : "Казань",
  depart_at: "2026-12-11T08:00:00+03:00",
  arrive_at: `2026-12-11T${String(8 + hours).padStart(2, "0")}:00:00+03:00`,
  price_rub: price,
  synthetic: true,
});

const offers = [
  hotel("h1", "Казантель", 7900, 8.1, 2.6),
  hotel("h2", "Волга", 9900, 9.0, 0.4),
  hotel("h2b", "Волга", 11900, 9.0, 0.4), // a second tariff of the same hotel: one row
  hotel("h3", "Кремлёвская", 12500, 8.5, 1.1),
  leg("t1", false, 7800, 12),
  leg("t2", false, 9500, 2),
  leg("t3", true, 7800, 12),
  leg("t4", true, 8800, 2),
];
const reply: ChatReply = {
  ...structuredClone(base),
  stage: "compare",
  plan: { ...structuredClone(base.plan), offers, selected: ["h1", "t1", "t3"] },
};

suite("#489: другие варианты пункта", () => {
  it("«самый дешёвый» — у единственного варианта с наименьшей ценой", () => {
    expect(badges([offers[0], offers[1], offers[3]]).h1).toContain("cheap");
    expect(badges([offers[0], offers[1], offers[3]]).h2).not.toContain("cheap");
  });

  it("«самый быстрый» — у транспорта с наименьшим временем в пути (прибытие − отправление)", () => {
    expect(badges([offers[4], offers[5]])).toEqual({ t1: ["cheap"], t2: ["fast"] });
  });

  it("«лучший рейтинг» — у отеля с наибольшим рейтингом", () => {
    expect(badges([offers[0], offers[1], offers[3]]).h2).toContain("rating");
  });

  it("«ближе к центру» — у отеля с наименьшим расстоянием до центра", () => {
    expect(badges([offers[0], offers[1], offers[3]]).h2).toContain("center");
  });

  it("нет плашки: один вариант, равные значения или значение только у одного", () => {
    expect(badges([offers[0]])).toEqual({ h1: [] });
    const tie = [hotel("a", "А", 5000, 8, null), hotel("b", "Б", 5000, 8, 1)];
    expect(badges(tie)).toEqual({ a: [], b: [] });
  });

  it("варианты: отель — строка на отель, «беру N»; «Туда»/«Обратно» — по городу отеля, «рейс N» по всей таблице", () => {
    const pick = (label: string) => optionsOf(label, reply).map((r) => [r.option.offer_id, r.pick]);
    expect(pick("Отель")).toEqual([
      ["h1", "беру первый"],
      ["h2", "беру второй"],
      ["h3", "беру третий"],
    ]);
    expect(pick("Туда")).toEqual([
      ["t1", "возьми рейс 1"],
      ["t2", "возьми рейс 2"],
    ]);
    expect(pick("Обратно")).toEqual([
      ["t3", "возьми рейс 3"],
      ["t4", "возьми рейс 4"],
    ]);
    expect(pick("Трансферы")).toEqual([]);
    // the hotel table has no «Выбрать» after compare: no pick either
    expect(optionsOf("Отель", { ...reply, stage: "assemble" }).every((r) => r.pick === null)).toBe(true);
  });

  it("пункт — кнопка с aria-expanded: Enter раскрывает варианты, выбран помечен, выбор шлёт сообщение", async () => {
    const box = document.createElement("div");
    const root = createRoot(box);
    const sent: string[] = [];
    const text = [
      "Предлагаю:",
      "Отель: Казантель, Казань, 2★, без питания, невозвратный — 7 900 ₽",
      "Туда: поезд Москва → Казань, 10.12 18:50 → 11.12 07:10 (UTC+3) — 7 800 ₽",
      "Итого 15 700 ₽.",
    ].join("\n");
    const choice = { reply, busy: false, onSend: (t: string) => sent.push(t) };
    await act(async () => root.render(<AgentText text={text} synthetic choice={choice} />));
    const there = box.querySelectorAll<HTMLElement>("li.pt [role='button']")[1];
    expect(there.getAttribute("aria-expanded")).toBe("false");
    await act(async () => there.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
    expect(there.getAttribute("aria-expanded")).toBe("true");
    const rows = [...box.querySelectorAll(`[id="${there.getAttribute("aria-controls")}"] li.alt`)];
    expect(rows.map((r) => r.className)).toEqual(["alt chosen", "alt"]);
    expect(rows[0].querySelector(".alt-mark")?.textContent).toBe("выбран");
    // TRAP-7: every price of an option has the chip
    expect(rows.map((r) => r.querySelector(".price .synth")?.textContent)).toEqual(["синтетика", "синтетика"]);
    expect(rows[1].querySelector(".alt-badge.fast")?.textContent).toBe("самый быстрый");
    await act(async () => rows[1].querySelector<HTMLButtonElement>("button.alt-pick")!.click());
    expect(sent).toEqual(["возьми рейс 2"]);
    act(() => root.unmount());
  });
});
