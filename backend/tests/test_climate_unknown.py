"""#157: the climate of a month other than November is not checked, and the answer says so.

Rules only, no model (TRAP-1). The data has the November climate only (TRAP-4: no invented numbers).
"""

import pytest

from api.contracts import ChatRequest
from api.fixtures import CATALOG
from api.model import chat, suggest_destinations
from api.store import SessionStore

UNCHECKED = "климат месяца не проверен"
JANUARY = "Хочу тёплое море в январе, недорого, 2 взрослых, из Москвы"
NOVEMBER = "Хочу тёплое море в ноябре, недорого, 2 взрослых, из Москвы"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text):
    return chat(ChatRequest(session_id=None, message=text), store)


def names(r):
    return [d.name for d in r.plan.destinations]


def test_trap_4_january_sea_does_not_rank_cold_cities_first(store):
    """TRAP-4: no data for January must not read as «passes the water check»."""
    spec = say(store, JANUARY).plan.spec
    got = [d.name for d in suggest_destinations(spec, len(CATALOG))]
    for cold in ("Калининград", "Сочи"):
        for warm in ("Анталия", "Хургада", "Дубай"):
            assert got.index(cold) > got.index(warm), (cold, warm, got)
    assert names(say(store, JANUARY))[0] not in ("Калининград", "Сочи")


def test_trap_4_january_sea_says_the_month_is_not_checked(store):
    r = say(store, JANUARY)
    for d in r.plan.destinations:
        assert d.air_c is None and d.water_c is None
        assert not any(UNCHECKED in x.lower() for x in [*d.tradeoffs, *d.violates]), d.name
    assert r.reply.lower().count(UNCHECKED) == 1  # one common line, not one per city


def test_trap_4_january_cold_sea_is_marked_by_the_november_water(store):
    r = say(store, JANUARY)
    cold = [d for d in r.plan.destinations if d.name in ("Калининград", "Сочи")]
    for d in cold:
        assert any("море холодное" in v and "в январе не теплее" in v for v in d.violates)


def test_november_sea_unchanged(store):
    r = say(store, NOVEMBER)
    for d in r.plan.destinations:
        assert not any(UNCHECKED in x.lower() for x in [*d.tradeoffs, *d.violates])
    assert UNCHECKED not in r.reply.lower()
    assert names(r) == ["Анталия", "Хургада", "Дубай", "Калининград"]
