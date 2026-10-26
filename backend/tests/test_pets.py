"""#131: with a pet the agent never leads to a booking with a hotel or a leg that refuses pets."""

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.offers import find_offers
from api.optimizer import optimize
from api.store import SessionStore

KAZAN_DOG = "Казань на выходные 12–14 декабря с собакой"
FROM_MOSCOW = "из Москвы, двое взрослых"
FLIGHT_ONLY = "Анталия, 5 ночей с 14 ноября, двое взрослых, из Москвы, с собакой"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def chosen(r):
    return [o for o in r.plan.offers if o.offer_id in r.plan.selected]


def test_kazan_with_a_dog_never_selects_a_hotel_or_leg_that_refuses_pets(store):
    r = say(store, f"{KAZAN_DOG}, {FROM_MOSCOW}")
    assert r.plan.spec.pets and r.stage == "compare"
    picked = [o for o in chosen(r) if o.kind in ("hotel", "transport")]
    assert any(o.kind == "hotel" for o in picked)
    assert all(o.pets_allowed for o in picked), [o.title for o in picked if not o.pets_allowed]


def test_kazan_with_a_dog_reaches_the_cart_with_pet_friendly_items_only(store):
    r = say(store, f"{KAZAN_DOG}, {FROM_MOSCOW}")
    r = say(store, "беру", r.session_id)
    r = say(store, "да", r.session_id)
    assert r.stage == "checkout" and r.cart.items
    assert all(o.pets_allowed for o in r.cart.items if o.kind in ("hotel", "transport"))


def test_the_reply_says_why_the_other_hotels_of_the_city_are_not_chosen(store):
    r = say(store, f"{KAZAN_DOG}, {FROM_MOSCOW}")
    hotel = next(e for e in r.plan.explanations if e.offer_id.startswith("h-"))
    assert any("животн" in x for x in hotel.tradeoffs)
    assert not hotel.risks  # nothing is broken: the pick takes pets


def test_a_hotel_without_a_pet_mark_is_listed_with_the_reason(store):
    r = say(store, f"{KAZAN_DOG}, {FROM_MOSCOW}")
    rows = [x for x in r.reply.splitlines() if x[:2] in ("1.", "2.", "3.", "4.", "5.", "6.")]
    assert len(rows) == 6
    refusing = [x for x in rows if "нет отметки о животных" in x]
    assert len(refusing) == 5 and all("Отель «" not in x for x in refusing)


def test_without_a_pet_the_city_keeps_its_five_hotels(store):
    r = say(store, f"Казань на выходные 12–14 декабря, {FROM_MOSCOW}")
    hotels = {o.title for o in r.plan.offers if o.kind == "hotel"}
    assert len(hotels) == 5 and not any(t.startswith("Отель «") for t in hotels)
    assert "животн" not in r.reply


def test_optimizer_selects_nothing_when_no_trip_takes_the_pet(store):
    r = say(store, FLIGHT_ONLY)
    spec = r.plan.spec
    offers = find_offers(spec, "Анталия")
    assert optimize(spec, offers).selected == []


def test_dead_end_says_so_and_offers_another_city_and_never_fills_the_cart(store):
    r = say(store, FLIGHT_ONLY)
    assert r.stage == "select" and not r.plan.selected and not r.cart.items
    assert "животн" in r.reply and "Анталия" in r.reply
    options = r.questions[0].options
    assert options and "Калининград" not in options  # no train there: the dog has no trip
    assert "Казань" in options
    again = say(store, "да", r.session_id)
    assert again.stage != "checkout" and not again.cart.items
    final = say(store, "подтверждаю", again.session_id)
    assert final.voucher is None and final.stage != "booked"


def test_a_user_pick_of_a_refusing_hotel_is_the_explicit_consent(store):
    r = say(store, f"{KAZAN_DOG}, {FROM_MOSCOW}")
    rows = [x for x in r.reply.splitlines() if x[:2] in ("1.", "2.", "3.", "4.", "5.", "6.")]
    n = next(i for i, x in enumerate(rows, 1) if "нет отметки о животных" in x)
    picked = say(store, str(n), r.session_id)
    assert picked.stage == "assemble"
    assert any(o.kind == "hotel" and not o.pets_allowed for o in chosen(picked))
