"""Ports (ADR-0002, #286, #307): each adapter set satisfies its Protocol and the invariants the
core reads, and the agent drives it to a voucher; every search of the chosen place goes through
the module variables of api.offers, the estimate of many places never does.

Your own adapter: add a set to ADAPTER_SETS below, for example
`"mine": (MyHotels(), offers.TRANSPORT, offers.TRANSFERS)`, then run `make check`. Every test
with the `adapters` fixture runs on each set. "foreign" is a sample partner API: its own ids
without the suffixes of the core, titles without a time and without km.
"""

import inspect
from datetime import UTC, date, datetime, time, timedelta

import pytest
from talk import say

from api import model, offers
from api.contracts import (
    ExtraOffer,
    HotelProvider,
    Plan,
    TransferProvider,
    TransportProvider,
    TripSpec,
)
from api.fixtures import travel_hours
from api.offers import find_offers, resolve_place, transfer_id
from api.optimizer import pick_hotel
from api.store import SessionStore
from api.timeline import build_timeline

SOON = date.today() + timedelta(days=60)
PORTS = [
    (HotelProvider, "HOTELS", "hotels"),
    (TransportProvider, "TRANSPORT", "legs"),
    (TransferProvider, "TRANSFERS", "transfers"),
]
KAZAN = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё."
ADD_ON = {"early": "Ранний заезд", "late": "Поздний выезд", "prev": "Ночь накануне"}
FOREIGN_EARLY = time(9, 0)  # not the 06:00 of the synthetic adapter: the core reads `at`


class ForeignHotels:
    """A partner API of hotels: ids P<n>, add-ons E<n>.<k> with tag and at, no time in titles;
    one add-on of a kind the core does not know (a plain add-on)."""

    def hotels(self, spec, place, now):
        out = []
        for n, (h, extras) in enumerate(offers.OsmOrSynthetic().hotels(spec, place, now)):
            hid = f"P{n}"
            mine = [
                x.model_copy(
                    update={
                        "offer_id": f"E{n}.{k}",
                        "for_offer_id": hid,
                        "title": ADD_ON[x.tag],
                        "at": FOREIGN_EARLY if x.tag == "early" else x.at,
                    }
                )
                for k, x in enumerate(extras)
            ]
            mine.append(
                ExtraOffer(
                    offer_id=f"E{n}.b", title="Завтрак", for_offer_id=hid, price_rub=900, tag="meal"
                )
            )
            out.append((h.model_copy(update={"offer_id": hid}), mine))
        return out


class ForeignTransport:
    """A partner API of tickets: ids L<n>, titles without a time."""

    def legs(self, spec, origin, place):
        legs = offers.SyntheticTransport().legs(spec, origin, place)
        return [
            x.model_copy(update={"offer_id": f"L{n}", "title": f"{x.origin} — {x.destination}"})
            for n, x in enumerate(legs)
        ]


class ForeignTransfers:
    """A partner API of rides: ids R<n>, the pair in hotel_offer_id and leg_offer_id, no km."""

    def transfers(self, spec, place, hotels, legs):
        rides = offers.SyntheticTransfers().transfers(spec, place, hotels, legs)
        return [
            x.model_copy(update={"offer_id": f"R{n}", "title": "Трансфер партнёра"})
            for n, x in enumerate(rides)
        ]


# name -> (HOTELS, TRANSPORT, TRANSFERS); None: the adapters of the project as they are.
ADAPTER_SETS = {
    "project": None,
    "foreign": (ForeignHotels(), ForeignTransport(), ForeignTransfers()),
}


@pytest.fixture(params=list(ADAPTER_SETS))
def adapters(request, monkeypatch) -> str:
    """Assign one adapter set to the module variables of api.offers for the test."""
    chosen = ADAPTER_SETS[request.param]
    for (_, var, _), adapter in zip(PORTS, chosen or (), strict=False):
        monkeypatch.setattr(offers, var, adapter)
    return request.param


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def spec(**kw) -> TripSpec:
    data = {"origin": "Москва", "date_from": SOON, "date_to": SOON + timedelta(days=3)}
    return TripSpec(nights=3, **{**data, **kw})


