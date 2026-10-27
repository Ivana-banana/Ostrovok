"""#216: what a question at a built plan answers beyond tests/test_plan_questions.py.

Hotels without «» (Kazan), «почему этот отель?», «почему не поезд?», the transfer duration,
no precipitation data, «чем X лучше?», and the tails that are no hotel name. Rules only, no
model (TRAP-1). Numbers come from the offers and the catalogue (TRAP-4); each price is marked
(TRAP-7). A question changes neither the plan, nor the cart, nor the stage.
"""

import re

import pytest

from api.answers import _versus
from api.contracts import ChatRequest
from api.guards import unknown_prices
from api.model import chat
from api.optimizer import hotel_rows
from api.store import SessionStore

SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых, отель до 40 тысяч, нужен завтрак"
KAZAN = "Казань, 3 ночи с 20 ноября, двое взрослых"
STAGES = ["compare", "assemble", "checkout"]
PLAN_REPEAT = "План поездки:"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def at(store, stage, first=SOCHI):
    r = say(store, first)
    for text in ("беру", "да")[: {"compare": 0, "assemble": 1, "checkout": 2}[stage]]:
        r = say(store, text, r.session_id)
    assert r.stage == stage
    return r


def ask(store, stage, question, first=SOCHI):
    r = at(store, stage, first)
    r2 = say(store, question, r.session_id)
    assert r2.stage == stage and r2.plan == r.plan and r2.cart == r.cart
    assert r2.llm is False
    return r, r2


def rows(r):
    return hotel_rows(r.plan.offers, r.plan.selected)


def hotel(r, name):
    return next(h for h in rows(r) if name in h.title)


def money(n):
    return format(n, ",").replace(",", " ") + " ₽"


def total(r):
    return sum(o.price_rub for o in r.plan.offers if o.offer_id in r.plan.selected)


def prices_known(r, reply):
    offers = [o.model_dump() for o in r.plan.offers]
    assert (
        unknown_prices(
            reply, offers, totals=[total(r), r.cart.total_rub, r.plan.spec.hotel_budget_rub or 0]
        )
        == []
    )


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("name", ["Корстон", "Шаляпин", "DoubleTree", "Островский"])
def test_why_not_a_hotel_without_guillemets_compares_the_data(store, stage, name):
    """Kazan titles are «Корстон, Казань»: the city is no part of the name."""
    r, r2 = ask(store, stage, f"почему не {name}?", KAZAN)
    other = hotel(r, name)
    mine = next(h for h in rows(r) if h.offer_id in r.plan.selected)
    assert "нет в данных" not in r2.reply
    assert f"{money(other.price_rub)} (синтетика)" in r2.reply
    assert f"{money(mine.price_rub)} (синтетика)" in r2.reply
    assert PLAN_REPEAT not in r2.reply
    prices_known(r, r2.reply)


def test_why_not_a_name_in_a_case_ending(store):
    _, r2 = ask(store, "assemble", "почему не выбрали Бульвара?")
    assert "Бульвар" in r2.reply and "цена" in r2.reply


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("question", ["почему этот отель?", "почему выбрали Панораму?"])
def test_why_this_hotel_gives_the_reasons(store, stage, question):
    r, r2 = ask(store, stage, question)
    mine = hotel(r, "Панорама")
    assert f"{money(mine.price_rub)} (синтетика)" in r2.reply
    assert "Почему он:" in r2.reply and PLAN_REPEAT not in r2.reply
    prices_known(r, r2.reply)


@pytest.mark.parametrize("stage", STAGES)
def test_why_not_the_train_compares_the_legs(store, stage):
    r, r2 = ask(store, stage, "почему не поезд?")
    train = next(o for o in r.plan.offers if o.kind == "transport" and o.mode == "train")
    assert f"{money(train.price_rub)} (синтетика)" in r2.reply and "в пути" in r2.reply
    assert PLAN_REPEAT not in r2.reply
    prices_known(r, r2.reply)


