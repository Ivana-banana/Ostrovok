"""#340 (TRAP-5): local times of legs in winter and in summer, one tz database for every run.

Ванкувер: the IANA tz database 2026d+ keeps British Columbia on UTC−7 from 2026-11-01 (no
change of clocks any more). An older system base still says UTC−8 in December. Python reads
the system base first, so stack.mk and the Dockerfile set PYTHONTZPATH="" and zoneinfo reads
the tzdata package of uv.lock. The tests fail when the base in use disagrees with that package.
"""

import importlib.resources
import io
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import tzdata

from api import catalog
from api.contracts import TripSpec
from api.offers import _complete, transport_offers

MOSCOW = catalog.resolve("Москва")
# Places outside the catalogue: offers work for any coordinates and zone (TRAP-2).
EXTRA = {
    "Ванкувер": {"name": "Ванкувер", "lat": 49.28, "lon": -123.12, "tz": "America/Vancouver"},
    "Сидней": {"name": "Сидней", "lat": -33.87, "lon": 151.21, "tz": "Australia/Sydney"},
    "Сантьяго": {"name": "Сантьяго", "lat": -33.45, "lon": -70.67, "tz": "America/Santiago"},
}
JAN, JUL = date(2027, 1, 15), date(2027, 7, 15)


def _place(name: str) -> dict:
    return _complete(EXTRA.get(name) or catalog.resolve(name))


def _flights(name: str, day: date) -> list:
    spec = TripSpec(
        origin="Москва",
        destinations=[name],
        date_from=day,
        date_to=day + timedelta(days=7),
        adults=2,
        modes=["flight"],
    )
    found = transport_offers(spec, _place("Москва"), _place(name))
    assert found, name
    return found


def _package_zone(key: str) -> ZoneInfo:
    """The zone straight from the tzdata package, bypassing any system base."""
    path = importlib.resources.files("tzdata").joinpath("zoneinfo", *key.split("/"))
    return ZoneInfo.from_file(io.BytesIO(path.read_bytes()), key=key)


def test_TRAP_5_tz_base_is_the_pinned_tzdata_package():
    assert tzdata.IANA_VERSION >= "2026d"  # the release with British Columbia on UTC−7
    for key in ("America/Vancouver", "America/New_York", "Europe/London", "Europe/Moscow"):
        for day in (JAN, JUL, date(2026, 12, 5)):
            at = datetime.combine(day, datetime.min.time())
            used = at.replace(tzinfo=ZoneInfo(key)).utcoffset()
            assert used == at.replace(tzinfo=_package_zone(key)).utcoffset(), (key, day)


def test_TRAP_5_vancouver_december_2026_is_utc_minus_7():
    for leg in _flights("Ванкувер", date(2026, 12, 5)):
        at = leg.arrive_at if leg.destination == "Ванкувер" else leg.depart_at
        assert at.utcoffset() == timedelta(hours=-7)
    # 05.12 16:55 МСК + 10 h 45 min = 00:40 UTC = 17:40 in Vancouver (UTC−7).
    late = next(x for x in _flights("Ванкувер", date(2026, 12, 5)) if x.arrive_at.hour == 17)
    assert f"{late.depart_at:%d.%m %H:%M}" == "05.12 16:55"
    assert late.arrive_at.astimezone(UTC) == datetime(2026, 12, 6, 0, 40, tzinfo=UTC)
    # A year before the change the same city was UTC−8 in December.
    assert datetime(2025, 12, 5, tzinfo=ZoneInfo("America/Vancouver")).utcoffset() == timedelta(
        hours=-8
    )


@pytest.mark.parametrize(
    ("name", "winter", "summer"),
    [
        ("Нью-Йорк", -5, -4),
        ("Лондон", 0, 1),
        ("Сидней", 11, 10),  # southern hemisphere: summer time in January
        ("Сантьяго", -3, -4),
        ("Москва", 3, 3),
        ("Дубай", 4, 4),
    ],
)
def test_TRAP_5_winter_and_summer_offsets_of_the_place(name, winter, summer):
    for day, hours in ((JAN, winter), (JUL, summer)):
        if name == "Москва":
            at = datetime.combine(day, datetime.min.time(), ZoneInfo(MOSCOW["tz"]))
            assert at.utcoffset() == timedelta(hours=hours)
            continue
        for leg in _flights(name, day):
            at = leg.arrive_at if leg.destination == name else leg.depart_at
            assert at.utcoffset() == timedelta(hours=hours), (name, day)
            assert leg.depart_at.utcoffset() == timedelta(hours=3) or leg.origin == name


@pytest.mark.parametrize("day", [JAN, JUL])
def test_TRAP_5_sweep_every_catalogue_place_local_equals_utc_by_zoneinfo(day):
    """Every leg from Москва: local arrival and departure are the UTC instant shown in the
    zone of the place by zoneinfo, and the leg lasts the same in UTC and on the wall clock
    corrected by the offsets."""
    checked = 0
    for p in catalog.places():
        if p is MOSCOW:
            continue
        spec = TripSpec(
            origin="Москва",
            destinations=[p["name"]],
            date_from=day,
            date_to=day + timedelta(days=7),
            adults=2,
        )
        for leg in transport_offers(spec, _place("Москва"), _place(p["name"])):
            there = leg.destination == p["name"]
            zone = ZoneInfo(p["tz"])
            local = leg.arrive_at if there else leg.depart_at
            by_zoneinfo = local.astimezone(UTC).astimezone(zone)
            assert str(local.tzinfo) == p["tz"], p["name"]
            wall = (local.replace(tzinfo=None), local.utcoffset())
            assert wall == (by_zoneinfo.replace(tzinfo=None), by_zoneinfo.utcoffset()), p["name"]
            assert leg.arrive_at > leg.depart_at
            checked += 1
    assert checked > 400