def search(city: str, **kw) -> tuple[TripSpec, list]:
    s = spec(destinations=[city], **kw)
    return s, find_offers(s, city)


@pytest.mark.parametrize(("port", "var", "method"), PORTS)
def test_adapter_has_the_method_of_its_port(adapters, port, var, method):
    """The active adapter has the port method with the same parameters."""
    adapter = getattr(offers, var)
    want = list(inspect.signature(getattr(port, method)).parameters)
    got = list(inspect.signature(getattr(adapter, method)).parameters)
    assert ["self", *got] == want


# Казань: the OSM snapshot; Анапа, Сочи: synthetic hotels; Рим: abroad, no direct flight.
@pytest.mark.parametrize("city", ["Анапа", "Казань", "Сочи", "Рим"])
def test_offers_keep_the_invariants_the_core_reads(adapters, city):
    s, items = search(city)
    hotels = [o for o in items if o.kind == "hotel"]
    legs = [o for o in items if o.kind == "transport"]
    transfers = [o for o in items if o.kind == "transfer"]
    extras = [o for o in items if o.kind == "extra"]
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
    assert {t.offer_id for t in transfers} <= pairs  # the key of the core (offers_for_place)
    hotel_ids = {h.offer_id for h in hotels}
    assert {x.for_offer_id for x in extras} <= hotel_ids
    assert {x.tag for x in extras} & {"early", "late", "prev"}  # the timeline reads the tag
    if adapters == "foreign":
        assert all(t.ref and t.ref.startswith("R") for t in transfers)  # the provider id stays


def test_foreign_ids_and_titles_reach_a_voucher(adapters, store):
    """The whole path on each set: a request, a cart, a payment, a voucher; no error in the
    timeline. The foreign set has no id suffixes, no time and no km in its titles."""
    r = say(store, KAZAN)
    r = say(store, "беру", r.session_id)
    r = say(store, "да", r.session_id)
    assert r.stage == "checkout" and r.cart.items
    assert not [i for i in r.plan.issues if i.severity == "error"]
    r = say(store, "подтверждаю", r.session_id)
    assert r.stage == "booked" and r.voucher is not None
    kinds = {o.kind for o in r.voucher.items}
    assert {"hotel", "transport"} <= kinds
    if adapters == "foreign":
        hotel = next(o for o in r.voucher.items if o.kind == "hotel")
        assert hotel.offer_id.startswith("P")
        rides = [o for o in r.voucher.items if o.kind == "transfer"]
        assert rides and all(o.ref and o.ref.startswith("R") for o in rides)


def _morning_train(city: str = "Казань") -> Plan:
    """The night train there (07:10 in Казань), the 15:20 flight back, the first hotel."""
    s = spec(destinations=[city], date_from=date(2026, 12, 11), date_to=date(2026, 12, 13))
    s = s.model_copy(update={"nights": 2})
    items = find_offers(s, city)
    legs = [o for o in items if o.kind == "transport"]
    there = min(
        (o for o in legs if o.destination == city and o.mode == "train"), key=lambda o: o.price_rub
    )
    back = next(
        o for o in legs if o.origin == city and o.mode == "flight" and o.depart_at.hour == 15
    )
    hotel = next(o for o in items if o.kind == "hotel")
    return Plan(
        spec=s,
        offers=items,
        selected=pick_hotel([there.offer_id, back.offer_id], items, hotel.offer_id),
    )


