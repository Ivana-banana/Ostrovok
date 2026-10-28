"""#532 (TRAP-5): no booking without the road or without the hotel.

A failed transport source (ops.guarded gives []) left the plan with a hotel alone: the
compare warning vanished on «беру», the cart took the hotel and «подтверждаю» booked it. A cart
whose hotel the user removed booked flights alone. Now the timeline has an error, the cart is
not filled, and checkout is a 409 that names what is missing. Own tickets (C5) and a stay at
the origin need no road.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from talk import say

from api import offers
from api.contracts import ChatReply, Plan, TripSpec
from api.fixtures import rank
from api.main import app, get_store
from api.model import NO_HOTEL
from api.offers import find_offers
from api.optimizer import pick_hotel
from api.store import SessionStore
from api.timeline import NO_ROAD, build_timeline

KAZAN = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё."


class NoTransport:
    """The transport source is down: an OSError, so ops.guarded gives []."""

    def legs(self, spec, origin, place):
        raise TimeoutError("transport timed out")


class NoHotels:
    def hotels(self, spec, place, now):
        raise ConnectionError("hotels down")


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


@pytest.fixture
def client(store):
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


def _road_errors(r: ChatReply) -> list[str]:
    return [i.text for i in r.plan.issues if i.severity == "error" and i.text.startswith(NO_ROAD)]


def test_trap_5_failed_transport_source_keeps_the_hotel_out_of_the_cart(monkeypatch, store):
    monkeypatch.setattr(offers, "TRANSPORT", NoTransport())
    r = say(store, KAZAN)
    assert r.stage == "compare"
    assert "Источник «транспорт» не ответил" in r.reply
    assert _road_errors(r)  # the board shows it before «беру»
    r = say(store, "беру", r.session_id)
    assert r.stage == "compare" and not r.cart.items
    assert "В корзину пока не кладу: в плане нет дороги." in r.reply
    assert "Транспорта в плане нет: нет дороги Москва → Казань и Казань → Москва" in r.reply
    assert "билеты у нас есть" in r.reply  # what the user can do
    errors = _road_errors(r)
    assert errors and "Предложений транспорта нет" in errors[0]
    r = say(store, "да", r.session_id)
    assert r.stage == "compare" and not r.cart.items and r.voucher is None


def test_trap_5_own_tickets_after_the_block_reach_the_booking(monkeypatch, store):
    """C5 (#408): the user knows the way; the hotel alone is the whole purchase."""
    monkeypatch.setattr(offers, "TRANSPORT", NoTransport())
    r = say(store, KAZAN)
    r = say(store, "беру", r.session_id)
    r = say(store, "билеты у нас есть", r.session_id)
    assert r.plan.spec.own_transport
    r = say(store, "беру", r.session_id)
    assert r.stage == "assemble" and not _road_errors(r)
    r = say(store, "да", r.session_id)
    assert r.stage == "checkout" and [o.kind for o in r.cart.items] == ["hotel"]
    r = say(store, "подтверждаю", r.session_id)
    assert r.stage == "booked" and r.voucher is not None


def test_trap_5_failed_hotel_source_gives_no_cart_of_the_road_alone(monkeypatch, store):
    monkeypatch.setattr(offers, "HOTELS", NoHotels())
    r = say(store, KAZAN)
    r = say(store, "беру", r.session_id)
    assert r.stage == "compare" and not r.cart.items
    assert NO_HOTEL in r.reply


def _plan(spec: TripSpec, legs: str) -> Plan:
    """Kazan with the ranked hotel and the legs named: "out", "back", both or none."""
    city = spec.destinations[0]
    found = find_offers(spec, city)
    ranked, _ = rank(spec, found)
    hotel = next(i for i in ranked if i.startswith("h-"))
    road = [o for o in found if o.kind == "transport"]
    chosen = []
    if "out" in legs:
        chosen.append(next(o.offer_id for o in road if o.destination == city))
    if "back" in legs:
        chosen.append(next(o.offer_id for o in road if o.origin == city))
    return Plan(spec=spec, offers=found, selected=pick_hotel(chosen, found, hotel))


def _spec(**kw) -> TripSpec:
    base = {
        "origin": "Москва",
        "destinations": ["Казань"],
        "date_from": date(2026, 12, 11),
        "date_to": date(2026, 12, 13),
        "nights": 2,
    }
    return TripSpec(**{**base, **kw})


def test_trap_5_missing_leg_back_is_an_error_with_offered_fixes():
    plan = _plan(_spec(), "out")
    _, issues = build_timeline(plan)
    assert issues[0].severity == "error"
    road = [i for i in issues if i.text.startswith(NO_ROAD)]
    assert len(road) == 1 and "нет дороги Казань → Москва." in road[0].text
    assert "Москва → Казань" not in road[0].text
    by_id = {o.offer_id: o for o in plan.offers}
    assert road[0].fixes  # TRAP-4: offers of the plan, legs of the missing direction
    assert all(by_id[x].kind == "transport" and by_id[x].origin == "Казань" for x in road[0].fixes)
    assert "рейс из предложенных" in road[0].text


@pytest.mark.parametrize(
    ("spec", "legs"),
    [
        (_spec(), "out back"),  # the whole road
        (_spec(own_transport=True), ""),  # own tickets: no leg is searched (C5)
        (_spec(origin="Казань"), ""),  # a stay in the city of departure (#94)
        (_spec(origin=None), ""),  # no origin known: nothing to check against
    ],
)
def test_trap_5_trips_that_need_no_more_road_have_no_road_error(spec, legs):
    _, issues = build_timeline(_plan(spec, legs))
    assert not [i for i in issues if i.text.startswith(NO_ROAD)]


def _at_checkout(store) -> ChatReply:
    r = say(store, KAZAN)
    r = say(store, "беру", r.session_id)
    r = say(store, "да", r.session_id)
    assert r.stage == "checkout" and r.cart.items
    return r


def _cart(client, r, action, offer_id):
    res = client.post(
        "/api/cart", json={"session_id": r.session_id, "action": action, "offer_id": offer_id}
    )
    assert res.status_code == 200, res.text
    return res.json()


def _checkout(client, r, total):
    return client.post(
        "/api/checkout", json={"session_id": r.session_id, "confirm_total_rub": total}
    )


def test_trap_5_checkout_without_the_hotel_is_409_that_names_it(client, store):
    r = _at_checkout(store)
    hotel = next(o for o in r.cart.items if o.kind == "hotel")
    cart = _cart(client, r, "remove", hotel.offer_id)
    res = _checkout(client, r, cart["total_rub"])
    assert res.status_code == 409
    assert "В корзине нет отеля" in res.json()["detail"]
    assert store.get(r.session_id).voucher is None
    back = _cart(client, r, "add", hotel.offer_id)
    assert _checkout(client, r, back["total_rub"]).status_code == 200


def test_trap_5_checkout_without_the_leg_back_is_409_that_names_it(client, store):
    r = _at_checkout(store)
    leg = next(o for o in r.cart.items if o.kind == "transport" and o.origin == "Казань")
    cart = _cart(client, r, "remove", leg.offer_id)
    res = _checkout(client, r, cart["total_rub"])
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert "В корзине нет дороги Казань → Москва" in detail and "«Вернуть»" in detail
    assert store.get(r.session_id).voucher is None


def test_trap_5_chat_confirm_without_the_hotel_says_why_and_books_nothing(client, store):
    """«подтверждаю» books through the same path (#382): the refusal is the reply."""
    r = _at_checkout(store)
    hotel = next(o for o in r.cart.items if o.kind == "hotel")
    _cart(client, r, "remove", hotel.offer_id)
    r = say(store, "подтверждаю", r.session_id)
    assert r.stage == "checkout" and r.voucher is None
    assert "В корзине нет отеля" in r.reply
