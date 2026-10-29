"""#212: the tails of the answers at a built plan.

1. «почему Панорама, а не Бульвар?» names the hotel outside the plan.
2. A tariff with free cancellation is named when the hotel has one.
3. «беру, а что с визой?»: the consent goes through, the question gets its fact line.
4. «чем Бульвар лучше?» compares the hotels (the rule is in #216: the test pins it here).
5. Grammar of the «no climate» line.
C13 (eval/cases_critic.json): «почему не Турция?» gives a fact and keeps the plan.
A question changes neither the plan, nor the cart, nor the stage; each price is marked (TRAP-7).
"""

import re

import pytest
from talk import say

from api import answers
from api.contracts import HotelOffer, Plan, TripSpec
from api.guards import unknown_prices
from api.optimizer import hotel_rows
from api.store import SessionStore

SOCHI = "Сочи из Москвы с 3 по 8 декабря, двое взрослых"
C13 = "Сочи на 5 ночей с 10 ноября вдвоём, самолётом из Москвы"
STAGES = ["compare", "assemble", "checkout"]
PRICE_RE = re.compile(r"\d[\d  ]*\s?₽")


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def at(store, stage, first=SOCHI):
    r = say(store, first)
    for text in ("беру", "да")[: {"compare": 0, "assemble": 1, "checkout": 2}[stage]]:
        r = say(store, text, r.session_id)
    assert r.stage == stage
    return r


def ask(store, stage, question, first=SOCHI):
    """A question: the stage, the plan and the cart stay; no model."""
    r = at(store, stage, first)
    r2 = say(store, question, r.session_id)
    assert r2.stage == stage and r2.plan == r.plan and r2.cart == r.cart and r2.llm is False
    return r, r2


def money(n):
    return format(n, ",").replace(",", " ") + " ₽"


def tariffs(r, name):
    return [o for o in r.plan.offers if isinstance(o, HotelOffer) and name in o.title]


def chosen(r):
    return next(
        h for h in hotel_rows(r.plan.offers, r.plan.selected) if h.offer_id in r.plan.selected
    )


def assert_marked(reply):
    """TRAP-7: each price of the answer is marked right after the number."""
    for m in PRICE_RE.finditer(reply):
        assert reply[m.end() :].startswith(" (синтетика"), reply


# 1


def short(hotel):
    """«Старый город» of «Отель «Старый город», Сочи»."""
    return re.search(r"«(.+?)»", hotel.title).group(1)


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize(
    "question", ["почему {plan}, а не Бульвар?", "почему Бульвар, а не {plan}?"]
)
def test_the_hotel_outside_the_plan_is_found_when_the_plan_hotel_is_named(store, stage, question):
    # #280: the plan hotel comes from the plan (a fare change moves it), the same in every stage
    plan = short(chosen(at(store, stage)))
    assert plan != "Бульвар"
    r, r2 = ask(store, stage, question.format(plan=plan))
    assert short(chosen(r)) == plan
    row = next(h for h in hotel_rows(r.plan.offers, r.plan.selected) if "Бульвар" in h.title)
    assert f"{money(row.price_rub)} (синтетика)" in r2.reply
    assert f"{money(chosen(r).price_rub)} (синтетика)" in r2.reply
    assert "против" in r2.reply and "«Бульвар» выигрывает" in r2.reply
    assert_marked(r2.reply)


# 2


ORDINAL_WORDS = ["первый", "второй", "третий", "четвёртый", "пятый"]


