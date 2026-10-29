"""#630: at most 5 questions before the first plan, in the order откуда → когда → ночи → кто →
бюджет; independent questions go together in one turn. Rules only (TRAP-1)."""

import pytest
from talk import ask

from api.model import BUDGET_ASK, MAX_ASKS, MONTH_ASK, NIGHTS_ASK, ORIGIN_ASK, WHO_ASK
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
