"""#186: a question at a built plan gets a fact, not a repeat of the plan.

Weather — the catalogue climate; transfer — the price in the plan; «почему не Бульвар?» —
price, cancellation, distance and rating of both hotels; visas — «нет данных». A question
changes neither the plan, nor the cart, nor the stage, and is never a consent.
"""

import re

import pytest

from api import llm
from api.contracts import ChatRequest, HotelOffer
from api.guards import unknown_prices
from api.model import chat
from api.store import SessionStore

SOCHI = "Сочи из Москвы с 3 по 8 декабря, двое взрослых"
WEATHER = [
    "погода?",
    "какая там погода?",
    "а море тёплое?",
    "какая погода в декабре?",
    "какая погода в Сочи?",
]
VISA = ["что с визой?", "нужна ли виза?"]
VERSUS = ["почему не Бульвар?", "а чем Бульвар хуже?"]
TRANSFER = ["сколько стоит трансфер?"]
ALL = [*WEATHER, *VISA, *VERSUS, *TRANSFER]
PRICE_RE = re.compile(r"\d[\d  ]*\s?₽")


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


def hotels(r, name):
    return [o for o in r.plan.offers if isinstance(o, HotelOffer) and name in o.title]


def rub(n):
    return f"{n:,} ₽".replace(",", " ")


@pytest.mark.parametrize("stage", ["compare", "assemble", "checkout"])
@pytest.mark.parametrize("question", ALL)
def test_question_changes_nothing(store, stage, question):
    r = at(store, stage)
    r2 = say(store, question, r.session_id)
    assert r2.stage == stage and r2.plan == r.plan and r2.cart == r.cart
    assert r2.llm is False and r2.voucher is None
    assert store.get(r.session_id) == r2


@pytest.mark.parametrize("question", WEATHER)
def test_weather_is_the_catalogue_climate(store, question):
    r = at(store, "assemble")
    reply = say(store, question, r.session_id).reply
    # December is not in the catalogue: no data for it, the November estimate is named as such
    assert "в декабре в данных нет" in reply and "нет данных" in reply
    assert "ноябрь: воздух 12 °C, вода 16 °C" in reply
    assert "План поездки" not in reply  # a fact, not a repeat of the plan


def test_weather_of_november_is_the_estimate(store):
    r = say(store, "Сочи из Москвы с 3 по 8 ноября, двое взрослых")
    r = say(store, "беру", r.session_id)
    reply = say(store, "какая погода?", r.session_id).reply
    assert "Сочи в ноябре: воздух 12 °C, вода 16 °C" in reply and "не прогноз" in reply


@pytest.mark.parametrize("question", VISA)
def test_visa_is_no_data_TRAP_4(store, question):
    """TRAP-4: the data has no visa rules; the answer says so and invents none."""
    r = at(store, "assemble")
    reply = say(store, question, r.session_id).reply
    assert "нет данных" in reply
    assert not re.search(r"виза\s+не\s+нужна|без\s+визы|безвиз", reply)


@pytest.mark.parametrize("question", VERSUS)
def test_why_not_hotel_names_price_cancellation_distance(store, question):
    r = at(store, "assemble")
    reply = say(store, question, r.session_id).reply
    chosen = next(o for o in r.plan.offers if o.offer_id in r.plan.selected and o.kind == "hotel")
    other = hotels(r, "Бульвар")[0]  # its row in the comparison: the first tariff
    assert rub(other.price_rub) in reply and rub(chosen.price_rub) in reply
    assert "отмена" in reply
    assert f"{other.distance_center_km:g} км" in reply
    assert f"{chosen.distance_center_km:g} км" in reply
    assert "Отель в плане выигрывает: дешевле" in reply
    assert "«Бульвар» выигрывает: ближе к центру, рейтинг выше" in reply


def test_named_hotel_of_the_plan_is_in_the_plan(store):
    r = at(store, "assemble")
    chosen = next(o for o in r.plan.offers if o.offer_id in r.plan.selected and o.kind == "hotel")
    name = re.search(r"«([^»]+)»", chosen.title).group(1)
    reply = say(store, f"почему {name}?", r.session_id).reply
    assert "уже в плане" in reply and rub(chosen.price_rub) in reply


def test_transfer_price_from_the_plan(store):
    r = at(store, "assemble")
    reply = say(store, "сколько стоит трансфер?", r.session_id).reply
    transfers = [o for o in r.plan.offers if o.offer_id in r.plan.selected and o.kind == "transfer"]
    assert transfers and all(f"{o.title} — {rub(o.price_rub)}" in reply for o in transfers)


@pytest.mark.parametrize("stage", ["compare", "assemble", "checkout"])
@pytest.mark.parametrize("question", ALL)
def test_every_price_is_marked_and_known_TRAP_7(store, stage, question):
    """TRAP-7: every price in the answer carries «синтетика»; TRAP-4: each is an offer price."""
    r = at(store, stage)
    reply = say(store, question, r.session_id).reply
    offers = [o.model_dump() for o in r.plan.offers]
    picked = sum(o.price_rub for o in r.plan.offers if o.offer_id in r.plan.selected)
    totals = [r.cart.total_rub, picked]
    assert unknown_prices(reply, offers, totals=totals) == []
    lines = [x for x in reply.splitlines() if PRICE_RE.search(x)]
    assert all("синтетика" in x for x in lines), reply
    for m in PRICE_RE.finditer(reply):  # each price of a fact line is marked right after it
        line = reply[: m.start()].rsplit("\n", 1)[-1]
        if not line.startswith("Итого"):
            assert reply[m.end() :].startswith(" (синтетика)"), reply


@pytest.mark.parametrize("stage", ["compare", "assemble"])
def test_question_is_not_consent(store, stage):
    """A question moves no stage; the next consent works as before."""
    r = at(store, stage)
    r2 = say(store, "почему не Бульвар?", r.session_id)
    assert r2.stage == stage and r2.cart == r.cart
    r3 = say(store, {"compare": "беру", "assemble": "да"}[stage], r2.session_id)
    assert r3.stage == {"compare": "assemble", "assemble": "checkout"}[stage]


@pytest.mark.parametrize("stage", ["compare", "assemble", "checkout"])
@pytest.mark.parametrize("question", ALL)
def test_question_never_reaches_the_model(monkeypatch, store, stage, question):
    """With a model, a question still changes no spec: the model is not asked at all."""
    r = at(store, stage)
    asked = []

    def stub(m, s, until=None, today=None):
        asked.append(m)
        return {"wishes": ["бассейн"], "destinations": ["Казань"]}

    monkeypatch.setattr(llm, "extract_spec", stub)
    r2 = say(store, question, r.session_id)
    assert asked == []
    assert r2.plan.spec == r.plan.spec and r2.stage == stage and r2.cart == r.cart
