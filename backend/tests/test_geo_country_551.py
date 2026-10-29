"""#551 (TRAP-2): a country outside the catalogue is no point: the reply asks for its city."""

import pytest

from api import catalog
from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def country_geo(geo):
    base = geo("Новая Зеландия", country="Новая Зеландия")

    def fetch(url: str, params: dict) -> dict:
        out = base(url, params)
        for r in out.get("results", []):
            r["feature_code"] = "PCLI"  # Open-Meteo marks a country record so
        return out

    return fetch


def test_trap_2_country_record_is_no_place(geo):
    assert catalog.lookup_online("Новая Зеландия", country_geo(geo)) is None
    assert catalog.country_record("Новая Зеландия") == "Новая Зеландия"


def test_trap_2_country_outside_the_catalogue_asks_for_a_city(geo, monkeypatch, store):
    monkeypatch.setattr(catalog, "_get", country_geo(geo))
    r = chat(
        ChatRequest(message="Из Москвы в Новую Зеландию в ноябре на неделю, двое взрослых"), store
    )
    assert "это страна" in r.reply
    assert not r.plan.offers
    assert any("Какой город страны «Новая Зеландия»" in q.text for q in r.questions)
