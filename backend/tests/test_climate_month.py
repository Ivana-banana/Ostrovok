"""#157: the climate of the trip month comes from catalog.json, and the answer shows its numbers.

Rules only, no model (TRAP-1). Air and water are monthly means of the catalogue (TRAP-4: no
invented numbers). Inspiration ranks the whole catalogue (#20); a cold sea is checked on the
card of a named place (inspire.card), as it no longer reaches the warm list.
"""

import pytest
import talk

from api import catalog
from api.contracts import TripSpec
from api.fixtures import WARM_WATER_C, future_date
from api.inspire import card
from api.model import suggest_destinations
from api.store import SessionStore

UNCHECKED = "климат месяца не проверен"
JANUARY = "Хочу тёплое море в январе, недорого, 2 взрослых, из Москвы"
NOVEMBER = "Хочу тёплое море в ноябре, недорого, 2 взрослых, из Москвы"
COLD = "море холодное для купания: вода"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text):
    """#630: the open slots get «Не важно, решите сами» (talk.say)."""
    return talk.say(store, text)


def names(r):
    return [d.name for d in r.plan.destinations]


def everything(store, text):
    spec = say(store, text).plan.spec
    return {d.name: d for d in suggest_destinations(spec, 7)}


def cards(month: int, *names: str) -> dict:
    """Cards of named places for a warm-sea request of the month, from Москва."""
    spec = TripSpec(origin="Москва", month=month, wishes=["море"])
    spec = spec.model_copy(update={"date_from": future_date(month, 15)})
    hub = catalog.resolve("Москва")
    return {n: card(spec, hub, catalog.resolve(n)) for n in names}


def test_trap_4_january_warm_water_ranks_above_cold_water(store):
    """TRAP-4: in January only seas from 21.5 °C are shown; Анталия 19.5 °C, Сочи, Калининград not."""
    got = everything(store, JANUARY)
    assert not {"Анталия", "Сочи", "Калининград"} & set(got), list(got)
    for d in got.values():
        assert d.water_c >= WARM_WATER_C and d.violates == [], d.name
    assert "Хургада" in names(say(store, JANUARY))  # 22.5 °C, the cheapest warm sea


def test_trap_4_january_climate_is_the_catalogue_month(store):
    r = say(store, JANUARY)
    for d in r.plan.destinations:
        assert (d.air_c, d.water_c) == catalog.month_climate(catalog.resolve(d.name), 1), d.name
        assert f"днём {d.air_c:g} °C, вода {d.water_c:g} °C" in r.reply
        assert not any(UNCHECKED in x.lower() for x in [*d.tradeoffs, *d.violates]), d.name
    assert UNCHECKED not in r.reply.lower()
    assert "вода 22.5 °C" in r.reply  # Хургада in January


def test_trap_4_january_cold_sea_is_marked_by_the_january_water():
    got = cards(1, "Анталия", "Сочи", "Калининград", "Хургада", "Дубай")
    for name, water in (("Анталия", 19.5), ("Сочи", 11.2), ("Калининград", 0.6)):
        assert got[name].violates == [f"{COLD} {water:g} °C"], name
    assert got["Хургада"].violates == [] and got["Дубай"].violates == []


def test_october_sea_is_marked_by_the_october_water():
    got = cards(10, "Калининград", "Сочи", "Анталия")
    assert got["Калининград"].violates == [f"{COLD} 12.7 °C"]
    assert got["Сочи"].violates == [f"{COLD} 21.1 °C"]
    assert got["Анталия"].violates == []  # 26.1 °C in October
    for d in got.values():
        assert not any("в ноябре" in v or "не теплее" in v for v in d.violates), d.name


def test_november_sea_unchanged(store):
    r = say(store, NOVEMBER)
    for d in r.plan.destinations:
        assert not any(UNCHECKED in x.lower() for x in [*d.tradeoffs, *d.violates])
    assert UNCHECKED not in r.reply.lower()
    assert 2 <= len(r.plan.destinations) <= 4
    assert all(d.water_c >= WARM_WATER_C and not d.violates for d in r.plan.destinations)
