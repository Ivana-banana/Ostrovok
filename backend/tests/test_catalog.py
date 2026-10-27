"""Destination catalogue (T4, #8): one case per rule, no network (conftest.py)."""

import importlib.util
import re
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from api import catalog
from api.contracts import ChatRequest, TripSpec
from api.fixtures import WARM_WATER_C
from api.guards import resolve_destination
from api.model import chat
from api.offers import offers_for_place, resolve_place
from api.store import SessionStore


def test_snapshot_has_climate_zones_and_hubs():
    places = catalog.places()
    assert 300 <= len(places) <= 500
    assert len(catalog.hubs()) >= 45 and {p["country"] for p in catalog.hubs()} == {"Россия"}
    for p in places:
        assert ZoneInfo(p["tz"]) and ZoneInfo(p["airport"]["tz"])
        c = p["climate"]
        assert len(c["air"]) == len(c["rain"]) == 12 and None not in c["air"] + c["rain"], p["name"]
        assert (c["sea"] is not None) == p["sea"], p["name"]
        assert c["sea"] is None or (len(c["sea"]) == 12 and None not in c["sea"]), p["name"]


def test_trap_5_every_place_has_a_road():
    """An airport with flights or a station: else the plan has no way there (Горно-Алтайск, B1)."""
    bad = [
        p["name"] for p in catalog.places() if p["airport_km"] is None and p["station_km"] is None
    ]
    assert bad == []
    # RGK has no scheduled_service in OurAirports: the nearest airport with flights by road
    # stands in, BAX 271 km, not NOZ 216 km by straight line and 372 km by road (#223)
    assert catalog.resolve("Горно-Алтайск")["airport"] == {
        "iata": "BAX", "km": 271, "tz": "Asia/Krasnoyarsk", "road": True
    }  # fmt: skip


def test_trap_5_altai_plan_has_a_flight_and_a_transfer(tmp_path):
    """TRAP-5: «Алтай из Москвы» in rules mode reaches the cart with the road, not a hotel alone."""
    store = SessionStore(tmp_path / "sessions.db")
    r = chat(ChatRequest(message="Хочу на Алтай в июле на неделю из Москвы, двое взрослых"), store)
    for text in ("беру", "да"):
        r = chat(ChatRequest(session_id=r.session_id, message=text), store)
    assert r.stage == "checkout" and r.llm is False
    kinds = [i.kind for i in r.cart.items]
    assert kinds.count("transport") == 2 and kinds.count("transfer") == 2
    flights = [i for i in r.cart.items if i.kind == "transport"]
    assert {(i.origin, i.destination, i.mode) for i in flights} == {
        ("Москва", "Горно-Алтайск", "flight"), ("Горно-Алтайск", "Москва", "flight")
    }  # fmt: skip
    assert any("≈272.5 км" in i.title for i in r.cart.items if i.kind == "transfer")


@pytest.mark.parametrize(
    "written,name",
    [
        ("antalya", "Анталия"),  # en alias
        ("Кишинев", "Кишинёв"),  # ё/е
        ("в Анталию", "Анталия"),  # preposition and case
        ("Антлия", "Анталия"),  # typo
        ("Барселна", "Барселона"),
    ],
)
def test_trap_3_aliases_case_and_typos(written, name):
    """TRAP-3."""
    assert catalog.resolve(written)["name"] == name


def test_trap_3_close_spelling_is_not_another_city():
    """TRAP-3: «Малага» is not «Мале»."""
    assert catalog.resolve("Малага") is None


@pytest.mark.parametrize("written", ["Гагра", "Гагры", "в Гагре", "Анья"])
def test_trap_3_gagra_is_not_agra(written):
    """TRAP-3 (#267): a name with another first letter is another place, not a typo.

    «Гагра» (not in the snapshot) is not «Агра», «Анья» is not «Ханья»: None, so the caller
    goes to the online geocoder, never to a different city.
    """
    assert catalog.resolve(written) is None


