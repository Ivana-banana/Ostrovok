"""Ports (ADR-0002, #286): each current adapter satisfies its Protocol and the invariants the
core reads; every search goes through the module variables of api.offers.

Run this file after you assign your own adapter to offers.HOTELS, TRANSPORT or TRANSFERS.
"""

import inspect
from datetime import UTC, date, datetime, timedelta

import pytest

from api import offers
from api.contracts import HotelProvider, TransferProvider, TransportProvider, TripSpec
from api.fixtures import travel_hours
from api.offers import find_offers, resolve_place, transfer_id

SOON = date.today() + timedelta(days=60)
PORTS = [
    (HotelProvider, "HOTELS", "hotels"),
    (TransportProvider, "TRANSPORT", "legs"),
    (TransferProvider, "TRANSFERS", "transfers"),
]


def spec(**kw) -> TripSpec:
    data = {"origin": "Москва", "date_from": SOON, "date_to": SOON + timedelta(days=3)}
    return TripSpec(nights=3, **{**data, **kw})


def search(city: str, **kw) -> tuple[TripSpec, list]:
    s = spec(destinations=[city], **kw)
    return s, find_offers(s, city)


@pytest.mark.parametrize(("port", "var", "method"), PORTS)
def test_adapter_has_the_method_of_its_port(port, var, method):
    """The active adapter has the port method with the same parameters."""
    adapter = getattr(offers, var)
    want = list(inspect.signature(getattr(port, method)).parameters)
    got = list(inspect.signature(getattr(adapter, method)).parameters)
    assert ["self", *got] == want


# Казань: the OSM snapshot; Анапа, Сочи: synthetic hotels; Рим: abroad, no direct flight.
@pytest.mark.parametrize("city", ["Анапа", "Казань", "Сочи", "Рим"])
def test_offers_keep_the_invariants_the_core_reads(city):
    s, items = search(city)
    hotels = [o for o in items if o.kind == "hotel"]
    legs = [o for o in items if o.kind == "transport"]
    transfers = [o for o in items if o.kind == "transfer"]
    assert hotels and legs and transfers
    cities = {h.city for h in hotels}
    assert len(cities) == 1
    by_title: dict[str, set[str]] = {}
    for h in hotels:
        by_title.setdefault(h.title, set()).add(h.city)
        assert h.cancellation.free_until is None or h.cancellation.free_until.tzinfo
    assert all(len(c) == 1 for c in by_title.values())  # tariffs of a hotel: one title, city
    there = [x for x in legs if x.origin == s.origin]
    back = [x for x in legs if x.origin != s.origin]
    assert there and back
    assert legs == there + back  # there first, then back
    assert all(x.destination in cities for x in there)  # letter by letter (timeline.py)
    assert all(x.origin in cities and x.destination == s.origin for x in back)  # cart.py
    assert all(x.depart_at.tzinfo and x.arrive_at.tzinfo for x in legs)
    pairs = {transfer_id(h.offer_id, x.offer_id) for h in hotels for x in legs}
    assert {t.offer_id for t in transfers} <= pairs


def test_search_and_travel_hours_go_through_the_module_variables(monkeypatch):
    """A swapped adapter is used by find_offers and by fixtures.travel_hours (inspiration)."""
    calls = []

    class Recording:
        def __init__(self, inner, method):
            self.inner, self.method = inner, method

        def __getattr__(self, name):
            fn = getattr(self.inner, name)
            return lambda *a: calls.append(self.method) or fn(*a)

    for _, var, method in PORTS:
        monkeypatch.setattr(offers, var, Recording(getattr(offers, var), method))
    s, items = search("Казань", date_from=SOON)
    assert items and calls == ["hotels", "legs", "transfers"]
    calls.clear()
    assert travel_hours(s, resolve_place("Москва"), resolve_place("Казань")) > 0
    assert calls == ["legs"]


def test_empty_hotel_adapter_gives_no_hotels(monkeypatch):
    """An empty answer of a provider stays empty: no silent synthetic fallback (ADR-0002)."""

    class NoHotels:
        def hotels(self, spec, place, now):
            return []

    monkeypatch.setattr(offers, "HOTELS", NoHotels())
    _, items = search("Казань")
    assert not [o for o in items if o.kind in ("hotel", "extra", "transfer")]
    assert [o for o in items if o.kind == "transport"]


def test_now_reaches_the_hotel_adapter(monkeypatch):
    """offers_for_place passes the cancellation clock to the port, aware."""
    seen = []
    real = offers.HOTELS

    class Spy:
        def hotels(self, spec, place, now):
            seen.append(now)
            return real.hotels(spec, place, now)

    monkeypatch.setattr(offers, "HOTELS", Spy())
    early = datetime(2026, 1, 1, tzinfo=UTC)
    s = spec()
    find_offers(s, "Казань", now=early)
    assert seen == [early]
