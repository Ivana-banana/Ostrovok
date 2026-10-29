"""#630: at most 5 questions before the first plan, in the order откуда → когда → ночи → кто →
бюджет; independent questions go together in one turn. Rules only (TRAP-1)."""

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


def test_five_questions_at_most_then_the_plan(store):
    """All five slots are open: two turns, five questions, then the plan without more."""
    first = ask(store, "Хочу отдохнуть")
    assert first.questions[0].text.startswith(ORIGIN_ASK)
    assert texts(first)[1:] == [MONTH_ASK, NIGHTS_ASK]
    second = ask(store, "Из Москвы, декабрь, 7 ночей", first.session_id)
    assert texts(second) == [WHO_ASK, BUDGET_ASK]
    plan = ask(store, "Один взрослый, до 100 000 ₽", second.session_id)
    assert plan.stage != "clarify"
    assert len(first.questions) + len(second.questions) + len(plan.questions) == MAX_ASKS
    assert plan.plan.spec.adults == 1 and plan.plan.spec.budget_rub == 100_000


def test_independent_questions_go_in_one_turn(store):
    """The party and the budget are asked together; one answer to both gives the plan."""
    r = ask(store, "Сочи на 4 ночи в июле из Москвы")
    assert r.stage == "clarify" and texts(r) == [WHO_ASK, BUDGET_ASK]
    plan = ask(store, "Трое взрослых, до 200 000 ₽", r.session_id)
    assert plan.stage == "compare"
    assert plan.plan.spec.adults == 3 and plan.plan.spec.budget_rub == 200_000


def test_skipped_party_question_is_not_asked_again(store):
    """«Не важно, решите сами» on «кто едет»: the cards come without the same question."""
    first = ask(store, "тёплое море в ноябре, недорого, с ребёнком")
    party = ask(store, "Из Москвы", first.session_id)
    assert PARTY_ASK in texts(party)
    cards = ask(store, SKIP_ASK, party.session_id)
    assert cards.stage == "inspire" and PARTY_ASK not in texts(cards)


def test_cap_cuts_the_party_turn(store):
    """A hotel without a city: 4 trip questions, so the second turn has 1 of 2 (5 in all)."""
    first = ask(store, "хочу отель")
    assert len(first.questions) == 4
    second = ask(store, "Из Москвы, Казань, декабрь, 3 ночи", first.session_id)
    assert texts(second) == [WHO_ASK]
    plan = ask(store, "Двое взрослых", second.session_id)
    assert plan.stage != "clarify"


def test_skipped_party_question_stays_skipped_on_the_comparison(store):
    """#660: «Не важно» on «кто едет», then the choice of a city: no second party question."""
    first = ask(store, "тёплое море в ноябре, недорого, с ребёнком")
    party = ask(store, "Из Москвы", first.session_id)
    cards = ask(store, SKIP_ASK, party.session_id)
    compare = ask(store, "1", cards.session_id)
    assert compare.stage == "compare" and PARTY_ASK not in texts(compare)


def test_party_answer_after_skip_drops_the_skip_note(store):
    """#660: «нас трое взрослых» after «Не важно»: the note of the skip goes with the guess."""
    first = ask(store, "тёплое море в ноябре, недорого, с ребёнком")
    party = ask(store, "Из Москвы", first.session_id)
    cards = ask(store, SKIP_ASK, party.session_id)
    r = ask(store, "1", cards.session_id)
    r = ask(store, "Нас трое взрослых и ребёнок", r.session_id)
    assert r.plan.spec.adults == 3
    assert not any("не важно" in a for a in r.plan.spec.assumptions)
