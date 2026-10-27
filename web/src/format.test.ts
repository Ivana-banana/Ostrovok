import { describe as suite, expect, it } from "vitest";
import type { ChatReply } from "./contracts";
import { describe, fieldLabel, guestsText, movePriority, priorityMessage, yearsText } from "./format";

const reply = (llm: boolean): ChatReply => ({
  session_id: "s",
  stage: "clarify",
  reply: "e",
  questions: [],
  plan: {
    spec: {
      origin: null,
      destinations: [],
      date_from: null,
      date_to: null,
      month: null,
      nights: null,
      adults: 2,
      children_ages: [],
      pets: false,
      budget_rub: null,
      hotel_budget_rub: null,
      meal: null,
      refundable: null,
      modes: [],
      max_travel_hours: null,
      own_transport: false,
      arrive_at: null,
      leave_at: null,
      wishes: [],
      priorities: [],
      assumptions: [],
    },
    destinations: [],
    offers: [],
    bundles: [],
    selected: [],
    explanations: [],
    timeline: [],
    issues: [],
    changes: [],
  },
  cart: { items: [], total_rub: 0 },
  voucher: null,
  llm,
});

suite("describe", () => {
  it("помечает режим без модели", () => {
    expect(describe(reply(false))).toContain("без ИИ");
  });
  it("без пометки с моделью", () => {
    expect(describe(reply(true))).toBe("e");
  });
});

suite("приоритеты", () => {
  it("сообщение задаёт полный порядок, самый важный первым", () => {
    expect(priorityMessage(["cancellation", "price", "travel_time"])).toBe(
      "Мои приоритеты по важности: отмена, цена, время в пути.",
    );
  });

  it("сдвиг на одно место; за край не двигается", () => {
    expect(movePriority(["price", "meal", "rating"], 1, -1)).toEqual(["meal", "price", "rating"]);
    expect(movePriority(["price", "meal", "rating"], 1, 1)).toEqual(["price", "rating", "meal"]);
    expect(movePriority(["price", "meal"], 0, -1)).toBeNull();
    expect(movePriority(["price", "meal"], 1, 1)).toBeNull();
  });
});

suite("подписи полей", () => {
  it("#41: бюджет отеля и бюджет поездки — разные подписи", () => {
    expect(fieldLabel("hotel_budget_rub")).toBe("бюджет отеля");
    expect(fieldLabel("budget_rub")).toBe("бюджет");
  });
});

suite("гости в корзине и ваучере (#348)", () => {
  it("возраст ребёнка: год, года, лет", () => {
    const words = (ns: number[]) => ns.map(yearsText);
    expect(words([1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 0])).toEqual([
      "1 год", "2 года", "4 года", "5 лет", "11 лет", "12 лет", "14 лет", "21 год", "22 года", "25 лет", "до 1 года",
    ]);
  });
  it("питомец стоит в строке гостей", () => {
    expect(guestsText({ adults: 2, children_ages: [1, 2, 5], pets: true })).toBe(
      "2 взр., дети: 1 год, 2 года, 5 лет, с питомцем",
    );
    expect(guestsText({ adults: 1, children_ages: [], pets: true })).toBe("1 взр., с питомцем");
    expect(guestsText({ adults: 2, children_ages: [], pets: false })).toBe("2 взр.");
  });
});
