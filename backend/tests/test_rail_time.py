"""#343: train hours by the rail model of the synthetic transport adapter.

Before: a day train went 170 km/h over 1.2 x the straight line, so СПб → Петрозаводск took
2 h 05 min (about 5-6 h in reality). The reference pairs below must land within ±20 % of the
time in research/domain.md and D-028. The «есть быстрее» hint shows only a real saving.
Offline, rules only (TRAP-1); hours and prices only from offers (TRAP-4).
"""

from datetime import date, time, timedelta

import pytest

from api import catalog
from api.contracts import TripSpec
from api.offers import DAY_ARRIVE_BY, transport_offers

# Fastest train there, hours: the reference and its source (research/domain.md, D-028).
PAIRS = [
    ("Москва", "Санкт-Петербург", 4.0),  # «Сапсан» 3:30-4:15 (Wikipedia after pass.rzd.ru)
    ("Москва", "Казань", 12.0),  # overnight train, team estimate
    ("Санкт-Петербург", "Петрозаводск", 5.5),  # team estimate
    ("Москва", "Сочи", 24.0),  # team estimate
    ("Москва", "Нижний Новгород", 3.9),  # 3:55 (Wikipedia «Сапсан»); 2.8 h before the fix
    ("Москва", "Владимир", 1.8),  # 1:46 (Wikipedia «Сапсан»)
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
    the prices are those of offers.TRAIN_FARE calibrated to tutu.ru (#280)."""
    assert [x.price_rub for x in trains("Москва", "Санкт-Петербург")] == [5300, 5500, 5000, 5500]
    assert [x.price_rub for x in trains("Санкт-Петербург", "Петрозаводск")] == [3800, 3600]
    assert [x.price_rub for x in trains("Москва", "Казань")] == [6000, 6100]


def _trip(key: str, hours: float, total: int):
    from types import SimpleNamespace

    hotel = SimpleNamespace(
        title=f"Отель {key}",
        rating=8.0,
        cancellation=SimpleNamespace(refundable=True),
        synthetic=True,
        kind="hotel",
    )
    return SimpleNamespace(
        key=key, hours=hours, total=total, hotel=hotel, offers=[hotel], slow_leg=None, slow=False
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


@pytest.mark.parametrize("suburb", ["Петергоф", "Кронштадт", "Сестрорецк"])
def test_suburb_of_a_corridor_end_rides_the_fast_line(suburb):
    """#343: a place near Санкт-Петербург goes by «Сапсан» and a local train, not 75 km/h."""
    spb = min(map(hours, trains("Москва", "Санкт-Петербург")))
    assert min(map(hours, trains("Москва", suburb))) <= spb + 1.5


def overnight(leg) -> bool:
    """The overnight train of offers.py: there it comes at 07:10, back it leaves at 21:40."""
    return leg.arrive_at.timetz().replace(tzinfo=None) == time(7, 10) or (
        leg.depart_at.timetz().replace(tzinfo=None) == time(21, 40)
    )


def test_TRAP_5_pskov_day_train_back_comes_home_by_23():
    """The reviewer's case: «13.08 16:20 Псков → Москва | 14.08 02:05» before the fix."""
    day = [x for x in trains("Москва", "Псков") if not overnight(x)]
    assert day
    for leg in day:
        assert leg.arrive_at.date() == leg.depart_at.date(), leg.title
        assert leg.arrive_at.timetz().replace(tzinfo=None) <= DAY_ARRIVE_BY, leg.title


def test_TRAP_5_sweep_no_day_train_arrives_at_night():
    """Every hub x place with a station: a day train (not the overnight one) comes between 05:00 and DAY_ARRIVE_BY local time, both ways; a way back exists."""
    checked = 0
    for a in catalog.hubs():
        for b in catalog.places():
            if a is b or a["station_km"] is None or b["station_km"] is None:
                continue
            if not 30 <= catalog.km_between(a, b) <= 800 / 1.2:
                continue
            spec = TripSpec(
                origin=a["name"],
                destinations=[b["name"]],
                date_from=date(2027, 8, 10),
                date_to=date(2027, 8, 13),
                adults=2,
                modes=["train"],
            )
            legs = [x for x in transport_offers(spec, a, b) if x.mode == "train"]
            assert any(x.origin == b["name"] for x in legs), (a["name"], b["name"])
            for leg in legs:
                if overnight(leg):
                    continue
                end = leg.arrive_at.timetz().replace(tzinfo=None)
                assert leg.arrive_at.date() == leg.depart_at.date(), leg.title
                assert time(5, 0) <= end <= DAY_ARRIVE_BY, (leg.title, end)
                checked += 1
    assert checked > 1000