@pytest.mark.parametrize(
    "written,name",
    [
        ("Эссентуки", "Ессентуки"),  # е/э
        ("Экатеринбург", "Екатеринбург"),
        ("Иошкар-Ола", "Йошкар-Ола"),  # и/й
        ("Йркутск", "Иркутск"),
        ("Ерусалим", "Иерусалим"),  # a vowel less at the start
        ("Истамбул", "Стамбул"),  # a vowel more at the start
        ("Опорту", "Порту"),
        ("Hania", "Ханья"),  # h/c
        ("Kopenhagen", "Копенгаген"),  # k/c
        ("Jerevan", "Ереван"),  # j/y
    ],
)
def test_trap_3_first_letter_typos_still_resolve(written, name):
    """TRAP-3 (#267): the first-letter rule keeps the typos people really make."""
    assert catalog.resolve(written)["name"] == name


def test_trap_3_dropped_first_consonant_is_not_the_second_best():
    """TRAP-3 (#267): «Емал» is close to «Чемал»; it must not become «Мале»."""
    assert catalog.resolve("Емал") is None


def test_trap_3_gagra_neighbours_still_resolve():
    """TRAP-3 (#267): the exact names next to the failure keep their place."""
    assert catalog.resolve("Агра")["name"] == "Агра"
    assert catalog.resolve("в Агре")["name"] == "Агра"
    assert catalog.resolve("Томск")["name"] == "Томск"
    assert catalog.resolve("Омск")["name"] == "Омск"
    assert catalog.resolve("Санья")["name"] == "Санья"


def test_trap_3_letters_after_a_short_name_are_not_a_case_ending():
    """TRAP-3: «Гори» (Georgia) is not «Гоа» + «ри»; «в Нижнем Новгороде» still is the city."""
    assert catalog.resolve("Гори") is None
    assert catalog.resolve("в Нижнем Новгороде")["name"] == "Нижний Новгород"
    assert catalog.country_of("из Нидерландов") == "Нидерланды"


def test_trap_5_home_airport_and_zone_of_a_new_airport():
    """TRAP-5: Выборг flies from LED, not Finnish LPP; GSV (2019) is UTC+4 as Саратов, not +3."""
    assert catalog.resolve("Выборг")["airport"]["iata"] == "LED"
    assert catalog.resolve("Саратов")["airport"] == {
        "iata": "GSV",
        "km": 23,
        "tz": "Europe/Saratov",
    }


def test_trap_4_mountain_airport_is_nearest_by_road():
    """TRAP-4: Домбай flies to MRV, not to AER behind the Main Caucasus ridge (#223).

    The transfer from the airport is the OSRM road of the snapshot, not the straight line.
    """
    place = catalog.resolve("Домбай")
    road = catalog.snapshot()["roads"]["Домбай"]
    assert place["airport"]["iata"] == "MRV" and place["airport"]["road"] is True
    assert place["airport_km"] == place["airport"]["km"] == road["MRV"] < road["AER"]
    soon = date.today() + timedelta(days=60)
    spec = TripSpec(origin="Москва", date_from=soon, date_to=soon + timedelta(days=3), nights=3)
    rides = [
        float(re.search(r"≈([\d.]+) км", o.title)[1])
        for o in offers_for_place(spec, place, resolve_place("Москва"))
        if o.kind == "transfer" and "аэропорт" in o.title
    ]
    assert rides and all(road["MRV"] <= x <= road["MRV"] + 10 for x in rides)


def test_trap_4_small_road_gain_keeps_the_nearest_airport_by_line():
    """TRAP-4: Дилижан stays on EVN; LWN is 106 km by road vs 108, a gain under 15 % and 20 km (#246)."""
    place = catalog.resolve("Дилижан")
    road = catalog.snapshot()["roads"]["Дилижан"]
    assert road["LWN"] < road["EVN"] and road["EVN"] - road["LWN"] < 20
    assert place["airport"]["iata"] == "EVN" and place["airport_km"] == road["EVN"]


