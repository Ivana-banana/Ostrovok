"""#311: «дешевле, чем в Турции?» at a built plan is a question. The spec stays: no new place,
no «недорого» wish, no re-plan. The answer comes from the plan; a price the data lacks is not
invented (TRAP-4)."""

import pytest
from talk import say

from api.store import SessionStore

SOCHI = "Сочи из Москвы с 3 по 8 декабря, двое взрослых"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


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


@pytest.mark.parametrize(
    "order",
    [
        "давай в Турцию",
        "давай в Турцию, там дешевле?",
        # #390 review: orders with no word of CHOICE_RE or VERSUS_ORDER_RE stay orders
        "лучше в Турцию, там дешевле?",
        "в Турцию, там дешевле?",
        "перенеси в Турцию, там дешевле?",
        "Турция, там дешевле?",
    ],
)
def test_price_versus_a_choice_of_the_place_still_moves_the_plan(store, order):
    """#390: «давай в Турцию» is a choice, not a question; the plan goes to Turkey."""
    before = at(store, "compare")
    after = say(store, order, before.session_id)
    assert after.plan.spec.destinations == ["Турция"]


@pytest.mark.parametrize(
    "order", ["поехали в Египет, там дешевле?", "тогда в Египет, раз там дешевле?"]
)
def test_price_versus_a_move_to_egypt_still_moves_the_plan(store, order):
    before = at(store, "compare")
    after = say(store, order, before.session_id)
    assert after.plan.spec.destinations != before.plan.spec.destinations


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
    ],
)
def test_price_versus_request_is_not_the_answer(store, request_):
    before = at(store, "compare")
    after = say(store, request_, before.session_id)
    assert "по рынку" not in after.reply
    assert "цифр не выдумываю" not in after.reply
    assert "недорого" in after.plan.spec.wishes  # the old rules read it as a wish


# #483: a mode comparison is a question too: the answer has the plan prices, the spec stays.
def test_mode_versus_question_changes_nothing(store):
    before = at(store, "compare")
    after = say(store, "а поезд дешевле, чем самолёт?", before.session_id)
    assert after.plan.spec == before.plan.spec and after.stage == "compare"
    assert "план не меняю" in after.reply
    for mode, name in (("train", "Поезд"), ("flight", "Самолёт")):
        prices = [o.price_rub for o in before.plan.offers if getattr(o, "mode", None) == mode]
        if prices:  # TRAP-4: the price in the reply is the cheapest offer of the plan
            assert f"{name}: от {min(prices):,} ₽".replace(",", " ") in after.reply
        else:
            assert f"{name}: в плане нет" in after.reply


def test_wish_next_to_a_versus_question_is_applied(store):
    before = at(store, "compare")
    after = say(store, "а дешевле, чем в Турции, с санаторием?", before.session_id)
    assert after.plan.spec.destinations == before.plan.spec.destinations
    assert "недорого" not in after.plan.spec.wishes
    assert set(after.plan.spec.wishes) - set(before.plan.spec.wishes), after.plan.spec.wishes
    assert "цифр не выдумываю" in after.reply


@pytest.mark.parametrize("stage", ["compare", "checkout"])
def test_mode_versus_question_with_a_wish(store, stage):
    before = at(store, stage)
    after = say(store, "а поезд дешевле, чем самолёт? с санаторием", before.session_id)
    assert after.plan.spec.modes == before.plan.spec.modes
    assert "санаторий" in after.plan.spec.wishes and "недорого" not in after.plan.spec.wishes
    assert "план не меняю" not in after.reply


@pytest.mark.parametrize("stage", ["compare", "checkout"])
def test_mode_versus_without_chem_changes_nothing(store, stage):
    before = at(store, stage)
    after = say(store, "поезд дешевле самолёта?", before.session_id)
    assert after.plan.spec == before.plan.spec


def test_mode_next_to_a_versus_question_is_applied(store):
    before = at(store, "compare")
    after = say(store, "а дешевле, чем в Турции, поездом?", before.session_id)
    assert after.plan.spec.destinations == before.plan.spec.destinations
    assert after.plan.spec.modes == ["train"]


@pytest.mark.parametrize("stage", ["compare", "checkout"])
@pytest.mark.parametrize(
    "text",
    [
        "поезд дешевле самолёта? с санаторием",
        "с санаторием, поезд дешевле самолёта?",
        "с санаторием. самолёт дороже поезда?",
    ],
)
def test_mode_versus_without_chem_next_to_a_wish(store, stage, text):
    """#483 review: no recursion (HTTP 500); the wish is applied, the question edits nothing."""
    before = at(store, stage)
    after = say(store, text, before.session_id)
    spec = after.plan.spec
    assert spec.modes == before.plan.spec.modes
    assert "санаторий" in spec.wishes and "недорого" not in spec.wishes
    assert spec.budget_rub == before.plan.spec.budget_rub


def test_mode_versus_names_the_source_of_a_real_flight_TRAP_7(store):
    from api.model import _mode_versus_reply

    r = at(store, "compare")
    plane = [o for o in r.plan.offers if getattr(o, "mode", None) == "flight"]
    assert plane
    assert "(синтетика)" in _mode_versus_reply(r, ["flight"])
    live = [o.model_copy(update={"synthetic": False}) if o in plane else o for o in r.plan.offers]
    r = r.model_copy(update={"plan": r.plan.model_copy(update={"offers": live})})
    text = _mode_versus_reply(r, ["flight"])
    assert "(Авиасейлс)" in text and "синтетика" not in text, text
