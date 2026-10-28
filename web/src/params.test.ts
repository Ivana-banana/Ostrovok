import { describe, expect, it } from "vitest";
import fixture from "../../tests/fixtures/chat_reply.json";
import type { ChatReply, TripSpec } from "./contracts";
import {
  budgetMessage,
  datesMessage,
  otherAssumptions,
  paramChips,
  partyMessage,
  partyText,
} from "./params";

const spec = (fixture as unknown as ChatReply).plan.spec;
const with_ = (over: Partial<TripSpec>): TripSpec => ({ ...spec, ...over });

describe("чипы параметров", () => {
  it("значение, которое выбрал агент, помечено; слова пользователя — нет", () => {
    const chips = paramChips(
      with_({
        assumptions: ["откуда: Москва (город вылета не назван)", "длительность: 7 ночей (не названа)"],
      }),
    );
    const by = Object.fromEntries(chips.map((c) => [c.id, c.assumed]));
    expect(by).toEqual({ origin: true, dates: true, party: false, budget: false });
  });

  it("заметки о словах пользователя не делают чип «по умолчанию»", () => {
    const notes = [
      "даты: сдвинуты на 2 дн. позже по вашей просьбе",
      "даты: 05.10 уже прошло — взят 2027 год",
      "взрослых: 3 (вы и двое друзей)",
    ];
    const s = with_({ assumptions: notes });
    const by = Object.fromEntries(paramChips(s).map((c) => [c.id, c.assumed]));
    expect(by).toEqual({ origin: false, dates: false, party: false, budget: false });
    expect(otherAssumptions(s)).toEqual(notes);
  });

  it("#451: служебное «уже показаны» не показывается среди допущений", () => {
    const s = with_({ assumptions: ["уже показаны: Сочи, Анапа", "маршрут: Сочи потерян"] });
    expect(otherAssumptions(s)).toEqual(["маршрут: Сочи потерян"]);
  });

  it("состав «не назван» помечен", () => {
    const s = with_({ assumptions: ["взрослых: 2 (состав не назван)"] });
    expect(paramChips(s).find((c) => c.id === "party")?.assumed).toBe(true);
  });

  it("допущение без чипа остаётся в списке допущений", () => {
    const s = with_({ assumptions: ["бюджет: не задан", "маршрут: Сочи потерян"] });
    expect(otherAssumptions(s)).toEqual(["маршрут: Сочи потерян"]);
  });

  it("состав: склонение и один год в конце, как читает разбор запроса", () => {
    expect(partyText(1, [])).toBe("1 взрослый");
    expect(partyText(2, [])).toBe("2 взрослых");
    expect(partyText(3, [9, 5])).toBe("3 взрослых, дети 5 и 9 лет");
    expect(partyText(2, [1])).toBe("2 взрослых, ребёнок 1 год");
    expect(partyText(2, [12, 2, 5])).toBe("2 взрослых, дети 2, 5 и 12 лет");
  });
});

describe("сообщения правок", () => {
  it("даты — «с 12 декабря по 14 декабря»", () => {
    expect(datesMessage("2026-12-12", "2026-12-14")).toBe("с 12 декабря по 14 декабря");
    expect(datesMessage("2026-11-07", "2026-11-14")).toBe("с 7 ноября по 14 ноября");
  });

  it("состав без детей говорит «без детей», чтобы снять прежних", () => {
    expect(partyMessage(2, [])).toBe("Едем 2 взрослых, без детей");
    expect(partyMessage(2, [5])).toBe("Едем 2 взрослых, ребёнок 5 лет");
  });

  it("бюджет называет единицу — всю поездку", () => {
    expect(budgetMessage(100000)).toBe("Бюджет до 100000 ₽ на всю поездку");
  });
});
