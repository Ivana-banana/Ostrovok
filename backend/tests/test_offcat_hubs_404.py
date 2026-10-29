"""#404: a place off the catalogue (the geocoder) near a big hub has that hub as a neighbour, as
a catalogue place has (#380). The cheap road from the hub measures the distance to the place,
not the origin: no origin question with road sums. Offline (TRAP-6): the geocoder is a stub."""

import pytest

from api import catalog, model
from api.contracts import TripSpec

VILLAGE = "Хутор Тестовый"


def _geocoder(lat: float, lon: float):
    days = [f"2025-{m:02d}-15" for m in range(1, 13)]

    def fetch(url: str, params: dict) -> dict:
        if url == catalog.GEOCODE:
            if params["name"] != VILLAGE:
                return {}
            return {"results": [{"id": 9, "name": VILLAGE, "latitude": lat, "longitude": lon,
                                 "timezone": "Europe/Moscow", "country": "Россия"}]}  # fmt: skip
        if url == catalog.ARCHIVE:
            return {"daily": {"time": days, "temperature_2m_max": [20.0] * 12,
                              "precipitation_sum": [1.0] * 12}}  # fmt: skip
        return {"latitude": 0.0, "longitude": 0.0, "daily": {}}

    return fetch


@pytest.mark.parametrize(
    ("lat", "lon", "asks"),
    [(47.0, 39.9, False), (56.0, 92.9, True)],  # near Ростов-на-Дону / far from it
)
def test_offcat_place_near_hub_excludes_that_hub(monkeypatch, lat, lon, asks):
    """TRAP-3, TRAP-6: a village near Ростов-на-Дону: its cheap road is no gap, the plan goes on."""
    monkeypatch.setattr(catalog, "_get", _geocoder(lat, lon))
    sums = {"Москва": 30_000, "Ростов-на-Дону": 1_000}  # home 30 %: under ROAD_SHARE
    monkeypatch.setattr(model, "_round_trip", lambda probe, origin: sums.get(origin))
    spec = TripSpec(destinations=[VILLAGE], month=7, adults=2, budget_rub=100_000)
    assert catalog.resolve(VILLAGE) is None
    questions = model.far_origin_question(spec)
    assert bool(questions) is asks
    if asks:
        assert "≈" in questions[0].text  # a far hub still measures the road
