"""#338: hard conditions of the request reach the spec; the reply names what no data checks."""

import pytest

from api.extract import HARD_WISHES, extract

CASES = [
    # F05: wheelchair is the existing «тяжело ходить» wish (the optimizer checks lift data)
    ("Едем на море в Анапу в июле, бабушка на коляске.", "тяжело ходить"),
    ("Едем в Сочи с детской коляской на неделю.", "детская коляска"),
    ("Прилетаю в Минск 14 ноября, я веганка.", "веганское питание"),
    (
        "Сапсан приходит в Питер в 00:35, заселиться надо сразу, а не ждать до двух.",
        "поздний заезд",
    ),
    ("Подмосковье с годовалым сыном, обязательно детская кроватка в номере.", "детская кроватка"),
    ("Командировка в Тюмень, нужен отель у Технопарка.", "рядом: Технопарка"),
    ("Командировка в Тюмень, завтрак с 7 утра.", "завтрак с 07:00"),
]


@pytest.mark.parametrize(("text", "wish"), CASES)
def test_hard_condition_reaches_the_spec(text, wish):
    found, _ = extract(text)
    assert wish in found.get("wishes", []), found


LOOKALIKES = [
    "Едем к Чёрному морю, у Чёрного моря хотим жить, без кроватки",
    "Тёплое море у Средиземного моря, у Маши аллергия на кошек",
    "Едем в Казань, гуляли около Москвы, у Волги красиво",
    "Заселение после 3 дня, завтрак с 2 детьми",
    "Из Москвы в Сочи, отель у Сочи",
]


@pytest.mark.parametrize("text", LOOKALIKES)
def test_lookalike_words_are_no_hard_wish(text):
    wishes = extract(text)[0].get("wishes", [])
    assert not [w for w in wishes if w.startswith(("рядом:", "завтрак с"))], wishes
    assert not set(wishes) & set(HARD_WISHES), wishes


def test_vegetarian_is_not_vegan():
    wishes = extract("Я вегетарианец, еду в Казань")[0]["wishes"]
    assert "вегетарианское питание" in wishes and "веганское питание" not in wishes


@pytest.mark.parametrize(
    ("text", "late"),
    [
        ("заселение после 00:35", True),
        ("заселение после 3 ночи", True),
        ("заселение после 3 дня", False),
    ],
)
def test_late_check_in_is_by_the_hour(text, late):
    assert ("поздний заезд" in extract(f"Минск, отель, {text}")[0].get("wishes", [])) == late


@pytest.mark.parametrize(("text", "answer"), CASES)
def test_reply_names_the_hard_wish_as_checked_or_not(tmp_path, text, answer):
    from talk import say

    from api.store import SessionStore

    r = say(SessionStore(tmp_path / "s.db"), text)
    assert answer in r.plan.spec.wishes, r.plan.spec.wishes
    if answer != "тяжело ходить":  # the optimizer checks the lift data of a built plan
        assert "Не проверяю по данным" in r.reply and answer.split(": ")[-1] in r.reply, r.reply


def test_llm_facts_mark_the_unverified_wishes():
    from api.contracts import Plan, TripSpec
    from api.llm import _facts

    plan = Plan(spec=TripSpec(wishes=["поздний заезд", "море"]))
    assert _facts(plan)["trip"]["wishes_not_verified"] == ["поздний заезд"]
