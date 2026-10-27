"""#168, #170: a length outside «на N ночей» is not lost and not replaced silently.

Rules only, no model (TRAP-1).
"""

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def length_notes(r):
    return [a for a in r.plan.spec.assumptions if a.startswith("длительность")]


def test_range_over_the_limit_is_named_and_not_taken(store):
    r = say(store, "Сочи с 1 ноября по 15 февраля, двое взрослых")
    assert r.plan.spec.nights == 7
    assert length_notes(r) == [
        "длительность: срок «1 ноября по 15 февраля» не принял — взято 7 ночей"
    ]
    assert r.plan.spec.date_from is not None
    assert (r.plan.spec.date_to - r.plan.spec.date_from).days == 7


def test_range_at_the_limit_is_taken(store):
    r = say(store, "Сочи с 1 ноября по 31 декабря, двое взрослых")  # 60 nights
    assert r.plan.spec.nights == 60
    assert length_notes(r) == []


def test_range_over_the_limit_on_an_edit_keeps_the_plan_length(store):
    first = say(store, "Сочи с 1 ноября по 6 ноября, двое взрослых")
    reply = say(store, "лучше с 1 ноября по 15 февраля", first.session_id)
    assert reply.plan.spec.nights == 5
    assert length_notes(reply) == [
        "длительность: срок «1 ноября по 15 февраля» не принял — взято 5 ночей"
    ]


def test_a_couple_of_months_is_60_nights_with_the_reading_named(store):
    r = say(store, "Сочи на пару месяцев, двое взрослых")
    assert r.plan.spec.nights == 60
    assert length_notes(r) == ["длительность: «на пару месяцев» — 60 ночей"]


@pytest.mark.parametrize(
    "text", ["Сочи на полтора месяца", "Сочи на 1.5 месяца", "Сочи на 1,5 месяца"]
)
def test_a_month_and_a_half_is_45_nights(store, text):
    r = say(store, text + ", двое взрослых")
    assert r.plan.spec.nights == 45
    assert len(length_notes(r)) == 1 and "45 ночей" in length_notes(r)[0]


@pytest.mark.parametrize(
    ("text", "said"), [("Сочи на полгода", "на полгода"), ("Сочи на пол года", "на пол года")]
)
def test_half_a_year_is_named_and_not_taken(store, text, said):
    r = say(store, text + ", двое взрослых")
    assert r.plan.spec.nights == 7
    assert length_notes(r) == [f"длительность: срок «{said}» не принял — взято 7 ночей"]


@pytest.mark.parametrize("stage_text", ["Сочи на 5 ночей в ноябре, двое взрослых"])
def test_a_request_for_a_long_stay_is_no_consent(store, stage_text):
    first = say(store, stage_text)
    assert first.stage == "compare"
    reply = say(store, "давай на 400 ночей", first.session_id)
    assert reply.stage == "compare"
    assert reply.plan.spec.nights == 5
    assert "не принял — взято 5 ночей" in length_notes(reply)[0]


def test_a_request_for_a_long_stay_is_no_consent_on_assemble(store):
    first = say(store, "Сочи на 5 ночей в ноябре, двое взрослых")
    second = say(store, "давай", first.session_id)
    assert second.stage == "assemble"
    reply = say(store, "давай на 400 ночей", second.session_id)
    assert reply.stage == "assemble" and not reply.cart.items


def test_plain_consent_still_moves_on(store):
    first = say(store, "Сочи на 5 ночей в ноябре, двое взрослых")
    assert say(store, "давай", first.session_id).stage == "assemble"


@pytest.mark.parametrize(
    "text",
    [
        "Сочи, бюджет 100 тысяч на месяц",
        "Сочи, 100 000 ₽ на месяц",
        "Сочи, бюджет 100 000 на месяц",
        "Сочи, не больше 100к на месяц",
    ],
)
def test_a_month_after_a_budget_sum_is_no_length(store, text):
    r = say(store, text + ", двое взрослых")
    assert r.plan.spec.nights == 7
    assert length_notes(r) == ["длительность: 7 ночей (не названа)"]


def test_a_month_before_a_budget_is_still_30_nights(store):
    r = say(store, "Сочи на месяц, бюджет 100 тысяч, двое взрослых")
    assert r.plan.spec.nights == 30
    assert length_notes(r) == ["длительность: «на месяц» — 30 ночей"]
