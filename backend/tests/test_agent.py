"""Stage state machine (T2, #6): one case per rule."""

import pytest
from fastapi.testclient import TestClient

from api.contracts import ChatRequest
from api.fixtures import extract
from api.main import app, get_store
from api.model import chat
from api.store import SessionStore

S1 = (
    "Хочу куда-нибудь к тёплому морю в ноябре, недорого, с ребёнком 5 лет, "
    "без долгих перелётов. Летим из Москвы."
)


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def test_s1_goes_ahead_with_stated_assumptions(store):
    r = say(store, S1)
    assert r.stage == "inspire" and r.questions == []
    assert r.plan.spec.assumptions and 2 <= len(r.plan.destinations) <= 4


def test_vague_request_asks_at_most_two_questions_once(store):
    r = say(store, "привет")
    assert r.stage == "clarify" and len(r.questions) == 2
    assert all(q.options for q in r.questions)
    again = say(store, "пока не знаю", r.session_id)
    assert again.stage != "clarify" and again.questions == []
    assert any(a.startswith("бюджет:") for a in again.plan.spec.assumptions)


def test_follow_up_updates_the_session_spec(store):
    first = say(store, S1)
    r = say(store, "Анталия", first.session_id)
    spec = r.plan.spec
    assert spec.destinations == ["Анталия"] and spec.month == 11 and spec.children_ages == [5]
    assert r.stage == "compare" and r.plan.offers


def test_stated_field_replaces_its_assumption(store):
    first = say(store, S1)
    r = say(store, "Анталия на 5 ночей", first.session_id)
    assert r.plan.spec.nights == 5
    assert all(not a.startswith("длительность:") for a in r.plan.spec.assumptions)
    assert (r.plan.spec.date_to - r.plan.spec.date_from).days == 5


def test_chat_reaches_booking_only_after_explicit_confirmation(store):
    r = say(store, SOCHI)
    assert r.stage == "compare"
    for text, stage in (("беру", "assemble"), ("да", "checkout")):
        r = say(store, text, r.session_id)
        assert r.stage == stage
    for text in ("не подтверждаю", "не  подтверждаю"):
        r = say(store, text, r.session_id)
        assert r.stage == "checkout" and r.voucher is None
    r = say(store, "подтверждаю", r.session_id)
    assert r.stage == "booked" and r.voucher.total_rub == r.cart.total_rub > 0


SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых, отель до 40 тысяч, нужен завтрак"


def test_impossible_date_is_skipped_with_an_assumption(store):
    r = say(store, "Сочи с 31 ноября, двое взрослых")
    assert r.stage == "compare" and r.plan.spec.date_from.month == 11
    assert any("31 ноября" in a for a in r.plan.spec.assumptions)


def test_values_below_one_are_skipped_with_an_assumption(store):
    r = say(store, "Сочи на 0 ночей с 20 ноября, 0 взрослых")
    spec = r.plan.spec
    assert r.stage == "compare" and spec.nights >= 1 and spec.adults >= 1
    assert any("0 ночей" in a for a in spec.assumptions)
    assert any("0 взрослых" in a for a in spec.assumptions)


def test_unknown_city_is_kept_and_named_honestly(store):
    # Istanbul is covered by the providers (T5, #9); Zurbagan is a fictional city.
    r = say(store, "Хочу в Зурбаган в ноябре")
    assert r.plan.spec.destinations == ["Зурбаган"]
    assert r.stage == "select" and "каталог" in r.reply


def test_spec_edit_after_offers_replans_and_resets_the_cart(store):
    r = say(store, SOCHI)
    for text in ("беру", "да"):
        r = say(store, text, r.session_id)
    assert r.stage == "checkout" and r.cart.items
    r = say(store, "а бюджет 30 000 руб", r.session_id)
    assert r.stage == "compare" and r.cart.items == []
    assert any(c.what == "budget_rub" for c in r.plan.changes)


@pytest.mark.parametrize("text", ["да нет, дорого", "можно в четверг?"])
def test_negation_and_weekday_do_not_move_compare(store, text):
    r = say(store, SOCHI)
    assert say(store, text, r.session_id).stage == "compare"


def test_extractor_ignores_word_parts_and_years():
    found, _ = extract("показаны отели, сказано в 2026 году, едем с ребёнком")
    assert "destinations" not in found and found["children_ages"] == [7]


def test_session_survives_a_new_store_on_the_same_file(tmp_path):
    path = tmp_path / "sessions.db"
    r = say(SessionStore(path), S1)
    assert SessionStore(path).get(r.session_id) == r


def test_unknown_session_is_404(store):
    app.dependency_overrides[get_store] = lambda: store
    try:
        client = TestClient(app)
        assert client.get("/api/session/nope").status_code == 404
        sid = client.post("/api/chat", json={"message": S1}).json()["session_id"]
        assert client.get(f"/api/session/{sid}").json()["stage"] == "inspire"
    finally:
        app.dependency_overrides.clear()
