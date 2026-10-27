"""#20: inspiration over the whole catalog.json — theme, region, climate, order, price calls.

Rules only, no model (TRAP-1). The requests are the jury and operator comments of the issue.
"""

import pytest

from api import catalog, inspire
from api.contracts import ChatRequest, TripSpec
from api.fixtures import WARM_WATER_C, future_date
from api.model import chat
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def inspired(store, text):
    r = chat(ChatRequest(session_id=None, message=text), store)
    assert r.stage == "inspire" and r.plan.spec.destinations == [], r.reply
    assert 2 <= len(r.plan.destinations) <= 4
    return r.plan.destinations


def tags(name: str) -> list[str]:
    return catalog.resolve(name)["tags"]


def test_trap_2_warm_sea_in_january_has_no_cold_sea(store):
    """Jury: «тёплое море в январе» gave Сочи and Калининград without a violation."""
    dests = inspired(store, "тёплое море в январе, недорого")
    assert not {"Сочи", "Калининград"} & {d.name for d in dests}
    assert all(d.water_c >= WARM_WATER_C and not d.violates for d in dests)


def test_trap_2_ski_in_february_gives_ski_resorts(store):
    """Jury: «горнолыжка в феврале» gave Казань and Петербург."""
    dests = inspired(store, "горнолыжка в феврале")
    assert sum("ski" in tags(d.name) for d in dests) >= 2


def test_trap_2_mountains_in_summer_with_a_dog(store):
    """Jury: «в горы летом с собакой» gave Казань, Петербург, Калининград."""
    dests = inspired(store, "в горы летом с собакой")
    assert all("mountains" in tags(d.name) for d in dests)


def test_europe_is_a_region_not_a_city(store):
    """Operator (#88): «в Европу» is inspiration, not «нет в каталоге»."""
    dests = inspired(store, "хочу в Европу в мае на неделю")
    assert all(d.country in inspire.EUROPE for d in dests)


def test_south_is_south_of_the_departure_city(store):
    dests = inspired(store, "на юг в ноябре, недорого")
    moscow = catalog.resolve("Москва")
    assert all(d.lat <= moscow["lat"] - inspire.SOUTH_DEG for d in dests)


def test_cards_that_break_the_request_go_below():
    """Operator: a card marked «Нарушает требования» goes below the fitting ones."""
    spec = TripSpec(origin="Москва", month=11, wishes=["море"], max_travel_hours=4)
    every = inspire.suggest_destinations(spec, 10)
    marks = [bool(d.violates) for d in every]
    assert marks == sorted(marks) and not marks[0]
    # Белек and Анталия are 30 km apart, yet both fit: no breaking card takes the slot of one
    fitting = sum(not d.violates for d in every)
    assert sum(not d.violates for d in inspire.suggest_destinations(spec)) == min(fitting, 4)


def test_card_over_the_budget_is_marked():
    spec = TripSpec(origin="Москва", month=11, wishes=["море"], budget_rub=1000)
    spec = spec.model_copy(update={"date_from": future_date(11, 15)})
    for d in inspire.suggest_destinations(spec):
        assert any(v.startswith("дороже бюджета") for v in d.violates), d.name


def test_shown_places_are_apart_and_two_per_country_at_most():
    spec = TripSpec(origin="Москва", month=1, wishes=["море"])
    dests = inspire.suggest_destinations(spec)
    for i, a in enumerate(dests):
        assert sum(b.country == a.country for b in dests) <= inspire.SHOWN_PER_COUNTRY
        for b in dests[i + 1 :]:
            assert catalog.km_between(vars(a), vars(b)) >= inspire.SPREAD_KM, (a.name, b.name)


def test_only_the_candidates_are_priced(monkeypatch):
    """445 places are not priced on each request: only CANDIDATES of them."""
    calls = []
    monkeypatch.setattr(inspire, "find_offers", lambda spec, name: calls.append(name) or [])
    inspire.suggest_destinations(TripSpec(origin="Москва", month=7, wishes=["горы"]), 7)
    assert 0 < len(calls) <= inspire.CANDIDATES


def test_reason_and_minuses_come_from_the_catalogue():
    spec = TripSpec(origin="Москва", month=2, wishes=["лыжи"])
    for d in inspire.suggest_destinations(spec):
        assert d.why.startswith("горнолыжный курорт") and d.tradeoffs, d.name


def test_no_trip_goes_below_a_cold_sea_and_a_cold_sea_below_no_sea():
    """S5: «с собакой на машине к морю» in November: a car trip to a cold sea comes first,
    not a warm sea that a car does not reach and not a town without a sea."""
    spec = TripSpec(origin="Москва", month=11, wishes=["море"], modes=["car"], pets=True)
    spec = spec.model_copy(update={"date_from": future_date(11, 7)})
    first = inspire.suggest_destinations(spec)[0]
    assert inspire.NO_PETS not in first.violates and inspire.NO_SEA not in first.violates
    assert not any(v.endswith(inspire.NO_WAY) for v in first.violates), first.violates