def test_TRAP_5_timeline_reads_the_tag_and_the_time_of_a_foreign_add_on(monkeypatch):
    """The early check-in E<n>.0 has no «-early» in its id and no time in its title: the
    timeline finds it by tag and checks the guest in at its `at` (09:00)."""
    for (_, var, _), adapter in zip(PORTS, ADAPTER_SETS["foreign"], strict=True):
        monkeypatch.setattr(offers, var, adapter)
    plan = _morning_train()
    _, issues = build_timeline(plan)
    fixes = issues[0].fixes
    by_id = {o.offer_id: o for o in plan.offers}
    early = by_id[fixes[0]]
    assert early.kind == "extra" and early.tag == "early" and "-early" not in early.offer_id
    assert "E" in fixes[0] and by_id[fixes[1]].tag == "prev"
    bought = plan.model_copy(update={"selected": [*plan.selected, early.offer_id]})
    events, _ = build_timeline(bought)
    check_in = next(e for e in events if e.title.startswith("Заселение"))
    assert check_in.at.time() == FOREIGN_EARLY
    assert not [e for e in events if "Завтрак" in e.title]  # a plain add-on is not an event


def test_TRAP_5_without_the_new_fields_the_timeline_parses_the_id_and_the_title():
    """The old rule stays: an add-on and a ride without tag, at and the pair give the same
    timeline as with them (ADR-0002: the fields first, the parse as the fallback)."""
    plan = _morning_train()
    bare = [
        o.model_copy(update={"tag": None, "at": None})
        if o.kind == "extra"
        else o.model_copy(update={"hotel_offer_id": None, "leg_offer_id": None})
        if o.kind == "transfer"
        else o
        for o in plan.offers
    ]
    old = plan.model_copy(update={"offers": bare})
    for p in (plan, old):
        _, issues = build_timeline(p)
        early = issues[0].fixes[0]
        assert early.endswith("-early")
    bought = [p.model_copy(update={"selected": [*p.selected, early]}) for p in (plan, old)]
    assert build_timeline(bought[0]) == build_timeline(bought[1])
    assert build_timeline(plan) == build_timeline(old)


class Recording:
    """An adapter that records each call of its port method, then calls the inner one."""

    def __init__(self, inner, method, calls):
        self.inner, self.method, self.calls = inner, method, calls

    def __getattr__(self, name):
        fn = getattr(self.inner, name)
        return lambda *a: self.calls.append(self.method) or fn(*a)


def test_search_of_the_chosen_place_goes_through_the_module_variables(monkeypatch):
    """find_offers (dag.py: the chosen place) calls the live ports, the estimate does not."""
    calls: list[str] = []
    for _, var, method in PORTS:
        monkeypatch.setattr(offers, var, Recording(getattr(offers, var), method, calls))
    s, items = search("Казань", date_from=SOON)
    assert items and calls == ["hotels", "legs", "transfers"]
    calls.clear()
    assert find_offers(s, "Казань", estimate=True) and calls == []
    assert travel_hours(s, resolve_place("Москва"), resolve_place("Казань")) > 0
    assert calls == []


def test_estimate_path_goes_through_the_estimate_variables(monkeypatch):
    """travel_hours and find_offers(estimate=True) read offers.ESTIMATE_*: a test or a business
    may swap the estimate too, apart from the live ports."""
    calls: list[str] = []
    for _, var, method in PORTS:
        name = f"ESTIMATE_{var}"
        monkeypatch.setattr(offers, name, Recording(getattr(offers, name), method, calls))
    s, _ = search("Казань", date_from=SOON)
    assert calls == []
    assert find_offers(s, "Казань", estimate=True)
    assert calls == ["hotels", "legs", "transfers"]
    calls.clear()
    assert travel_hours(s, resolve_place("Москва"), resolve_place("Казань")) > 0
    assert calls == ["legs"]


def test_inspiration_and_pet_dead_ends_never_call_the_live_ports(monkeypatch, store):
    """ADR-0002: dozens of places per turn are priced by the estimate adapters only."""
    calls: list[str] = []
    for _, var, method in PORTS:
        monkeypatch.setattr(offers, var, Recording(getattr(offers, var), method, calls))
    r = say(store, "Тёплое море, 10–17 ноября, из Москвы, 2 взрослых")
    assert r.plan.destinations and r.plan.destinations[0].est_total_rub and calls == []
    pets = spec(pets=True, wishes=["горы"])
    assert find_offers(pets, "Казань", estimate=True)  # the estimate prices the places
    model._pet_dead_cities(pets)
    assert calls == []


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
