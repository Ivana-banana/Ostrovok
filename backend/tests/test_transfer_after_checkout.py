"""#370 (TRAP-5): the transfer to the station or the airport starts after the hotel check-out.

Ульяновск, Киров, Курск -> Суздаль: the station is 30 km away, and the plan showed «11:25
Трансфер -> вокзал | 12:00 Выезд из отеля». The return leg must leave no earlier than the
check-out, the way to the hub and the margin at the hub. Offline, rules only (TRAP-1); times
come only from offers (TRAP-4).
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from api import catalog
from api.contracts import HotelOffer, TransferOffer, TransportOffer, TripSpec
from api.offers import offers_for_place, transfer_id

DAY_IN, DAY_OUT = date(2027, 8, 10), date(2027, 8, 13)


def spec(origin: str, place: str, modes: list[str]) -> TripSpec:
    return TripSpec(
        origin=origin,
        destinations=[place],
        date_from=DAY_IN,
        date_to=DAY_OUT,
        adults=2,
        modes=modes,
    )


def early_transfers(origin: dict, place: dict, modes: list[str]) -> list[str]:
    """Return transfers that start before the check-out of their hotel, as text."""
    offers = offers_for_place(spec(origin["name"], place["name"], modes), place, origin)
    by_id = {o.offer_id: o for o in offers}
    hotels = [o for o in offers if isinstance(o, HotelOffer)]
    back = [o for o in offers if isinstance(o, TransportOffer) and o.origin == place["name"]]
    bad = []
    for h in hotels:
        checkout = datetime.combine(h.date_out, h.check_out, ZoneInfo(h.tz))
        for leg in back:
            tr = by_id.get(transfer_id(h.offer_id, leg.offer_id))
            if isinstance(tr, TransferOffer) and tr.depart_at < checkout:
                start = tr.depart_at.astimezone(ZoneInfo(h.tz))
                bad.append(f"{place['name']} {leg.title}: трансфер {start:%d.%m %H:%M}")
    return bad


@pytest.mark.parametrize("origin", ["Ульяновск", "Киров", "Курск"])
def test_TRAP_5_suzdal_train_transfer_starts_after_check_out(origin):
    a, b = catalog.resolve(origin), catalog.resolve("Суздаль")
    assert b["station_km"] >= 25  # the far station of the case
    assert early_transfers(a, b, ["train"]) == []


def test_TRAP_5_far_airport_flight_transfer_starts_after_check_out():
    """Ейск: the airport (KRR) is about 200 km away; the 15:20 flight cannot be reached."""
    a, b = catalog.resolve("Москва"), catalog.resolve("Ейск")
    assert early_transfers(a, b, ["flight"]) == []
    back = [
        o
        for o in offers_for_place(spec("Москва", "Ейск", ["flight"]), b, a)
        if isinstance(o, TransportOffer) and o.origin == "Ейск"
    ]
    assert back  # a flight back still exists


@pytest.mark.parametrize("modes", [["train"], ["flight"]])
def test_TRAP_5_sweep_no_return_transfer_before_check_out(modes):
    """Every catalogue place from three hubs of different regions, train and plane."""
    bad: list[str] = []
    for hub in ("Москва", "Ульяновск", "Новосибирск"):
        a = catalog.resolve(hub)
        for b in catalog.places():
            if b is a or catalog.km_between(a, b) < 30:
                continue
            bad += early_transfers(a, b, modes)
    assert bad == [], bad[:10]


def test_TRAP_5_every_hub_to_suzdal_and_back():
    b = catalog.resolve("Суздаль")
    bad = [x for a in catalog.hubs() if a is not b for x in early_transfers(a, b, ["train"])]
    assert bad == [], bad[:10]


def test_day_train_back_still_leaves_at_16_20_when_the_station_is_near():
    """Москва -> Нижний Новгород: the station is 3 km away, nothing moves (#343)."""
    a, b = catalog.resolve("Москва"), catalog.resolve("Нижний Новгород")
    back = [
        o
        for o in offers_for_place(spec("Москва", "Нижний Новгород", ["train"]), b, a)
        if isinstance(o, TransportOffer) and o.origin == "Нижний Новгород"
    ]
    assert any(f"{o.depart_at:%H:%M}" == "16:20" for o in back)