def test_the_free_cancellation_tariff_of_the_named_hotel_is_named(store):
    # #280: the plan hotel is the row of the table without a free cancellation tariff in the
    # data, picked by the user; a fare change moves the ranked hotel, not this premise
    first = at(store, "compare")
    rows = hotel_rows(first.plan.offers, first.plan.selected)
    n, plan = next(
        (i, short(h))
        for i, h in enumerate(rows)
        if not any(o.cancellation.refundable for o in tariffs(first, short(h)))
    )
    assert plan != "Бульвар"
    r = say(store, ORDINAL_WORDS[n], first.session_id)
    r2 = say(store, "почему не Бульвар?", r.session_id)
    assert r2.stage == r.stage and r2.plan == r.plan and r2.cart == r.cart and r2.llm is False
    assert short(chosen(r)) == plan
    shown = next(h for h in hotel_rows(r.plan.offers, r.plan.selected) if "Бульвар" in h.title)
    assert not shown.cancellation.refundable and not chosen(r).cancellation.refundable
    free = min(
        (o for o in tariffs(r, "Бульвар") if o.cancellation.refundable), key=lambda o: o.price_rub
    )
    assert "отмена: у обоих невозвратный тариф" in r2.reply  # the shown tariffs, as before
    assert (
        f"Тариф с бесплатной отменой: «Бульвар» — {money(free.price_rub)} (синтетика)" in r2.reply
    )
    assert f"«{plan}» — в данных нет" in r2.reply  # no such tariff at the plan hotel: said so
    assert_marked(r2.reply)


def test_a_free_tariff_comes_from_an_offer_of_the_plan_TRAP_4(store):
    r, r2 = ask(store, "assemble", "почему не Бульвар?")
    offers = [o.model_dump() for o in r.plan.offers]
    assert unknown_prices(r2.reply, offers, totals=[r.cart.total_rub]) == []


def offer(offer_id, name, price, meal="BB", refundable=False):
    return HotelOffer(
        offer_id=offer_id,
        title=f"Отель «{name}», Сочи",
        price_rub=price,
        meal=meal,
        cancellation={"refundable": refundable},
        city="Сочи",
        tz="Europe/Moscow",
        date_in="2026-12-03",
        date_out="2026-12-08",
        adults=2,
        lat=43.6,
        lon=39.7,
    )


def test_no_free_tariff_in_the_data_adds_no_line():
    """The hotels have no tariff with free cancellation: the answer does not invent one."""
    a, b = offer("h1", "Панорама", 7000), offer("h2", "Бульвар", 9000)
    plan = Plan(spec=TripSpec(destinations=["Сочи"]), offers=[a, b], selected=["h1"])
    reply = answers.versus_fact(plan, "почему не бульвар?")
    assert "отмена: у обоих невозвратный тариф" in reply
    assert "Тариф с бесплатной отменой" not in reply
    assert answers._free_tariff(plan, b) is None


def test_the_hotel_in_the_plan_names_its_free_tariff_too():
    offers = [
        offer("a", "Панорама", 7000),
        offer("b", "Панорама", 8100, meal="RO", refundable=True),
        offer("c", "Бульвар", 9000),
    ]
    plan = Plan(spec=TripSpec(destinations=["Сочи"]), offers=offers, selected=["a"])
    same = answers.versus_fact(plan, "почему панорама?")
    assert "уже в плане" in same
    assert "Тариф с бесплатной отменой: «Панорама» — 8 100 ₽ (синтетика), питание RO" in same
    versus = answers.versus_fact(plan, "почему не бульвар?")
    assert (
        "Тариф с бесплатной отменой: «Бульвар» — в данных нет; «Панорама» — 8 100 ₽ (синтетика)"
        in versus
    )


# 3


def test_consent_with_a_visa_question_gets_the_fact(store):
    r = say(store, SOCHI)
    r2 = say(store, "беру, а что с визой?", r.session_id)
    assert r2.stage == "assemble" and r2.cart == r.cart
    assert r2.reply.startswith("Данных о визах и правилах въезда в каталоге нет")
    assert "нет данных" in r2.reply and "План поездки:" in r2.reply
    assert not re.search(r"виза\s+не\s+нужна|без\s+визы|безвиз", r2.reply)


