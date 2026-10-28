import os
from collections.abc import Callable

import pytest

from api import catalog

# Tests never call Travelpayouts (TRAP-6): api.main picks the real adapter by this token at
# import, and test modules import api.main after this file.
os.environ.pop("TRAVELPAYOUTS_TOKEN", None)
# Same for the live 101hotels pricing (#511, TRAP-6): api.main installs it by this flag at import
# (on by default). The suite stays offline and deterministic; the adapter is tested directly in
# test_live_hotels.py.
os.environ["LIVE_HOTELS"] = "0"


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    """Tests never call Open-Meteo: lookup_online sees no network, as on a demo offline (TRAP-6)."""

    def offline(url: str, params: dict) -> dict:
        raise OSError("no network in tests")

    monkeypatch.setattr(catalog, "_get", offline)
    monkeypatch.setattr(catalog, "ONLINE_CACHE", tmp_path / "online_places.json")
    monkeypatch.setattr(catalog, "_online", {})
    monkeypatch.setattr(catalog, "_failed", {})


@pytest.fixture
def geo():
    """Open-Meteo with one place: the geocoder answers `name` (default `found`) only to `found`."""

    def make(found: str, name: str = "", country: str = "Словения") -> Callable[[str, dict], dict]:
        days = [f"2025-{m:02d}-15" for m in range(1, 13)]

        def fetch(url: str, params: dict) -> dict:
            if url == catalog.GEOCODE:
                if params["name"] != found:
                    return {}
                return {"results": [{"id": 7, "name": name or found, "latitude": 46.4, "longitude": 14.1,
                                     "timezone": "Europe/Ljubljana", "country": country}]}  # fmt: skip
            if url == catalog.ARCHIVE:
                return {"daily": {"time": days, "temperature_2m_max": [10.0] * 12,
                                  "precipitation_sum": [1.0] * 12}}  # fmt: skip
            return {"latitude": 0.0, "longitude": 0.0, "daily": {}}

        return fetch

    return make
