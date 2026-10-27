"""#128: a link word makes a route only right before a city; «либо» is «или».

Without a model (TRAP-1). «Сочи и Анапа на 5 ночей, потом решим» are two candidates, not a trip
through two cities: the notice is «варианты», not «маршрут». Accepted risk (not tested as a
route): «Из Москвы в Питер, потом в Казань или Сочи» stays candidates, one city is not shown.
"""

import json
from pathlib import Path

import pytest

from api import catalog, checks, model
from api.contracts import ChatRequest
from api.model import CANDIDATES, ROUTE, chat, is_route, severals
from api.offers import places
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
    ("Рим и Вена, потом венок куплю", ["Рим", "Вена"]),
    ("Сочи и Чита, потом читать отзывы буду", ["Сочи", "Чита"]),
    ("Сочи или Анапа, потом в Казань", ["Сочи", "Анапа", "Казань"]),  # accepted risk
    ("Сочи и Анапа на 5 ночей, потом поищу отель в Анапе", ["Сочи", "Анапа"]),  # named before
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
    # Routes on main: the city stands later in the clause of the link word, not right after it.
    ("Из Москвы в Питер, потом поедем в Казань", ["Санкт-Петербург", "Казань"]),
    ("Сначала Питер на 3 ночи, потом хотим в Казань", ["Санкт-Петербург", "Казань"]),
    ("Питер, затем на поезде в Казань", ["Санкт-Петербург", "Казань"]),
    ("В Питер, потом на пару дней в Казань", ["Санкт-Петербург", "Казань"]),
    ("Питер, потом заедем в Казань", ["Санкт-Петербург", "Казань"]),
    # Short names (4 letters or less): the case forms are not in the patterns.
    ("Питер, потом в Ригу", ["Санкт-Петербург", "Рига"]),
    ("Казань, потом в Уфу", ["Казань", "Уфа"]),
    ("Из Москвы в Ригу, потом в Вену", ["Рига", "Вена"]),
    # A parenthetical word right after the link.
    ("Питер, потом, может, Казань", ["Санкт-Петербург", "Казань"]),
    ("Питер, затем, наверное, в Казань", ["Санкт-Петербург", "Казань"]),
    ("Питер, потом скорее всего Казань", ["Санкт-Петербург", "Казань"]),
    ("Питер, потом, возможно, заедем в Казань", ["Санкт-Петербург", "Казань"]),
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
    assert all(f"п.{i}" in note for i in "1245")
    assert "п.3" not in note  # the booking of the first city itself goes through


@pytest.mark.parametrize(
    "text", ["Сочи и Анапа на 5 ночей, потом на море", "Сочи и Анапа на 5 ночей, потом решим"]
)
def test_trap_6_is_route_does_not_ask_the_geocoder(monkeypatch, geo, text):
    """TRAP-6: a word after the link is no place query: no network call, no «Море» of France."""
    answer = geo("море")
    calls = []

    def fetch(url, params):
        calls.append(params)
        return answer(url, params)

    monkeypatch.setattr(catalog, "_get", fetch)
    assert not is_route(text, ["Сочи", "Анапа"])
    assert severals(text, ["Сочи", "Анапа"]) == CANDIDATES
    assert calls == []
    assert not catalog.ONLINE_CACHE.exists()


def test_trap_6_the_sea_after_the_link_is_still_candidates(store, monkeypatch, geo):
    monkeypatch.setattr(catalog, "_get", geo("море"))
    r = chat(ChatRequest(session_id=None, message="Сочи и Анапа на 5 ночей, потом на море"), store)
    assert [a for a in r.plan.spec.assumptions if a.startswith("варианты:")]
    assert not [a for a in r.plan.spec.assumptions if a.startswith("маршрут:")]
    assert "В плане только:" not in r.reply


def test_s11_fails_when_the_route_notice_is_missing(fresh_runs, monkeypatch):
    """The route is taken for candidates: the second city is named, but not as a lost one."""
    monkeypatch.setattr(model, "is_route", lambda message, destinations: False)
    ok, note = checks.check_S11(S11)
    assert not ok
    assert "п.5" in note


def _forms(name: str) -> list[str]:
    """Accusative and prepositional case of a one-word name, by the simple endings."""
    if name.endswith("а"):
        return [name[:-1] + "у", name[:-1] + "е"]
    if name.endswith("я"):
        return [name[:-1] + "ю", name[:-1] + "е"]
    if name.endswith("ь"):
        return [name, name[:-1] + "и"]
    if name[-1] in "оеиыуюйэёъ":  # indeclinable or plural: the name as it is
        return [name]
    return [name, name + "е"]


def test_every_one_word_catalog_city_after_the_link_is_a_route():
    """The sweep of the reviewer: «Казань, потом в <город>» in two cases, offline."""
    lost = []
    for place in places():
        name = place["name"]
        if " " in name or name == "Казань" or "-" in name:
            continue
        for form in _forms(name):
            text = f"Казань, потом в {form}"
            if not is_route(text, ["Казань", name]):
                lost.append(text)
    assert lost == []
