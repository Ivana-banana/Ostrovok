"""#311: «дешевле, чем в Турции?» at a built plan is a question. The spec stays: no new place,
no «недорого» wish, no re-plan. The answer comes from the plan; a price the data lacks is not
invented (TRAP-4)."""

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore

SOCHI = "Сочи из Москвы с 3 по 8 декабря, двое взрослых"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def at(store, stage):
    r = say(store, SOCHI)
    for text in ("беру", "да")[: {"compare": 0, "assemble": 1, "checkout": 2}[stage]]:
        r = say(store, text, r.session_id)
    assert r.stage == stage
    return r


@pytest.mark.parametrize("stage", ["compare", "assemble", "checkout"])
@pytest.mark.parametrize(
    "question",
    [
        "а дешевле, чем в Турции?",
        "дешевле, чем в Турции?",
        "это дешевле, чем в среднем?",
        "а дороже, чем в Египте?",
        "а в Турции дешевле?",  # #390: no «чем»
        "в Египте дороже?",
    ],
)
def test_price_versus_question_changes_nothing(store, stage, question):
    before = at(store, stage)
    after = say(store, question, before.session_id)
    assert after.stage == before.stage
    assert after.plan.spec == before.plan.spec
    assert "недорого" not in after.plan.spec.wishes
    assert after.plan.selected == before.plan.selected
    assert after.cart.total_rub == before.cart.total_rub
    assert not after.llm


def test_price_versus_answer_is_honest(store):
    before = at(store, "compare")
    total = sum(o.price_rub for o in before.plan.offers if o.offer_id in before.plan.selected)
    after = say(store, "а дешевле, чем в Турции?", before.session_id)
    assert "Турци" in after.reply
    assert "в данных нет" in after.reply
    assert f"{total:,} ₽".replace(",", " ") in after.reply  # the price of the plan itself
    assert "синтетика" in after.reply  # TRAP-7
    average = say(store, "это дешевле, чем в среднем?", before.session_id)
    assert "по рынку" in average.reply


@pytest.mark.parametrize("order", ["давай в Турцию", "давай в Турцию, там дешевле?"])
def test_price_versus_a_choice_of_the_place_still_moves_the_plan(store, order):
    """#390: «давай в Турцию» is a choice, not a question; the plan goes to Turkey."""
    before = at(store, "compare")
    after = say(store, order, before.session_id)
    assert after.plan.spec.destinations == ["Турция"]


BUDGET = ["можно уложиться дешевле, чем в 50 тысяч?", "а дешевле, чем 30 тысяч на человека?"]


@pytest.mark.parametrize("question", BUDGET)
def test_price_versus_budget_is_still_an_edit(store, question):
    before = at(store, "compare")
    after = say(store, question, before.session_id)
    assert after.plan.spec.budget_rub is not None
    assert after.plan.spec.budget_rub != before.plan.spec.budget_rub


@pytest.mark.parametrize(
    "request_",
    [
        "хочу дешевле, чем сейчас",
        "хочу дешевле, чем сейчас?",
        "сделай дешевле, чем в Турции",
        "а есть отель дешевле, чем этот?",
        "а можно дешевле, чем сейчас?",
        "а поезд дешевле, чем самолёт?",
    ],
)
def test_price_versus_request_is_not_the_answer(store, request_):
    before = at(store, "compare")
    after = say(store, request_, before.session_id)
    assert "по рынку" not in after.reply
    assert "цифр не выдумываю" not in after.reply
    assert "недорого" in after.plan.spec.wishes  # the old rules read it as a wish
