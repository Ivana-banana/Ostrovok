"""#157: the climate of the trip month comes from catalog.json, and the answer shows its numbers.

Rules only, no model (TRAP-1). Air and water are monthly means of the catalogue (TRAP-4: no
invented numbers). Each sea place of fixtures.CATALOG has the water of every month.
"""

import pytest

from api import catalog
from api.contracts import ChatRequest
from api.fixtures import CATALOG
from api.model import chat, suggest_destinations
from api.store import SessionStore

UNCHECKED = "климат месяца не проверен"
JANUARY = "Хочу тёплое море в январе, недорого, 2 взрослых, из Москвы"
NOVEMBER = "Хочу тёплое море в ноябре, недорого, 2 взрослых, из Москвы"
COLD = "море холодное для купания: вода"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text):
    return chat(ChatRequest(session_id=None, message=text), store)


def names(r):
    return [d.name for d in r.plan.destinations]


def everything(store, text):
    spec = say(store, text).plan.spec
    return {d.name: d for d in suggest_destinations(spec, len(CATALOG))}


def test_trap_4_january_warm_water_ranks_above_cold_water(store):
    """TRAP-4: Хургада 22.5 °C and Дубай 21.9 °C rank above every sea of January below 21.5 °C."""
    got = list(everything(store, JANUARY))
    for cold in ("Анталия", "Сочи", "Калининград"):
        for warm in ("Хургада", "Дубай"):
            assert got.index(cold) > got.index(warm), (cold, warm, got)
    assert names(say(store, JANUARY))[:2] == ["Хургада", "Дубай"]


def test_trap_4_january_climate_is_the_catalogue_month(store):
    r = say(store, JANUARY)
    for d in r.plan.destinations:
        assert (d.air_c, d.water_c) == catalog.month_climate(catalog.resolve(d.name), 1), d.name
        assert f"воздух {d.air_c:g} °C, вода {d.water_c:g} °C" in r.reply
        assert not any(UNCHECKED in x.lower() for x in [*d.tradeoffs, *d.violates]), d.name
    assert UNCHECKED not in r.reply.lower()
    assert "вода 22.5 °C" in r.reply  # Хургада in January


def test_trap_4_january_cold_sea_is_marked_by_the_january_water(store):
    got = everything(store, JANUARY)
    for name, water in (("Анталия", 19.5), ("Сочи", 11.2), ("Калининград", 0.6)):
        assert got[name].violates == [f"{COLD} {water:g} °C"], name
    assert got["Хургада"].violates == [] and got["Дубай"].violates == []


def test_october_sea_is_marked_by_the_october_water(store):
    got = everything(store, "Хочу тёплое море в октябре, недорого, 2 взрослых, из Москвы")
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
    assert names(r) == ["Анталия", "Хургада", "Дубай", "Санкт-Петербург"]
