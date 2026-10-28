import { expect, it } from "vitest";
import fixture from "../../tests/fixtures/chat_reply.json";
import type { ChatReply } from "./contracts";

// Typed copy of tests/fixtures/chat_reply.json: tsc rejects a missing or unknown field, the test
// rejects a value that differs from the fixture that backend/tests/test_api.py round-trips.
const sample: ChatReply = {
  "session_id": "s1",
  "stage": "booked",
  "reply": "Бронь оформлена.",
  "questions": [{ "text": "Когда едете?", "options": ["ноябрь", "декабрь"] }],
  "plan": {
    "spec": {
      "origin": "Москва",
      "destinations": ["Казань"],
      "date_from": "2026-12-11",
      "date_to": "2026-12-13",
      "month": null,
      "nights": 2,
      "adults": 2,
      "children_ages": [],
      "pets": false,
      "budget_rub": 50000,
      "hotel_budget_rub": 40000,
      "meal": "BB",
      "refundable": true,
      "modes": ["train"],
      "max_travel_hours": null,
      "own_transport": false,
      "arrive_at": null,
      "leave_at": null,
      "wishes": ["центр"],
      "priorities": ["price", "cancellation"],
      "assumptions": ["выезд из Москвы"]
    },
    "destinations": [
      {
        "name": "Казань",
        "country": "Россия",
        "lat": 55.79,
        "lon": 49.12,
        "why": "выходные недалеко",
        "est_total_rub": 42000,
        "synthetic": true,
        "air_c": -6.5,
        "water_c": null,
        "travel_hours": 12.0,
        "tradeoffs": ["ночь в поезде"],
        "violates": [],
        "risks": ["гололёд в декабре"]
      }
    ],
    "offers": [
      {
        "kind": "hotel",
        "offer_id": "h1",
        "title": "Отель у Кремля",
        "city": "Казань",
        "tz": "Europe/Moscow",
        "date_in": "2026-12-11",
        "date_out": "2026-12-13",
        "adults": 2,
        "children_ages": [],
        "price_rub": 19600,
        "synthetic": true,
        "stars": 4,
        "rating": 4.6,
        "meal": "BB",
        "cancellation": { "refundable": true, "free_until": "2026-12-09T23:59:00+03:00" },
        "distance_center_km": 0.8,
        "lat": 55.79,
        "lon": 49.11,
        "check_in": "14:00:00",
        "check_out": "12:00:00",
        "pets_allowed": false,
        "amenities": ["elevator"],
        "description": "У набережной."
      },
      {
        "kind": "transport",
        "offer_id": "t1",
        "title": "Поезд 002Й",
        "mode": "train",
        "origin": "Москва",
        "destination": "Казань",
        "depart_at": "2026-12-10T19:30:00+03:00",
        "arrive_at": "2026-12-11T07:10:00+03:00",
        "changes": 0,
        "price_rub": 9800,
        "synthetic": true,
        "pets_allowed": false
      },
      {
        "kind": "transfer",
        "offer_id": "tr1",
        "title": "Такси с вокзала",
        "depart_at": "2026-12-11T07:30:00+03:00",
        "arrive_at": "2026-12-11T07:50:00+03:00",
        "price_rub": 600,
        "synthetic": true,
        "hotel_offer_id": "h1",
        "leg_offer_id": "t1",
        "ref": null
      },
      {
        "kind": "extra",
        "offer_id": "x1",
        "title": "Ранний заезд",
        "for_offer_id": "h1",
        "price_rub": 2500,
        "synthetic": true,
        "tag": "early",
        "at": "10:00:00"
      }
    ],
    "bundles": [
      {
        "bundle_id": "b1",
        "label": "оптимум",
        "offer_ids": ["h1", "t1", "tr1"],
        "total_rub": 30000,
        "travel_hours": 12.0,
        "tradeoffs": ["ночь в поезде"]
      }
    ],
    "selected": ["h1", "t1"],
    "explanations": [
      { "offer_id": "h1", "why": "в центре, завтрак", "tradeoffs": ["дороже"], "risks": [] }
    ],
    "timeline": [
      { "at": "2026-12-11T07:10:00+03:00", "title": "Прибытие", "offer_id": "t1" }
    ],
    "issues": [
      { "severity": "warn", "text": "прибытие 07:10 раньше заезда 14:00", "fixes": ["x1"] }
    ],
    "changes": [{ "what": "даты", "before": "11–13 декабря", "after": "13–15 декабря" }]
  },
  "cart": {
    "items": [
      {
        "kind": "transfer",
        "offer_id": "tr1",
        "title": "Такси с вокзала",
        "depart_at": "2026-12-11T07:30:00+03:00",
        "arrive_at": "2026-12-11T07:50:00+03:00",
        "price_rub": 600,
        "synthetic": true,
        "hotel_offer_id": "h1",
        "leg_offer_id": "t1",
        "ref": null
      }
    ],
    "total_rub": 600
  },
  "voucher": {
    "booking_id": "OST-0001",
    "items": [
      {
        "kind": "extra",
        "offer_id": "x1",
        "title": "Ранний заезд",
        "for_offer_id": "h1",
        "price_rub": 2500,
        "synthetic": true,
        "tag": "early",
        "at": "10:00:00"
      }
    ],
    "spec": {
      "origin": null,
      "destinations": [],
      "date_from": null,
      "date_to": null,
      "month": 12,
      "nights": null,
      "adults": 2,
      "children_ages": [5],
      "pets": true,
      "budget_rub": null,
      "hotel_budget_rub": null,
      "meal": null,
      "refundable": null,
      "modes": [],
      "max_travel_hours": 5.0,
      "own_transport": false,
      "arrive_at": null,
      "leave_at": null,
      "wishes": [],
      "priorities": [],
      "assumptions": []
    },
    "total_rub": 2500,
    "created_at": "2026-12-01T10:00:00+03:00",
    "status": "confirmed",
    "simulated": true,
    "guest": { "name": "Анна Иванова", "contact": "+7 900 000-00-00" }
  },
  "llm": true
};

it("TS-зеркало контракта совпадает с фикстурой бэкенда", () => {
  expect(sample).toEqual(fixture);
});
