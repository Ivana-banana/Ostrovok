"""#278 (critic C18): «бюджет поменьше» without a sum lowers the budget one step and the DAG
re-plans; no sum is invented (TRAP-4). Without a model (TRAP-1)."""

import re

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore

START = "Сочи на 5 ночей с 10 ноября вдвоём, самолётом из Москвы"
HINT = "Напишите «беру» или номер отеля"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def total_of(reply) -> int:
    return sum(o.price_rub for o in reply.plan.offers if o.offer_id in reply.plan.selected)


@pytest.mark.parametrize(
    "text",
    ["бюджет поменьше", "подешевле", "дешевле", "можно экономнее", "слишком дорого"],
)
def test_c18_cheaper_without_sum_rebuilds(store, text):
    first = say(store, START)
    second = say(store, text, first.session_id)
    assert "Было → стало" in second.reply
    assert total_of(second) < total_of(first)
    assert second.plan.spec.budget_rub is not None
    assert second.plan.spec.budget_rub < total_of(first)


def test_c18_second_ask_goes_lower_or_names_minimum(store):
    first = say(store, START)
    second = say(store, "подешевле", first.session_id)
    third = say(store, "ещё подешевле", second.session_id)
    again = say(store, "подешевле", third.session_id)
    for r in (third, again):
        assert not r.reply.startswith(HINT)
        assert total_of(r) <= total_of(second)
    cheapest = total_of(again)
    last = say(store, "бюджет поменьше", again.session_id)
    assert total_of(last) == cheapest
    assert f"{cheapest:,}".replace(",", " ") in last.reply  # the minimum is named from tools


@pytest.mark.parametrize(
    "text",
    ["это дешевле, чем в среднем?", "дешевле ли это, чем самому?", "почему так дорого?"],
)
def test_c18_question_is_no_budget_edit(store, text):
    first = say(store, START)
    second = say(store, text, first.session_id)
    assert second.plan.spec.budget_rub is None
    assert re.search(r"\S", second.reply)


def test_c310_budget_never_below_the_cheapest_total(store):
    first = say(store, START)
    second = say(store, "бюджет поменьше", first.session_id)
    assert second.plan.spec.budget_rub >= total_of(second)
    assert "Снизил итог до" in second.reply and "не уложиться" not in second.reply
    third = say(store, "бюджет поменьше", second.session_id)
    assert third.plan.spec.budget_rub == second.plan.spec.budget_rub  # nothing cheaper: unchanged
    assert "Дешевле в предложениях нет" in third.reply
    assert f"{total_of(third):,}".replace(",", " ") in third.reply


@pytest.mark.parametrize("text", ["не надо дешевле", "дешевле не надо", "не нужно подешевле"])
def test_c310_negation_lowers_no_budget(store, text):
    first = say(store, START)
    second = say(store, text, first.session_id)
    assert second.plan.spec.budget_rub is None


@pytest.mark.parametrize("text", ["можно ли подешевле?", "а можно ли подешевле"])
def test_c310_can_i_cheaper_is_a_request(store, text):
    first = say(store, START)
    second = say(store, text, first.session_id)
    assert second.plan.spec.budget_rub is not None
    assert total_of(second) < total_of(first)
