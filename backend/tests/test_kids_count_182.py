"""#182: «с детьми» without a number in the first message is no two children of 7.

The children are asked next to the options (like the adults, #112). The rules and the model
may not guess them (TRAP-1: no model here). A wrong guest count is a wrong price and booking.
"""

import pytest
from talk import ask, planned, say

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore

KIDS_ASK = "Сколько детей едет и сколько им лет?"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def kids_questions(reply):
    return [q for q in reply.questions if q.text == KIDS_ASK]


def test_inspire_keeps_the_options_after_the_skipped_children_question(store):
    reply = planned(store, "Тёплое море в ноябре, недорого, с детьми")  # #630: skipped
    assert reply.stage == "inspire"
    assert reply.plan.destinations  # the options are shown at once
    assert reply.plan.spec.children_ages == []
    assert not any("7 лет" in a for a in reply.plan.spec.assumptions)
    assert kids_questions(reply) == []  # #630: asked before the cards and skipped, not again


def test_a_named_place_asks_the_children_in_the_party_turn(store):
    """#630: the trip turn first (откуда, ночи), then the children."""
    first = ask(store, "Сочи, если можно с детьми, в ноябре")
    reply = ask(store, "Из Москвы, 7 ночей", first.session_id)
    assert reply.plan.spec.children_ages == []
    assert not any("7 лет" in a for a in reply.plan.spec.assumptions)
    assert len(kids_questions(reply)) == 1


def test_the_answer_sets_the_children(store):
    first = say(store, "Тёплое море в ноябре, недорого, с детьми")
    again = say(store, "Двое детей, 5 и 10 лет", first.session_id)
    assert again.plan.spec.children_ages == [5, 10]


def test_counted_children_are_not_asked(store):
    reply = say(store, "Сочи в ноябре, с ребёнком 4 года")
    assert reply.plan.spec.children_ages == [4]
    assert not kids_questions(reply)


@pytest.mark.parametrize("stage_turns", [1, 2, 3])
def test_a_question_about_kids_at_a_plan_asks_the_age(store, stage_turns):
    first = say(store, "Сочи из Москвы с 3 по 8 декабря, двое взрослых")
    sid = first.session_id
    if stage_turns >= 2:
        sid = say(store, "беру", sid).session_id  # assemble
    if stage_turns == 3:
        sid = say(store, "да", sid).session_id  # checkout
    reply = chat(ChatRequest(session_id=sid, message="Сочи, если можно с детьми"), store)
    assert reply.plan.spec.children_ages == []  # a question adds nobody
    assert len(kids_questions(reply)) == 1
    assert "напишите возраст" in reply.reply
    done = say(store, "Двое детей, 5 и 10 лет", sid)
    assert done.plan.spec.children_ages == [5, 10]
