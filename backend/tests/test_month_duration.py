"""#160: «на месяц» is 30 nights with a named reading; a length the rules reject is named, not «не названа».

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


def test_month_is_30_nights_with_the_reading_named(store):
    r = say(store, "Сочи на месяц в ноябре, двое взрослых")
    assert r.plan.spec.nights == 30
    assert length_notes(r) == ["длительность: «на месяц» — 30 ночей"]


def test_two_months_is_over_the_limit_and_named(store):
    r = say(store, "Сочи на 3 месяца, двое взрослых")
    assert r.plan.spec.nights == 7
    assert length_notes(r) == ["длительность: срок «на 3 месяца» не принял — взято 7 ночей"]


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("Сочи на 400 ночей, двое взрослых", "на 400 ночей"),
        ("Сочи на 400 дней, двое взрослых", "на 400 дней"),
        ("Сочи на 99 ночей, двое взрослых", "99 ночей"),
    ],
)
def test_rejected_length_is_named_not_unnamed(store, text, said):
    r = say(store, text)
    assert r.plan.spec.nights == 7
    assert length_notes(r) == [f"длительность: срок «{said}» не принял — взято 7 ночей"]


def test_no_length_word_keeps_the_default_note(store):
    r = say(store, "Сочи в ноябре, двое взрослых")
    assert length_notes(r) == ["длительность: 7 ночей (не названа)"]


@pytest.mark.parametrize(
    ("text", "nights"),
    [("Сочи на неделю, двое взрослых", 7), ("Сочи на 3 недели, двое взрослых", 21)],
)
def test_weeks_stay_as_they_were(store, text, nights):
    assert say(store, text).plan.spec.nights == nights


def test_shift_by_a_month_is_no_length(store):
    first = say(store, "Сочи на 5 ночей в ноябре, двое взрослых")
    reply = say(store, "сдвинь на месяц позже", first.session_id)
    assert reply.plan.spec.nights == 5
