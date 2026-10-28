"""The reference of real dated offers and its comparison with the agent (#263).

eval/reference_offers.json holds real Booking/Agoda offers without names, ids and urls (the
decision on named raw data is open). scripts/compare_reference_offers.py only measures: these
tests check the data and the rows, not the prices of the agent.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = json.loads((ROOT / "eval" / "reference_offers.json").read_text("utf-8"))
CASES = {c["id"] for c in json.loads((ROOT / "eval" / "cases.json").read_text("utf-8"))}

_spec = importlib.util.spec_from_file_location(
    "compare_reference_offers", ROOT / "scripts" / "compare_reference_offers.py"
)
cmp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmp)

FIELDS = {
    "request", "city", "check_in", "check_out", "nights", "adults", "children", "stars",
    "night_price", "total_price", "currency", "meal", "free_cancel", "source", "collected_at",
}  # fmt: skip
FORBIDDEN = {"id", "name", "title", "url", "link", "photo", "photos", "image", "address", "hotel"}


def test_reference_size_and_cases():
    assert 20 <= len(REFERENCE["offers"]) <= 30
    assert 5 <= len(REFERENCE["requests"]) <= 8
    ids = {r["id"] for r in REFERENCE["requests"]}
    assert {o["request"] for o in REFERENCE["offers"]} <= ids
    assert {r["case"] for r in REFERENCE["requests"]} <= CASES


def test_reference_has_no_names_ids_or_urls():
    """Named raw data is not in git: only the fields of FIELDS (and the date of free cancel)."""
    for o in REFERENCE["offers"]:
        assert FIELDS <= set(o) <= FIELDS | {"free_cancel_until"}
        assert not FORBIDDEN & set(o)
    text = json.dumps(REFERENCE, ensure_ascii=False)
    assert "http" not in text and "www." not in text


def test_reference_offers_match_their_request():
    by_id = {r["id"]: r for r in REFERENCE["requests"]}
    for o in REFERENCE["offers"]:
        req = by_id[o["request"]]
        assert (o["city"], o["check_in"], o["check_out"], o["nights"]) == (
            req["city"], req["check_in"], req["check_out"], req["nights"],
        )  # fmt: skip
        assert o["total_price"] == pytest.approx(o["night_price"] * o["nights"], rel=0.01)
        assert o["currency"] == ("RUB" if o["source"] == "booking" else "EUR")


def test_row_is_the_night_price_against_the_market():
    """price_rub of a hotel is the whole stay: the row divides it by the nights."""
    market = {"min": 1000, "p25": 2000, "median": 3000, "p75": 4000, "max": 5000,
              "free_cancel_share": 0.5, "breakfast_share": 0.25}  # fmt: skip
    req = {"id": "R", "city": "X", "market": {"3": market}}

    class Cancel:
        refundable = True

    class Hotel:
        def __init__(self, price: int, meal: str):
            self.price_rub, self.meal, self.cancellation = price, meal, Cancel()

    row = cmp.row_of(req, 3, [Hotel(8000, "RO"), Hotel(16000, "BB")], market, 4, "★")
    assert (row["agent"], row["ratio"], row["in_range"]) == (
        3000,
        1.0,
        True,
    )  # median of 2000, 4000
    assert row["free_cancel"] and row["breakfast"]
    assert cmp.row_of(req, 3, [Hotel(40000, "RO")], market, 4, "★")["in_range"] is False
    assert cmp.row_of(req, 5, [], None, 4, "★")["agent"] is None


def test_agent_answers_a_reference_request_in_the_rules_mode(monkeypatch):
    """TRAP-1: no LLM key; TRAP-6: no network. The agent gives hotels for the city and dates."""
    for name in ("LLM_API_KEY", "GIGACHAT_AUTH_KEY"):
        monkeypatch.delenv(name, raising=False)
    req = REFERENCE["requests"][0]
    reply = cmp.ask_agent(req["message"])
    assert reply.llm is False
    rows = cmp.compare_request(req, reply)
    assert [r["stars"] for r in rows[:3]] == [3, 4, 5]
    assert all(r["agent"] and r["ref"] for r in rows[:3])
    assert str(reply.plan.spec.date_from) == req["check_in"]
