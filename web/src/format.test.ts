import { describe as suite, expect, it } from "vitest";
import type { ChatReply } from "./contracts";
import { describe, movePriority, priorityMessage } from "./format";

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
