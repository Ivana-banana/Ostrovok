"""#343: train hours by the rail model of the synthetic transport adapter.

Before: a day train went 170 km/h over 1.2 x the straight line, so СПб → Петрозаводск took
2 h 05 min (about 5-6 h in reality). The reference pairs below must land within ±20 % of the
time in research/domain.md and D-028. The «есть быстрее» hint shows only a real saving.
Offline, rules only (TRAP-1); hours and prices only from offers (TRAP-4).
"""

from datetime import date, timedelta

import pytest

from api import catalog
from api.contracts import TripSpec
from api.offers import transport_offers

# Fastest train there, hours: the reference and its source (research/domain.md, D-028).
PAIRS = [
    ("Москва", "Санкт-Петербург", 4.0),  # «Сапсан» 3:30-4:15 (Wikipedia after pass.rzd.ru)
    ("Москва", "Казань", 12.0),  # overnight train, team estimate
    ("Санкт-Петербург", "Петрозаводск", 5.5),  # team estimate
    ("Москва", "Сочи", 24.0),  # team estimate
]


def trains(origin: str, place: str, day: date = date(2027, 8, 10)):
    spec = TripSpec(
        origin=origin,
        destinations=[place],
        date_from=day,
        date_to=day + timedelta(days=3),
        adults=2,
        modes=["train"],
    )
    a, b = catalog.resolve(origin), catalog.resolve(place)
    found = [x for x in transport_offers(spec, a, b) if x.mode == "train"]
    assert found, (origin, place)
    return found


def hours(leg) -> float:
    return (leg.arrive_at - leg.depart_at).total_seconds() / 3600


@pytest.mark.parametrize(("origin", "place", "ref"), PAIRS)
def test_fastest_train_is_within_20_percent_of_the_reference(origin, place, ref):
    there = [x for x in trains(origin, place) if x.origin == origin]
    fastest = min(map(hours, there))
    assert 0.8 * ref <= fastest <= 1.2 * ref, (origin, place, fastest)
    back = [x for x in trains(origin, place) if x.origin == place]
    assert min(map(hours, back)) == pytest.approx(fastest, abs=0.1)  # the same rail both ways


def test_spb_petrozavodsk_is_no_longer_two_hours():
    assert min(map(hours, trains("Санкт-Петербург", "Петрозаводск"))) >= 4


def test_TRAP_4_train_price_does_not_follow_the_speed():
    """TRAP-4: the fare comes from the route length of the same table, not from the hours:
    the prices are those of main before #343."""
    assert [x.price_rub for x in trains("Москва", "Санкт-Петербург")] == [9000, 9600, 8500, 9500]
    assert [x.price_rub for x in trains("Санкт-Петербург", "Петрозаводск")] == [5200, 4900]
    assert [x.price_rub for x in trains("Москва", "Казань")] == [10300, 10500]


def _trip(key: str, hours: float, total: int):
    from types import SimpleNamespace

    hotel = SimpleNamespace(
        title=f"Отель {key}", rating=8.0, cancellation=SimpleNamespace(refundable=True)
    )
    return SimpleNamespace(
        key=key, hours=hours, total=total, hotel=hotel, slow_leg=None, slow=False
    )


@pytest.mark.parametrize(
    ("mine", "fast", "shown"),
    [
        (2.6, 2.5, False),  # #343: 0.1 h is no saving
        (5.5, 4.6, False),  # 0.9 h: under FASTER_H
        (12.0, 10.5, False),  # 1.5 h, but 12.5 %: under FASTER_SHARE
        (5.5, 4.0, True),  # 1.5 h and 27 %
    ],
)
def test_faster_hint_only_for_a_real_saving(mine, fast, shown):
    from api.optimizer import _sacrificed

    t, f = _trip("a", mine, 10_000), _trip("b", fast, 15_000)
    lines = _sacrificed(TripSpec(), t, [t, f])
    assert any("есть быстрее" in x for x in lines) is shown
