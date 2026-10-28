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


# #316: «подешевле» at the minimum, and the questions «дешевле не бывает?», «не надо дешевле».
AT_MIN = "Сочи на 5 ночей с 10 ноября вдвоём, самолётом из Москвы, недорого"


def rub(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₽"


@pytest.mark.parametrize("text", ["подешевле", "бюджет поменьше"])
def test_c316_cheaper_at_the_minimum_changes_nothing(store, text):
    first = say(store, AT_MIN)
    second = say(store, text, first.session_id)
    assert "Дешевле в предложениях нет" in second.reply
    assert "минимум под ваши условия" in second.reply and rub(total_of(first)) in second.reply
    assert "было" not in second.reply and "Было → стало" not in second.reply
    assert "С вашими условиями" not in second.reply
    assert total_of(second) == total_of(first)
    assert second.plan.spec.wishes == first.plan.spec.wishes  # «недорого» is no new condition
    again = say(store, text, second.session_id)
    assert "Дешевле в предложениях нет" in again.reply
    assert total_of(again) == total_of(first)


def test_c316_cheaper_word_at_the_minimum_adds_no_wish(store):
    first = say(store, "Сочи на 5 ночей с 10 ноября вдвоём, самолётом из Москвы")
    low = say(store, "подешевле", first.session_id)
    assert "недорого" in low.plan.spec.wishes  # a real step: the word is the wish
    again = say(store, "подешевле", low.session_id)
    assert "Дешевле в предложениях нет" in again.reply
    assert "С вашими условиями" not in again.reply
    assert again.plan.spec.wishes == low.plan.spec.wishes


def test_c316_how_cheaper_is_not_a_condition_edit(store):
    from api.model import _other_edits

    assert not _other_edits({"budget_rub": 1000, "wishes": ["недорого"]})
    assert _other_edits({"budget_rub": 1000, "wishes": ["недорого", "море"]})
    assert _other_edits({"budget_rub": 1000, "meal": "BB"})


@pytest.mark.parametrize("text", ["дешевле не бывает?", "а дешевле не бывает?"])
def test_c316_cheaper_never_happens_names_the_minimum(store, text):
    first = say(store, START)
    ans = say(store, text, first.session_id)
    floor = total_of(say(store, "подешевле", first.session_id))
    assert f"минимум в предложениях — {rub(floor)}" in ans.reply  # from the offers (TRAP-4)
    assert rub(total_of(first)) in ans.reply  # the plan is not the minimum: «Бывает»
    assert ans.reply != HINT and not ans.reply.startswith(HINT)
    assert total_of(ans) == total_of(first) and ans.plan.spec.budget_rub is None
    assert "Было → стало" not in ans.reply
    assert ans.plan.spec.wishes == first.plan.spec.wishes


def test_c316_cheaper_never_happens_at_the_minimum(store):
    first = say(store, AT_MIN)
    ans = say(store, "дешевле не бывает?", first.session_id)
    assert f"минимум в предложениях — {rub(total_of(first))}" in ans.reply
    assert "уже в плане" in ans.reply
    assert total_of(ans) == total_of(first)


@pytest.mark.parametrize("text", ["не надо дешевле", "дешевле не надо", "не хочу подешевле"])
def test_c316_refusal_says_the_budget_stays(store, text):
    first = say(store, START)
    ans = say(store, text, first.session_id)
    assert "бюджет не меняю" in ans.reply and rub(total_of(first)) in ans.reply
    assert not ans.reply.startswith(HINT)
    assert ans.plan.spec.budget_rub is None
    assert ans.plan.spec.wishes == first.plan.spec.wishes  # the refused word is no wish
    assert total_of(ans) == total_of(first)
    assert "Было → стало" not in ans.reply


def test_c316_c18_eval_case_still_lowers(store):
    first = say(store, START)
    second = say(store, "бюджет поменьше", first.session_id)
    assert total_of(second) < total_of(first) and "Было → стало" in second.reply


def test_c316_why_not_cheaper_is_no_refusal():
    from api.model import talks_cheaper

    assert not talks_cheaper("почему не дешевле?")  # stays the wish «недорого»
    assert talks_cheaper("не надо дешевле") and talks_cheaper("дешевле не бывает?")


@pytest.mark.parametrize("edit", ["на 2 дня позже", "на 7 ночей", "нас трое", "на 3 дня дольше"])
def test_c316_agent_budget_is_marked_and_ends_with_the_next_edit(store, edit):
    first = say(store, START)
    second = say(store, "подешевле", first.session_id)
    spec = second.plan.spec
    if spec.budget_rub is None:  # nothing cheaper: no budget set, nothing to mark
        return
    assert any("задан агентом" in a for a in spec.assumptions)
    third = say(store, edit, second.session_id)
    assert third.plan.spec.budget_rub is None
    assert not any(a.startswith("бюджет") for a in third.plan.spec.assumptions)


def test_c316_user_budget_survives_the_date_edit(store):
    first = say(store, START)
    second = say(store, "бюджет 60000", first.session_id)
    third = say(store, "на 2 дня позже", second.session_id)
    assert third.plan.spec.budget_rub == 60000
