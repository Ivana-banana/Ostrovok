"""Destination catalogue (T4, #8): one case per rule, no network (conftest.py)."""

from zoneinfo import ZoneInfo

import pytest

from api import catalog
from api.contracts import ChatRequest
from api.guards import resolve_destination
from api.model import chat
from api.offers import resolve_place
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
    # RGK has no scheduled_service in OurAirports: the nearest airport with flights stands in
    assert catalog.resolve("Горно-Алтайск")["airport"] == {
        "iata": "NOZ", "km": 216, "tz": "Asia/Krasnoyarsk"
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
    assert any("≈217.5 км" in i.title for i in r.cart.items if i.kind == "transfer")


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


def test_country_in_text_gives_its_places():
    places = catalog.by_country("в Турцию")
    assert len(places) >= 10 and {p["country"] for p in places} == {"Турция"}
    assert catalog.resolve("в Турцию")["name"] == places[0]["name"] == "Анталия"


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