def test_consent_with_a_weather_question_gets_the_fact(store):
    r = say(store, SOCHI)
    r2 = say(store, "погода хорошая? тогда беру", r.session_id)
    assert r2.stage == "assemble"
    assert (
        "Сочи в декабре: днём 11.6 °C, вода 13.5 °C — оценка по каталогу, не прогноз." in r2.reply
    )
    assert "План поездки:" in r2.reply


def test_the_cart_consent_with_a_question_gets_the_fact(store):
    r = at(store, "assemble")
    r2 = say(store, "да, а что с визой?", r.session_id)
    assert r2.stage == "checkout" and r2.cart.items and "нет данных" in r2.reply
    assert "В корзине" in r2.reply
    assert_marked(r2.reply)


@pytest.mark.parametrize("text", ["беру", "беру 2", "да, давай оформим"])
def test_a_plain_consent_gets_no_fact_line(store, text):
    r = say(store, SOCHI)
    reply = say(store, text, r.session_id).reply
    assert "нет данных" not in reply and "оценка по каталогу" not in reply


# 4


@pytest.mark.parametrize("stage", STAGES)
def test_chem_hotel_better_is_a_comparison(store, stage):
    r, r2 = ask(store, stage, "чем Бульвар лучше?")
    row = next(h for h in hotel_rows(r.plan.offers, r.plan.selected) if "Бульвар" in h.title)
    assert f"{money(row.price_rub)} (синтетика)" in r2.reply
    assert "План поездки:" not in r2.reply and "«Бульвар» выигрывает" in r2.reply
    assert_marked(r2.reply)


# 5


def test_no_climate_line_has_no_wrong_case():
    plan = Plan(spec=TripSpec(destinations=["Сочи"], month=12))
    lines = answers.weather_fact(plan, "", ["Квазимодово"]).splitlines()
    assert lines[0] == "Квазимодово: климата в декабре в данных нет — нет данных."
    assert "Климата для" not in lines[0]


def test_no_climate_for_the_month_keeps_the_name(monkeypatch):
    monkeypatch.setattr(answers.catalog, "month_climate", lambda place, month: (None, None))
    plan = Plan(spec=TripSpec(destinations=["Сочи"], month=12))
    line = answers.weather_fact(plan, "", ["Сочи"]).splitlines()[0]
    assert line == "Сочи: климата в декабре в данных нет — нет данных."
    plan = Plan(spec=TripSpec(destinations=["Сочи"]))
    line = answers.weather_fact(plan, "", ["Калуга"]).splitlines()[0]
    assert line == "Калуга: климата вашего месяца в данных нет — нет данных."


# C13


@pytest.mark.parametrize("question", ["почему не Турция?", "почему не Анталия?"])
@pytest.mark.parametrize("stage", ["compare", "assemble"])
def test_c13_why_not_another_country_gives_a_fact_and_keeps_the_plan(store, stage, question):
    _, r2 = ask(store, stage, question, C13)
    assert "Турц" in r2.reply or "Антал" in r2.reply
    assert re.search(r"\d+(?:\.\d+)? °C", r2.reply)  # a number from the catalogue
    assert "не сравнивал" in r2.reply and "могу сравнить" in r2.reply.lower()
    assert r2.plan.spec.destinations == ["Сочи"]
    assert not any("исключено" in a for a in r2.plan.spec.assumptions)
    assert "нет в данных" not in r2.reply


def test_c13_the_offered_switch_works(store):
    r = at(store, "compare", C13)
    r2 = say(store, "почему не Турция?", r.session_id)
    assert "«поехали в Турцию»" in r2.reply
    r3 = say(store, "поехали в Турцию", r.session_id)
    assert r3.plan.spec.destinations == ["Турция"]


def test_why_not_the_country_of_the_plan_is_the_plan(store):
    _, r2 = ask(store, "compare", "почему не Турция?", "Анталия, 3 ночи с 20 ноября, двое взрослых")
    assert "это и есть направление плана" in r2.reply
