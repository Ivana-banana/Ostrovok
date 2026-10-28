"""#278 (critic C18): «бюджет поменьше» without a sum lowers the budget one step and the DAG
re-plans; no sum is invented (TRAP-4). Without a model (TRAP-1)."""

import re

import pytest
from talk import say

from api.store import SessionStore

START = "Сочи на 5 ночей с 10 ноября вдвоём, самолётом из Москвы"
HINT = "Напишите «беру» или номер отеля"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


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


def test_c310_other_edit_in_the_message_counts_in_the_minimum(store):
    first = say(store, "Казань на 4 ночи с 1 декабря вдвоём из Москвы")
    second = say(store, "подешевле, с завтраком", first.session_id)
    assert second.plan.spec.budget_rub is not None
    assert second.plan.spec.budget_rub >= total_of(second)
    assert "Дешевле в предложениях нет" not in second.reply
    notes = " ".join(second.plan.spec.assumptions)
    if second.plan.spec.budget_rub >= total_of(first):
        assert "ниже итога" not in notes  # the note never claims «below» for a higher budget


@pytest.mark.parametrize(
    "text",
    [
        "подешевле, не надо дорогих",
        "подешевле, не хочу переплачивать",
        "давай дешевле, не стоит брать люкс",
        "ещё подешевле, не хочу переплачивать",
    ],
)
def test_c310_tail_negation_still_lowers(store, text):
    first = say(store, START)
    second = say(store, text, first.session_id)
    assert not second.reply.startswith(HINT)
    assert second.plan.spec.budget_rub is not None
    assert total_of(second) < total_of(first)


@pytest.mark.parametrize("text", ["не надо дешевле", "дешевле не надо", "не хочу подешевле"])
def test_c310_adjacent_negation_still_refuses(store, text):
    first = say(store, START)
    assert say(store, text, first.session_id).plan.spec.budget_rub is None


@pytest.mark.parametrize(
    "text", ["есть ли дешевле?", "а есть ли что-то подешевле?", "нельзя ли подешевле?"]
)
def test_c310_li_requests_lower(store, text):
    first = say(store, START)
    second = say(store, text, first.session_id)
    assert second.plan.spec.budget_rub is not None
    assert total_of(second) < total_of(first)


def test_c310_hand_picked_hotel_never_makes_a_false_minimum(store):
    """TRAP-4: the reply never calls the total with a hand-picked hotel the minimum."""
    first = say(store, "Байкал на 4 ночи с 1 декабря вдвоём из Москвы")
    picked = say(store, "беру второй", first.session_id)
    r = say(store, "подешевле", picked.session_id)
    from api.model import _ranked_total

    floor = _ranked_total(r.plan)
    assert total_of(r) == floor
    assert "ваш выбор:" in r.reply and "дороже бюджета" in r.reply
    assert "минимум под ваши условия" in r.reply


def test_c310_not_a_cheaper_request_questions():
    from api.model import asks_cheaper

    assert not asks_cheaper("дешевле не бывает?")
    assert not asks_cheaper("дешевле, чем в среднем?")
