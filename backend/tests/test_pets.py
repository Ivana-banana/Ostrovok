"""#131: with a pet the agent never leads to a booking with a hotel or a leg that refuses pets."""

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.offers import find_offers
from api.optimizer import optimize
from api.store import SessionStore

KAZAN_DOG = "Казань на выходные 12–14 декабря с собакой, из Москвы, двое взрослых"
FLIGHT_ONLY = "Анталия, 5 ночей с 14 ноября, двое взрослых, из Москвы, с собакой"
NO_CATALOG = "нет в каталоге"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def chosen(r):
    return [o for o in r.plan.offers if o.offer_id in r.plan.selected]


def hotel_rows(r):
    return [x for x in r.reply.splitlines() if x[:2] in ("1.", "2.", "3.", "4.", "5.", "6.")]


def test_kazan_with_a_dog_picks_no_hotel_and_keeps_only_legs_that_take_the_pet(store):
    r = say(store, KAZAN_DOG)
    assert r.plan.spec.pets and r.stage == "compare"
    picked = chosen(r)
    assert picked and all(o.kind == "transport" and o.pets_allowed for o in picked)
    assert not r.cart.items


def test_kazan_reply_says_the_pet_condition_is_unconfirmed_and_offers_a_way_out(store):
    r = say(store, KAZAN_DOG)
    assert "нет отметки о приёме животных" in r.reply and "не подтверждено" in r.reply
    assert "уточнив у отеля" in r.reply
    options = r.questions[0].options
    assert options and "Казань" not in options
    rows = hotel_rows(r)
    assert len(rows) == 5 and all("нет отметки о животных" in x for x in rows)


def test_kazan_beru_is_no_consent_and_fills_no_cart(store):
    r = say(store, KAZAN_DOG)
    again = say(store, "беру", r.session_id)
    assert again.stage == "compare" and not again.cart.items
    assert not any(o.kind == "hotel" for o in chosen(again))
    assert "нет отметки о приёме животных" in again.reply
    assert say(store, "да", r.session_id).stage != "checkout"


def test_a_click_on_an_offered_city_goes_on_without_the_catalogue_phrase(store):
    r = say(store, KAZAN_DOG)
    other = say(store, r.questions[0].options[0], r.session_id)
    assert NO_CATALOG not in other.reply
    assert other.stage == "compare" and other.plan.spec.destinations != ["Казань"]
    picked = [o for o in chosen(other) if o.kind in ("hotel", "transport")]
    assert any(o.kind == "hotel" for o in picked) and all(o.pets_allowed for o in picked)


def test_the_hotel_number_is_the_explicit_consent(store):
    r = say(store, KAZAN_DOG)
    picked = say(store, "4", r.session_id)
    assert picked.stage == "assemble"
    assert any(o.kind == "hotel" and not o.pets_allowed for o in chosen(picked))
    assert say(store, "да", r.session_id).stage == "checkout"


def test_without_a_pet_the_city_keeps_its_five_hotels_and_a_choice(store):
    r = say(store, "Казань на выходные 12–14 декабря, из Москвы, двое взрослых")
    hotels = {o.title for o in r.plan.offers if o.kind == "hotel"}
    assert len(hotels) == 5 and any(o.kind == "hotel" for o in chosen(r))
    assert "животн" not in r.reply


def test_optimizer_selects_nothing_when_no_leg_takes_the_pet(store):
    r = say(store, FLIGHT_ONLY)
    assert optimize(r.plan.spec, find_offers(r.plan.spec, "Анталия")).selected == []


def test_flight_only_dead_end_says_so_and_never_fills_the_cart(store):
    r = say(store, FLIGHT_ONLY)
    assert r.stage == "select" and not r.plan.selected and not r.cart.items
    assert "животн" in r.reply and "Анталия" in r.reply
    options = r.questions[0].options
    assert options and "Калининград" not in options  # no train there: the dog has no trip
    again = say(store, "да", r.session_id)
    assert again.stage != "checkout" and not again.cart.items
    final = say(store, "подтверждаю", again.session_id)
    assert final.voucher is None and final.stage != "booked"


def test_a_click_after_the_flight_only_dead_end_has_no_catalogue_phrase(store):
    r = say(store, FLIGHT_ONLY)
    other = say(store, r.questions[0].options[0], r.session_id)
    assert NO_CATALOG not in other.reply and other.stage == "compare"
    assert all(o.pets_allowed for o in chosen(other) if o.kind in ("hotel", "transport"))
