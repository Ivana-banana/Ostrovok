import { describe as suite, expect, it } from "vitest";
import type { ChatReply } from "./contracts";
import { describe } from "./format";

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