def test_trap_4_road_gain_is_a_share_or_km_not_both():
    """TRAP-4: 60 km by line, 48 by road is a gain of 12 km = 20 %: switch (OR, not AND) (#246)."""
    spec = importlib.util.spec_from_file_location(
        "build_catalog", Path(__file__).parents[2] / "scripts" / "build_catalog.py"
    )
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    place = {"ru": "X", "lat": 0.0, "lon": 0.0, "cc": "XX"}
    ports = [["NEAR", 0.54, 0.0, "UTC", "XX"], ["FAR", 0.0, 0.6, "UTC", "XX"]]
    switch = build.airport_fields(place, ports, {"X": {"NEAR": 60, "FAR": 48}}, None)
    keep = build.airport_fields(place, ports, {"X": {"NEAR": 60, "FAR": 55}}, None)
    assert switch["airport"]["iata"] == "FAR" and switch["airport_km"] == 48
    assert keep["airport"]["iata"] == "NEAR" and keep["airport_km"] == 60


def test_trap_6_airport_road_from_the_snapshot_else_straight_line():
    """TRAP-6: road km come from the roads snapshot in catalog.json (no network).

    A place without a road for every candidate keeps the straight line and no "road" mark.
    """
    roads = catalog.snapshot()["roads"]
    ports = {a[0]: {"lat": a[1], "lon": a[2]} for a in catalog.snapshot()["airports"]}
    for p in catalog.places():
        ap = p["airport"]
        if ap.get("road"):
            known = {k: v for k, v in roads[p["name"]].items() if v is not None}
            best = min(known.values())
            first = known[min(known, key=lambda k: catalog.km_between(p, ports[k]))]
            # the road-nearest airport, or the straight-line one when the gain is small (#246)
            assert ap["km"] == best or (
                ap["km"] == first and first - best < min(20, 0.15 * first)
            ), p["name"]
        else:
            assert ap["km"] == round(catalog.km_between(p, ports[ap["iata"]])), p["name"]


def test_country_in_text_gives_its_places():
    places = catalog.by_country("в Турцию")
    assert len(places) >= 10 and {p["country"] for p in places} == {"Турция"}
    assert catalog.resolve("в Турцию")["name"] == places[0]["name"] == "Анталия"


def test_swim_tag_is_the_warm_season_of_the_sea():
    """#224: «у моря» is not «море для купания»: Санкт-Петербург has the sea, 2 warm months only."""
    assert catalog.SWIM_WATER_C == WARM_WATER_C
    for p in catalog.places():
        assert ("swim" in p["tags"]) == catalog.swims(p["climate"]["sea"]), p["name"]
        assert "swim" not in p["tags"] or "sea" in p["tags"], p["name"]
    spb = catalog.resolve("Санкт-Петербург")
    assert spb["sea"] and "swim" not in spb["tags"]
    assert "swim" in catalog.resolve("Сочи")["tags"]


def test_find_filters_by_month_climate():
    warm = {p["name"] for p in catalog.find(month=11, sea=True, sea_min=24)}
    assert "Хургада" in warm and "Сочи" not in warm and "Анталия" not in warm


def test_departure_city_off_the_list_goes_to_nearest_hub():
    hub, note = catalog.origin_hub("Суздаль")
    assert hub["hub"] and hub["name"] != "Суздаль" and hub["name"] in note
    assert catalog.origin_hub("Казань") == (catalog.resolve("Казань"), "")


def test_trap_6_no_network_unknown_name_gives_none():
    """TRAP-6: no network, the name is not in the snapshot: None and an honest note, no error."""
    assert resolve_place("Зурбаган") is None
    hub, note = catalog.origin_hub("Зурбаган")
    assert hub["name"] == "Москва" and "не нашёл" in note


