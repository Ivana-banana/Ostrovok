"""#128: a link word makes a route only right before a city; «либо» is «или».

Without a model (TRAP-1). «Сочи и Анапа на 5 ночей, потом решим» are two candidates, not a trip
through two cities: the notice is «варианты», not «маршрут». Accepted risk (not tested as a
route): «Из Москвы в Питер, потом в Казань или Сочи» stays candidates, one city is not shown.
"""

import json
from pathlib import Path

import pytest

from api import checks, model
from api.contracts import ChatRequest
from api.model import CANDIDATES, ROUTE, chat, is_route, severals
from api.store import SessionStore

CASES = json.loads((Path(__file__).resolve().parents[2] / "eval/cases.json").read_text("utf-8"))
S11 = next(c for c in CASES if c["id"] == "S11")

NOT_ROUTES = [
    ("Сочи и Анапа на 5 ночей, потом решим", ["Сочи", "Анапа"]),
    ("Сочи либо Анапа на 5 ночей", ["Сочи", "Анапа"]),
    ("Сочи либо Анапа на 5 ночей, потом решим", ["Сочи", "Анапа"]),
    ("Сочи/Анапа на 5 ночей, затем выберу", ["Сочи", "Анапа"]),
    ("Сочи, Анапа на 5 ночей, потом посмотрю цены", ["Сочи", "Анапа"]),
    ("Либо Сочи, либо Анапа, а там посмотрим", ["Сочи", "Анапа"]),
    ("Сочи и Анапа на неделю, затем сравню", ["Сочи", "Анапа"]),
    ("Сочи или Анапа, потом в Казань", ["Сочи", "Анапа", "Казань"]),  # accepted risk
]
ROUTES = [
    ("Из Москвы в Питер на 2 ночи, потом в Казань на 2 ночи", ["Санкт-Петербург", "Казань"]),
    ("Из Москвы в Питер, потом в Казань", ["Санкт-Петербург", "Казань"]),
    ("Питер, потом Казань", ["Санкт-Петербург", "Казань"]),
    ("питер, потом казань", ["Санкт-Петербург", "Казань"]),
    ("Питер, затем в Казань", ["Санкт-Петербург", "Казань"]),
    ("Поеду в Питер, а после в Казань", ["Санкт-Петербург", "Казань"]),
    ("Из Москвы в Питер, оттуда в Казань", ["Санкт-Петербург", "Казань"]),
    ("Питер, потом ещё в Казань", ["Санкт-Петербург", "Казань"]),
    ("Питер, а после этого в Казань", ["Санкт-Петербург", "Казань"]),
    ("Сочи 3 дня, Анапа 4 дня", ["Сочи", "Анапа"]),
    ("Москва — Питер — Казань", ["Санкт-Петербург", "Казань"]),
]


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


@pytest.mark.parametrize("text, dests", NOT_ROUTES)
def test_no_link_before_a_city_is_no_route(text, dests):
    assert not is_route(text, dests)
    assert severals(text, dests) == CANDIDATES


@pytest.mark.parametrize("text, dests", ROUTES)
def test_link_before_a_city_is_a_route(text, dests):
    assert is_route(text, dests)
    assert severals(text, dests) == ROUTE


@pytest.mark.parametrize(
    "text",
    [
        "Сочи и Анапа на 5 ночей, потом решим",
        "Сочи либо Анапа на 5 ночей",
        "Сочи/Анапа на 5 ночей, затем выберу",
    ],
)
def test_candidates_get_no_route_notice(store, text):
    r = chat(ChatRequest(session_id=None, message=text), store)
    assert r.plan.spec.destinations == ["Сочи", "Анапа"]
    assert not [a for a in r.plan.spec.assumptions if a.startswith("маршрут:")]
    assert [a for a in r.plan.spec.assumptions if a.startswith("варианты:")]
    assert "В плане только:" not in r.reply
    assert "Сравниваю сначала Сочи." in r.reply


@pytest.fixture
def fresh_runs(monkeypatch):
    monkeypatch.setattr(checks, "RUNS", [])


def test_s11_passes_on_the_honest_route_answer(fresh_runs):
    ok, note = checks.check_S11(S11)
    assert ok, note
    assert "все пункты выполнены" in note


def test_s11_fails_when_the_second_city_is_lost_silently(fresh_runs, monkeypatch):
    """The product before #137: no notice, no chip, no line in the assumptions."""
    monkeypatch.setattr(model, "severals", lambda message, destinations: None)
    ok, note = checks.check_S11(S11)
    assert not ok
    assert "п.1" in note and "п.2" in note and "п.4" in note
    assert "п.3" not in note  # the booking of the first city itself goes through
