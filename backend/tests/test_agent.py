"""Stage state machine (T2, #6): one case per rule."""

import pytest
from fastapi.testclient import TestClient

from api import catalog
from api.contracts import ChatRequest
from api.extract import extract
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
    assert any(c.what == "бюджет" for c in r.plan.changes)


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


def _book(store, r):
    for text in ("беру", "да", "подтверждаю"):
        r = say(store, text, r.session_id)
    return r


def test_trap_4_origin_off_the_hub_list_flies_from_the_nearest_hub(store):
    """TRAP-4 (#104): Суздаль has no airport: the plan flies from the nearest hub and says so."""
    spec = say(store, "Из Суздаля в Сочи в ноябре на 5 ночей").plan.spec
    assert spec.origin != "Суздаль" and catalog.resolve(spec.origin)["hub"]
    assert any(a.startswith("откуда:") and "Суздаль" in a and spec.origin in a
               for a in spec.assumptions)  # fmt: skip


def test_trap_4_unknown_origin_offline_is_moscow_with_a_note(store):
    """TRAP-4, TRAP-6 (#104): Тихвин is not in the catalogue and there is no network."""
    spec = say(store, "Из Тихвина в Сочи в ноябре").plan.spec
    assert spec.origin == "Москва"
    assert any(a.startswith("откуда:") and "не нашёл" in a for a in spec.assumptions)


def test_origin_city_is_never_offered_as_destination(store):
    # #94: «недорого на выходные из Казани»: Kazan is the origin, not a destination
    assert extract("недорого на выходные из Казани")[0].get("destinations") is None
    r = say(store, "недорого на выходные из Казани")
    assert r.stage == "inspire" and r.plan.spec.origin == "Казань"
    assert r.plan.destinations and "Казань" not in [d.name for d in r.plan.destinations]
    r = say(store, "1", r.session_id)  # the first offered destination
    assert r.plan.spec.destinations != ["Казань"]
    booked = _book(store, r)
    assert booked.stage == "booked"
    legs = [i for i in booked.voucher.items if i.kind == "transport"]
    assert legs and legs[0].origin == "Казань" and legs[-1].destination == "Казань"


def test_destination_equal_to_origin_is_said_honestly_before_confirmation(store):
    r = say(store, "недорого на выходные из Казани")
    r = say(store, "Казань", r.session_id)
    assert r.stage == "compare" and r.plan.spec.destinations == ["Казань"]
    assert "перевозки в плане нет" in r.reply  # no invented transport (TRAP-4)
    assert not [i for i in r.plan.selected if i.startswith("t-")]


def test_other_destination_keeps_two_or_three_options_without_the_origin(store):
    r = say(store, "на море в ноябре из Сочи")
    for text in ("другое направление", "другие варианты"):
        r = say(store, text, r.session_id)
        names = [d.name for d in r.plan.destinations]
        assert 2 <= len(names) <= 3 and "Сочи" not in names
        assert "Сочи" not in r.questions[0].options


def test_inspire_keeps_four_options_without_the_origin(store):
    r = say(store, "недорого на выходные из Казани")
    names = [d.name for d in r.plan.destinations]
    assert len(names) == 4 and "Казань" not in names


def test_select_exit_does_not_offer_the_origin(store):
    r = say(store, "Хиврии, 3 ночи с 20 ноября")
    r = say(store, "летим из Казани", r.session_id)
    options = r.questions[0].options
    assert 2 <= len(options) <= 3 and "Казань" not in options