def test_trap_3_geocoder_is_asked_again_without_the_case_ending(geo):
    """TRAP-3: «в Бледе» — the geocoder knows only «Блед»."""
    place = catalog.lookup_online("Бледе", geo("Блед"))
    assert place["name"] == "Блед" and place["slug"]
    assert (
        catalog.lookup_online("Старую", geo(catalog._stems("Старую"), "Стара-Загора")) is None
    )  # not a case of it


def test_trap_2_online_place_outside_snapshot():
    """TRAP-2: a place outside the snapshot from the geocoder and one year of climate."""
    days = [f"2025-{m:02d}-15" for m in range(1, 13)]

    def fetch(url: str, params: dict) -> dict:
        if url == catalog.GEOCODE:
            return {"results": [{"id": 1, "name": "Зурбаган", "latitude": 44.6, "longitude": 33.5,
                                 "timezone": "Europe/Moscow", "country": "Россия"}]}  # fmt: skip
        if url == catalog.ARCHIVE:
            return {"daily": {"time": days, "temperature_2m_max": list(range(12)),
                              "precipitation_sum": [1.0] * 12}}  # fmt: skip
        return {"latitude": 44.6, "longitude": 33.5,
                "daily": {"time": days, "sea_surface_temperature_mean": [20.0] * 12}}  # fmt: skip

    place = catalog.lookup_online("Зурбаган", fetch)
    assert place["source"] == "online" and place["sea"] and place["airport"]["iata"]
    assert catalog.month_climate(place, 7) == (6, 20.0)
    assert resolve_place("Зурбаган") is place  # cached in the process


def _bled_with_roads(geo, road_km: list | None):
    """The online place «Блед» (46.4, 14.1); OSRM answers `road_km` to the nearest airports."""
    base = geo("Блед")

    def fetch(url: str, params: dict) -> dict:
        if not url.startswith(catalog.OSRM):
            return base(url, params)
        if road_km is None:
            raise OSError("no network")
        assert len(url.removeprefix(catalog.OSRM).split(";")) == len(road_km) + 1
        return {"code": "Ok", "distances": [[0, *[m and m * 1000 for m in road_km]]]}

    here = {"lat": 46.4, "lon": 14.1}
    ports = [{"iata": a[0], "lat": a[1], "lon": a[2]} for a in catalog.snapshot()["airports"]]
    near = sorted(ports, key=lambda a: catalog.km_between(here, a))
    return fetch, near


def test_trap_6_online_airport_straight_line_without_network_is_marked(geo):
    """TRAP-6: OSRM is down: the nearest airport by line, "road": False, the place works."""
    fetch, near = _bled_with_roads(geo, None)
    place = catalog.lookup_online("Блед", fetch)
    ap = place["airport"]
    assert ap["iata"] == near[0]["iata"] and ap["road"] is False
    assert ap["km"] == round(catalog.km_between(place, near[0]))
    assert place["airport_km"] == ap["km"]
    assert not catalog.ONLINE_CACHE.exists()  # one OSRM flake is not frozen on disk


def test_trap_2_online_airport_by_road_when_osrm_answers(geo):
    """TRAP-2: a ridge in the way (Домбай): the 2nd airport (KLU, LJU) is 40 km by road, the 1st 90."""
    fetch, near = _bled_with_roads(geo, [90, 40])
    ap = catalog.lookup_online("Блед", fetch)["airport"]
    assert ap["iata"] == near[1]["iata"] and ap["km"] == 40 and ap["road"] is True
    assert catalog.ONLINE_CACHE.exists()  # a road answer is cached with the place


