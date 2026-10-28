"""#436: the origin is asked first, not assumed silently as Москва.

No model (TRAP-1): the rules read the messages. The answer spellings are TRAP-3.
"""

import pytest

from api.contracts import ChatRequest
from api.model import ORIGIN_ASK, chat
from api.store import SessionStore

CHIPS = ["Из Москвы", "Из Санкт-Петербурга", "Из Екатеринбурга", "Из Новосибирска", "Из Казани"]
ORIGIN_NOTE = "откуда: Москва (город вылета не назван)"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def origin_q(r):
    return next((q for q in r.questions if q.text.startswith(ORIGIN_ASK)), None)


@pytest.mark.parametrize(
    "text",
    [
        "Казань на 3 ночи в декабре, двое, бюджет 50 тысяч",
        "Токио на 7 ночей в апреле, двое",
        "Тёплое море в ноябре, недорого, с ребёнком",
        "Хочу отдохнуть",
    ],
)
def test_unnamed_origin_is_the_first_question(store, text):
    """The bug: the plan came at once with «откуда: Москва» as an assumption."""
    r = say(store, text)
    assert r.stage == "clarify"
    assert r.questions[0].text.startswith(ORIGIN_ASK)
    assert r.questions[0].options[: len(CHIPS)] == CHIPS
    assert ORIGIN_NOTE not in r.plan.spec.assumptions
    assert len(r.questions) <= 2  # one turn, not an interrogation


@pytest.mark.parametrize(
    "answer, origin",
    [
        ("Из Санкт-Петербурга", "Санкт-Петербург"),
        ("из Москвы", "Москва"),
        ("Питер", "Санкт-Петербург"),
        ("мск", "Москва"),
        ("из мск", "Москва"),
        ("Масква", "Москва"),  # TRAP-3: a typo
        ("из Масквы", "Москва"),
        ("Новосибирк", "Новосибирск"),
        ("екб", "Екатеринбург"),
        ("Казань", "Казань"),
    ],
)
def test_answer_keeps_the_trip_and_asks_nothing_again(store, answer, origin):
    """TRAP-3: the spellings of the answer are normalised; the rest of the first message stays."""
    first = say(store, "Сочи на 4 ночи в июле, двое взрослых, бюджет 120 тысяч")
    assert origin_q(first)
    r = say(store, answer, first.session_id)
    assert r.stage == "compare" and r.questions == []
    s = r.plan.spec
    assert s.origin == origin and s.destinations == ["Сочи"]
    assert (s.nights, s.adults, s.budget_rub, s.month) == (4, 2, 120_000, 7)
    assert not any(a.startswith("откуда") for a in s.assumptions)


def test_origin_named_asks_nothing(store):
    r = say(store, "Сочи на 4 ночи в июле, двое взрослых, из Казани")
    assert r.stage == "compare" and not origin_q(r)


def test_own_tickets_ask_no_origin(store):
    """C5 (#408): own tickets need no departure city."""
    r = say(store, "Сочи с 14 ноября на 4 ночи, двое взрослых, билеты уже куплены")
    assert not origin_q(r) and r.stage != "clarify"


def test_origin_goes_first_next_to_the_children(store):
    """F25 class: the other question comes in the same turn, after the origin."""
    r = say(store, "Хотим с детьми в Таиланд на Новый год, бюджет 400 тысяч")
    assert [q.text.startswith(ORIGIN_ASK) for q in r.questions] == [True, False]
    assert "детей" in r.questions[1].text
    after = say(store, "Из Казани, двое детей 5 и 10 лет", r.session_id)
    assert after.plan.spec.origin == "Казань" and after.plan.spec.children_ages == [5, 10]
    assert after.stage != "clarify"


def test_vague_request_asks_origin_with_the_month(store):
    """Two questions at most: the origin and the month; the budget becomes an assumption."""
    r = say(store, "Хочу отдохнуть")
    assert [q.text for q in r.questions][1:] == ["Когда хотите поехать?"]
    after = say(store, "Питер, декабрь", r.session_id)
    assert after.stage == "inspire" and after.plan.spec.origin == "Санкт-Петербург"
    assert after.plan.spec.month == 12
    assert "Санкт-Петербург" not in after.plan.spec.destinations


def test_destination_answer_is_not_the_origin(store):
    """«в Сочи» answers no origin question: the place keeps its preposition."""
    r = say(store, "Хочу отдохнуть")
    after = say(store, "в Сочи", r.session_id)
    assert after.plan.spec.destinations == ["Сочи"]
    assert after.plan.spec.origin == "Москва" and ORIGIN_NOTE in after.plan.spec.assumptions


def test_unknown_answer_falls_back_aloud(store):
    """«не знаю» names no city: Москва stays an assumption the user sees, no second question."""
    first = say(store, "Сочи на 4 ночи в июле, двое взрослых")
    r = say(store, "не знаю", first.session_id)
    assert r.stage == "compare" and ORIGIN_NOTE in r.plan.spec.assumptions


def test_model_cannot_turn_the_origin_answer_into_a_destination(monkeypatch, store):
    """The model reads «Питер» as a place to go: the pending question makes it the origin."""
    from api import llm

    def stub(message, spec, until=None, today=None):
        return {"destinations": ["Санкт-Петербург"]} if message == "Питер" else {}

    monkeypatch.setattr(llm, "extract_spec", stub)
    first = say(store, "Сочи на 4 ночи в июле, двое взрослых")
    r = say(store, "Питер", first.session_id)
    assert r.plan.spec.origin == "Санкт-Петербург" and r.plan.spec.destinations == ["Сочи"]


def test_restart_keeps_the_named_origin(store):
    first = say(store, "Сочи на 4 ночи в июле, двое взрослых")
    r = say(store, "из Казани", first.session_id)
    r = say(store, "Начнём сначала: Анталия, 4 ночи, двое взрослых", r.session_id)
    assert r.stage == "compare" and r.plan.spec.origin == "Казань"
    assert not origin_q(r)


def test_city_off_the_catalogue_answers_by_the_geocoder(geo, monkeypatch, store):
    """TRAP-3, TRAP-6: «Орск» is no hub; the geocoder finds it, the nearest hub is said aloud."""
    from api import catalog

    monkeypatch.setattr(catalog, "_get", geo("Орск"))
    first = say(store, "Сочи на 4 ночи в июле, двое взрослых")
    r = say(store, "Орск", first.session_id)
    assert r.stage == "compare" and r.plan.spec.destinations == ["Сочи"]
    assert any(a.startswith("откуда") and "Орск" in a for a in r.plan.spec.assumptions)


def test_city_off_the_catalogue_offline_is_no_destination(store):
    """TRAP-6: no network, «Орск» is not found: the trip stays Сочи, Москва is said aloud."""
    first = say(store, "Сочи на 4 ночи в июле, двое взрослых")
    r = say(store, "Орск", first.session_id)
    assert r.plan.spec.destinations == ["Сочи"] and ORIGIN_NOTE in r.plan.spec.assumptions