def test_why_not_a_mode_without_offers_says_so(store):
    _, r2 = ask(store, "assemble", "почему не на машине?")
    assert "в предложениях плана нет" in r2.reply


@pytest.mark.parametrize("stage", STAGES)
def test_transfer_has_price_and_duration(store, stage):
    r, r2 = ask(store, stage, "сколько стоит трансфер?")
    legs = [o for o in r.plan.offers if o.kind == "transfer" and o.offer_id in r.plan.selected]
    assert legs
    for o in legs:
        minutes = int((o.arrive_at - o.depart_at).total_seconds() // 60)
        h, m = divmod(minutes, 60)
        way = f"{h} ч {m} мин" if m else f"{h} ч"  # 28.1 km of catalog.json: exactly 1 h
        assert f"{money(o.price_rub)} (синтетика), в пути {way}" in r2.reply


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("question", ["дождь будет?", "снег будет?"])
def test_trap_4_precipitation_is_the_catalogue_month(store, stage, question):
    """TRAP-4: precipitation is the November sum of catalog.json for Сочи; no days, no forecast."""
    _, r2 = ask(store, stage, question)
    assert "Сочи в ноябре: днём 15.7 °C, вода 17.1 °C, осадки 135 мм за месяц" in r2.reply
    assert "Прогноза погоды в данных нет." in r2.reply and "дней" not in r2.reply


@pytest.mark.parametrize("question", ["тепло будет?", "а жарко там будет?"])
def test_weather_without_li_is_a_weather_question(store, question):
    _, r2 = ask(store, "assemble", question)
    assert "днём 15.7 °C" in r2.reply and PLAN_REPEAT not in r2.reply


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("question", ["чем Бульвар лучше?", "чем Гранд хуже?"])
def test_chem_name_comparative_compares_hotels(store, stage, question):
    r, r2 = ask(store, stage, question)
    name = "Бульвар" if "Бульвар" in question else "Гранд"
    assert f"{money(hotel(r, name).price_rub)} (синтетика)" in r2.reply
    assert PLAN_REPEAT not in r2.reply


def test_chem_two_named_hotels_are_compared_with_each_other(store):
    r, r2 = ask(store, "assemble", "чем Парк лучше Панорамы?")
    assert "Парк" in r2.reply and "Панорама" in r2.reply
    assert f"{money(hotel(r, 'Парк').price_rub)} (синтетика)" in r2.reply
    assert "уже в плане" not in r2.reply


def test_chem_two_named_hotels_neither_in_the_plan(store):
    """Neither is the chosen hotel: the first named is set against the second, no «в плане»."""
    r, r2 = ask(store, "assemble", "чем Бульвар лучше Парка?")
    assert f"{money(hotel(r, 'Бульвар').price_rub)} (синтетика)" in r2.reply
    assert f"{money(hotel(r, 'Парк').price_rub)} (синтетика)" in r2.reply
    assert "в плане" not in r2.reply.split("\n")[1]


@pytest.mark.parametrize("stage", STAGES)
def test_parking_is_not_the_hotel_park(store, stage):
    """«парковки» is no «Парк»: no comparison with that hotel."""
    _, r2 = ask(store, stage, "почему нет парковки?")
    assert "против" not in r2.reply and "Парк" not in r2.reply.replace("Парковк", "")


@pytest.mark.parametrize("stage", STAGES)
def test_the_departure_city_is_no_hotel_and_no_missing_data(store, stage):
    _, r2 = ask(store, stage, "почему не Москва?")
    assert "нет в данных" not in r2.reply and "город вылета" in r2.reply


@pytest.mark.parametrize("stage", STAGES)
def test_trap_4_a_name_that_is_no_hotel_is_not_in_the_data(store, stage):
    _, r2 = ask(store, stage, "почему не Лазурь?")
    assert "«Лазурь» нет в данных" in r2.reply and "₽" not in r2.reply


@pytest.mark.parametrize("stage", STAGES)
def test_a_listed_hotel_is_never_not_in_the_data(store, stage):
    for first in (SOCHI, KAZAN):
        r = at(store, stage, first)
        for h in rows(r):
            name = re.search(r"«([^»]+)»", h.title)
            name = name.group(1) if name else h.title.split(",")[0].split()[0]
            r2 = say(store, f"почему не {name}?", r.session_id)
            assert "нет в данных" not in r2.reply, (first, name)


def test_why_not_a_month_is_still_a_date_proposal(store):
    r = at(store, "assemble")
    r2 = say(store, "а почему не в январе?", r.session_id)
    assert "Перенести на январь" in r2.reply and "нет в данных" not in r2.reply
    assert r2.plan == r.plan and r2.cart == r.cart


def test_why_not_cheaper_is_still_an_edit(store):
    r = at(store, "assemble")
    r2 = say(store, "почему не дешевле?", r.session_id)
    assert r2.stage == "compare" and "недорого" in r2.plan.spec.wishes


@pytest.mark.parametrize(
    "text", ["почему не 4 звезды?", "почему не с завтраком?", "почему не отель получше?"]
)
def test_why_not_a_property_keeps_the_older_answer(store, text):
    r = at(store, "assemble")
    r2 = say(store, text, r.session_id)
    assert r2.reply.startswith("Это вопрос: план и корзину не меняю.") and PLAN_REPEAT in r2.reply
    assert "нет в данных" not in r2.reply and "Почему он" not in r2.reply
    assert r2.plan == r.plan and r2.cart == r.cart


def test_a_question_with_a_hotel_name_is_no_consent(store):
    r = at(store, "compare", KAZAN)
    r2 = say(store, "почему не Корстон?", r.session_id)
    assert r2.stage == "compare"
    assert say(store, "беру", r2.session_id).stage == "assemble"


def test_a_request_with_a_hotel_name_is_not_answered_as_a_question(store):
    r = at(store, "assemble")
    r2 = say(store, "хочу отель Бульвар", r.session_id)
    assert r2.reply != "" and "Почему он" not in r2.reply
    assert "против" not in r2.reply


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize(
    "question",
    [
        "почему не Корстон?",
        "почему этот отель?",
        "почему не поезд?",
        "чем Шаляпин лучше?",
        "сколько стоит трансфер?",
    ],
)
def test_trap_7_every_price_in_the_answer_is_marked(store, stage, question):
    r, r2 = ask(store, stage, question, KAZAN)
    for m in re.finditer(r"\d[\d  ]*\s?₽", r2.reply):
        line = r2.reply[: m.start()].rsplit("\n", 1)[-1]
        if not line.startswith("Итого"):
            assert r2.reply[m.end() :].startswith(" (синтетика)"), r2.reply
    prices_known(r, r2.reply)


KALININGRAD = "Калининград, 3 ночи с 20 ноября, двое взрослых"
ISTANBUL = "Стамбул, 3 ночи с 20 ноября, двое взрослых"
ANTALYA = "Анталия, 3 ночи с 20 ноября, двое взрослых"
PETERSBURG = "Санкт-Петербург из Москвы, 3 ночи с 20 ноября, двое взрослых"


def test_a_hotel_with_a_word_of_the_question_in_its_name_is_not_named(store):
    """«Северная звезда» is a hotel of Калининград; «4 звезды» names no hotel."""
    r = at(store, "compare", KALININGRAD)
    assert any("Северная звезда" in h.title for h in rows(r))
    for text in ("почему не 4 звезды?", "почему не 5 звезд?", "почему не пять звёзд?"):
        r2 = say(store, text, r.session_id)
        assert "Северная звезда" not in r2.reply and "против" not in r2.reply, text
        assert r2.plan == r.plan


def test_a_hotel_whose_name_has_a_star_is_still_named(store):
    r, r2 = ask(store, "compare", "почему не Северная звезда?", KALININGRAD)
    assert f"{money(hotel(r, 'Северная звезда').price_rub)} (синтетика)" in r2.reply


def test_a_word_is_not_a_hotel_by_a_two_letter_ending(store):
    """«на берегу» is no «Лазурный берег»; «границу» is no «Гранд»."""
    r = at(store, "compare", ISTANBUL)
    assert any("Лазурный берег" in h.title for h in rows(r))
    r2 = say(store, "почему не на берегу?", r.session_id)
    assert "Лазурный берег" not in r2.reply and "против" not in r2.reply
    r, r2 = ask(store, "assemble", "почему мы не пересекаем границу?")
    assert "Гранд" not in r2.reply


@pytest.mark.parametrize("stage", ["compare", "assemble"])
@pytest.mark.parametrize(
    "text",
    ["почему не Сочи?", "почему не Хургада?", "почему не Стамбул?", "почему не Турция?"],
)
def test_trap_4_a_place_of_the_catalogue_is_not_missing_from_the_data(store, stage, text):
    """The place is in the catalogue: no «нет в данных»; the older rules read the question."""
    r = at(store, stage, ANTALYA)
    r2 = say(store, text, r.session_id)
    assert "нет в данных" not in r2.reply


def test_a_country_with_a_reason_is_the_weather_question_as_on_main(store):
    _, r2 = ask(store, "assemble", "Почему не Египет, там теплее?", ANTALYA)
    assert "Анталия в ноябре: днём 21.5 °C" in r2.reply and "нет в данных" not in r2.reply


@pytest.mark.parametrize("text", ["почему не All inclusive?", "почему не UAI?", "почему не BB?"])
def test_a_meal_word_is_not_a_hotel_name(store, text):
    r = at(store, "assemble", ANTALYA)
    r2 = say(store, text, r.session_id)
    assert "нет в данных" not in r2.reply


def test_an_order_with_chem_luchshe_is_not_a_question(store):
    r = at(store, "assemble", PETERSBURG)
    r2 = say(store, "давай самолётом, чем быстрее тем лучше", r.session_id)
    assert not r2.reply.startswith("Это вопрос")
    assert any(o.kind == "transport" and o.mode == "flight" for o in r2.plan.offers)


def test_chem_luchshe_with_a_hotel_between_is_a_question(store):
    r, r2 = ask(store, "assemble", "чем Бульвар лучше?")
    assert r2.reply.startswith("Это вопрос")
    assert f"{money(hotel(r, 'Бульвар').price_rub)} (синтетика)" in r2.reply


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("text", ["почему выбрали самолёт?", "почему выбрали перелёт?"])
def test_why_the_mode_is_no_hotel_card(store, stage, text):
    r, r2 = ask(store, stage, text)
    assert "Почему он" not in r2.reply and "★" not in r2.reply
    assert "Перелёт Москва → Сочи" in r2.reply and "в пути" in r2.reply
    prices_known(r, r2.reply)


def test_why_the_race_shows_the_legs_of_the_plan(store):
    _, r2 = ask(store, "assemble", "почему этот рейс?")
    assert "Почему он" not in r2.reply and "Перелёт Москва → Сочи" in r2.reply


@pytest.mark.parametrize("stage", STAGES)
def test_why_the_price_is_no_hotel_card(store, stage):
    _, r2 = ask(store, stage, "почему эта цена?")
    assert "Почему он" not in r2.reply and "★" not in r2.reply


def test_a_mode_missing_from_the_offers_is_named(store):
    """No train to Antalya: the answer names the mode and the route."""
    _, r2 = ask(store, "assemble", "почему не поезд?", ANTALYA)
    assert "Поездов Москва → Анталия в предложениях плана нет" in r2.reply


def test_225_comparison_without_transport_is_no_whole_trip(store):
    r, r2 = ask(store, "compare", "чем Корстон лучше?", KAZAN)
    assert "поездку целиком" in r2.reply
    plan = r.plan.model_copy(update={"offers": [o for o in r.plan.offers if o.kind != "transport"]})
    mine = next(h for h in hotel_rows(plan.offers, plan.selected) if h.offer_id in plan.selected)
    other = next(h for h in hotel_rows(plan.offers, plan.selected) if h is not mine)
    text = _versus(plan, mine, other, " в плане")
    assert "поездку целиком" not in text and "Как добраться — нет данных" in text