@pytest.mark.parametrize("roads", [[100, 84], [200, 178]])
def test_trap_2_online_road_gain_is_a_share_or_km_not_both(geo, roads):
    """TRAP-2: only one threshold holds: 16 km = 16 % (< 20 km), 22 km = 11 % (< 15 %): switch.

    `max` of the two thresholds would keep the nearest in both cases (#246, as in the snapshot).
    """
    first, second = roads
    gain = first - second
    assert (gain >= 0.15 * first) != (gain >= 20)  # exactly one condition holds
    assert gain < max(20, 0.15 * first)  # a `max` rule would not switch
    fetch, near = _bled_with_roads(geo, roads)
    ap = catalog.lookup_online("Блед", fetch)["airport"]
    assert ap["iata"] == near[1]["iata"] and ap["km"] == second and ap["road"] is True


def test_trap_6_online_airport_one_candidate_does_not_ask_osrm(geo, monkeypatch):
    """TRAP-6: a single candidate airport: no OSRM request, the straight line."""
    monkeypatch.setattr(catalog, "ROAD_CANDIDATES", 1)
    fetch, near = _bled_with_roads(geo, [90])  # the fake asserts the URL has 2 points if asked
    asked = []

    def spy(url: str, params: dict) -> dict:
        asked.append(url)
        return fetch(url, params)

    ap = catalog.lookup_online("Блед", spy)["airport"]
    assert ap["iata"] == near[0]["iata"] and ap["road"] is False
    assert not any(u.startswith(catalog.OSRM) for u in asked)
    assert catalog.ONLINE_CACHE.exists()  # nothing flaked: the place is cached


def test_trap_2_online_airport_keeps_the_nearest_for_a_small_road_gain(geo):
    """TRAP-2: 100 km by road to the nearest, 90 to the second: a gain under 15 % and 20 km (#246)."""
    fetch, near = _bled_with_roads(geo, [100, 90])
    ap = catalog.lookup_online("Блед", fetch)["airport"]
    assert ap["iata"] == near[0]["iata"] and ap["km"] == 100 and ap["road"] is True


def test_trap_6_online_airport_without_a_road_to_the_nearest_takes_the_next(geo):
    """TRAP-6: OSRM finds no road (null) to the nearest airport: the next one by road."""
    fetch, near = _bled_with_roads(geo, [None, 70])
    ap = catalog.lookup_online("Блед", fetch)["airport"]
    assert ap["iata"] == near[1]["iata"] and ap["km"] == 70 and ap["road"] is True


def test_online_road_choice_equals_the_snapshot_rule():
    """The road constants of catalog.py mirror scripts/build_catalog.py (one rule, D-020)."""
    spec = importlib.util.spec_from_file_location(
        "build_catalog", Path(__file__).parents[2] / "scripts" / "build_catalog.py"
    )
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    for name in ("ROAD_SPAN", "ROAD_PLUS_KM", "ROAD_GAIN", "ROAD_GAIN_KM", "OSRM", "AIRPORT_KM"):
        assert getattr(catalog, name) == getattr(build, name), name


def test_trap_6_a_network_error_is_remembered_for_a_minute_only(monkeypatch):
    """TRAP-6: offline, one wait per city a minute; one timeout does not make it unknown until a restart."""
    calls = []

    def timeout(url: str, params: dict) -> dict:
        calls.append(url)
        raise TimeoutError

    def nothing(url: str, params: dict) -> dict:
        return {"results": []}

    now = 1000.0
    monkeypatch.setattr(catalog.time, "monotonic", lambda: now)
    assert catalog.lookup_online("Зурбаган", timeout) is None
    assert catalog.lookup_online("Зурбаган", timeout) is None and len(calls) == 1
    now += catalog.FAILED_S
    assert catalog.lookup_online("Зурбаган", nothing) is None and "зурбаган" in catalog._online


def test_trap_6_empty_geocoder_answer_uses_snapshot():
    """TRAP-6: the geocoder answered nothing, the snapshot has the place."""
    snap = {"Сочи": {"lat": 43.6, "lon": 39.7}}
    assert resolve_destination("Сочи", {}, lambda n: None, snapshot=snap)["source"] == "snapshot"
