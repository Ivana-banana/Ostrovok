import pytest

from api import catalog


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    """Tests never call Open-Meteo: lookup_online sees no network, as on a demo offline (TRAP-6)."""

    def offline(url: str, params: dict) -> dict:
        raise OSError("no network in tests")

    monkeypatch.setattr(catalog, "_get", offline)
    monkeypatch.setattr(catalog, "ONLINE_CACHE", tmp_path / "online_places.json")
    monkeypatch.setattr(catalog, "_online", {})
    monkeypatch.setattr(catalog, "_failed", {})
