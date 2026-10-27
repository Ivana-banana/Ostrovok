"""#216: what a question at a built plan answers beyond tests/test_plan_questions.py.

Hotels without «» (Kazan), «почему этот отель?», «почему не поезд?», the transfer duration,
no precipitation data, «чем X лучше?», and the tails that are no hotel name. Rules only, no
model (TRAP-1). Numbers come from the offers and the catalogue (TRAP-4); each price is marked
(TRAP-7). A question changes neither the plan, nor the cart, nor the stage.
"""

import re

import pytest

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
        assert f"{money(o.price_rub)} (синтетика), в пути {h} ч {m} мин" in r2.reply


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("question", ["дождь будет?", "снег будет?"])
def test_trap_4_precipitation_has_no_data(store, stage, question):
    """TRAP-4: the catalogue has no precipitation; the answer says so and gives no mm or days."""
    _, r2 = ask(store, stage, question)
    assert "Данных об осадках" in r2.reply and "воздух 12 °C" in r2.reply
    assert "мм" not in r2.reply and "дн" not in r2.reply


@pytest.mark.parametrize("question", ["тепло будет?", "а жарко там будет?"])
def test_weather_without_li_is_a_weather_question(store, question):
    _, r2 = ask(store, "assemble", question)
    assert "воздух 12 °C" in r2.reply and PLAN_REPEAT not in r2.reply


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
