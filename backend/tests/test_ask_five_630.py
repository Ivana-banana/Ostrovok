"""#630, #666: every open slot is asked at once, in one turn, in the order откуда → когда → ночи →
кто → бюджет; there is no second question turn. Rules only (TRAP-1)."""

import pytest
from talk import ask

from api.model import (
    BUDGET_ASK,
    MAX_ASKS,
    MONTH_ASK,
    NIGHTS_ASK,
    ORIGIN_ASK,
    PARTY_ASK,
    SKIP_ASK,
    WHO_ASK,
)
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def texts(r):
    return [q.text for q in r.questions]


def test_all_open_slots_in_one_turn_then_the_plan(store):
    """All five slots are open: one turn asks all five, one answer gives the plan."""
    first = ask(store, "Хочу отдохнуть")
    assert first.questions[0].text.startswith(ORIGIN_ASK)
    assert texts(first)[1:] == [MONTH_ASK, NIGHTS_ASK, WHO_ASK, BUDGET_ASK]
    plan = ask(store, "Из Москвы, декабрь, 7 ночей. Один взрослый, до 100 000 ₽", first.session_id)
    assert plan.stage != "clarify"
    assert plan.plan.spec.adults == 1 and plan.plan.spec.budget_rub == 100_000


def test_independent_questions_go_in_one_turn(store):
    """The party and the budget are asked together; one answer to both gives the plan."""
    r = ask(store, "Сочи на 4 ночи в июле из Москвы")
    assert r.stage == "clarify" and texts(r) == [WHO_ASK, BUDGET_ASK]
    plan = ask(store, "Трое взрослых, до 200 000 ₽", r.session_id)
    assert plan.stage == "compare"
    assert plan.plan.spec.adults == 3 and plan.plan.spec.budget_rub == 200_000


def test_skipped_party_question_is_not_asked_again(store):
    """«Не важно, решите сами» on the first turn: the cards come without the party question."""
    first = ask(store, "тёплое море в ноябре, недорого, с ребёнком")
    assert first.questions[0].text.startswith(ORIGIN_ASK)
    asked = set(texts(first))
    cards = ask(store, SKIP_ASK, first.session_id)
    assert cards.stage == "inspire" and not asked & set(texts(cards))


def test_hotel_without_a_city_asks_every_slot_at_once(store):
    """A hotel without a city: origin, city, month, nights, party, budget in one turn."""
    first = ask(store, "хочу отель")
    assert len(first.questions) == MAX_ASKS
    plan = ask(store, "Из Москвы, Казань, декабрь, 3 ночи. Двое взрослых", first.session_id)
    assert plan.stage != "clarify"


def test_skipped_party_question_stays_skipped_on_the_comparison(store):
    """#660: «Не важно» on «кто едет», then the choice of a city: no second party question."""
    first = ask(store, "тёплое море в ноябре, недорого, с ребёнком")
    cards = ask(store, SKIP_ASK, first.session_id)  # #666: the party is in the first turn
    compare = ask(store, "1", cards.session_id)
    assert compare.stage == "compare" and PARTY_ASK not in texts(compare)


def test_party_answer_after_skip_drops_the_skip_note(store):
    """#660: «нас трое взрослых» after «Не важно»: the note of the skip goes with the guess."""
    first = ask(store, "тёплое море в ноябре, недорого, с ребёнком")
    cards = ask(store, SKIP_ASK, first.session_id)  # #666: the party is in the first turn
    r = ask(store, "1", cards.session_id)
    r = ask(store, "Нас трое взрослых и ребёнок", r.session_id)
    assert r.plan.spec.adults == 3
    assert not any("не важно" in a for a in r.plan.spec.assumptions)
