"""#195: «без детей» says there are no children. It never adds two children of 7 years.

Without a model (TRAP-1). A wrong guest count is a wrong price and a wrong booking.
"""

import pytest

from api.contracts import ChatRequest
from api.extract import extract
from api.model import chat
from api.store import SessionStore

FIRST = "Сочи из Москвы с 3 по 8 декабря, двое взрослых"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


@pytest.mark.parametrize(
    "text",
    [
        "в январе, без детей",
        "без детей в январе",
        "едем без детей",
        "Сочи в ноябре, без маленьких детей",
        "Сочи в ноябре, без ребёнка",
    ],
)
def test_no_kids_extracts_zero_children(text):
    found, notes = extract(text)
    assert found.get("children_ages", []) == []
    assert not any("возраст ребёнка" in n for n in notes)


@pytest.mark.parametrize("text", ["в январе, без детей", "без детей в январе"])
def test_no_kids_keeps_the_party_and_the_total(store, text):
    first = say(store, FIRST)
    again = say(store, text, first.session_id)
    assert first.plan.spec.children_ages == []
    assert again.plan.spec.children_ages == []
    assert again.plan.spec.adults == 2
    assert not any("возраст ребёнка" in a for a in again.plan.spec.assumptions)


def test_no_kids_in_the_first_message(store):
    reply = say(store, "Сочи в ноябре, едем без детей")
    assert reply.plan.spec.children_ages == []


def test_no_kids_removes_the_children_of_the_plan(store):
    first = say(store, "Сочи из Москвы с 3 по 8 декабря, двое взрослых и дочка 5 лет")
    assert first.plan.spec.children_ages == [5]
    again = say(store, "без детей", first.session_id)
    assert again.plan.spec.children_ages == []
    assert again.plan.spec.adults == 2
    assert "состав: 2 взр. + дети 5 лет → 2 взр." in again.reply  # the usual «было → стало»


def test_with_kids_still_adds_children():
    found, _ = extract("Сочи в ноябре, с детьми")
    assert found["children_ages"] == [7, 7]
    found, _ = extract("Сочи в ноябре, с ребёнком 4 года")
    assert found["children_ages"] == [4]


def test_a_question_with_no_kids_changes_nothing():
    found, _ = extract("а без детей можно?", asking=True)
    assert "children_ages" not in found
