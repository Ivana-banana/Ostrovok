"""#576: «вместо Казани Нижний Новгород» replaces the city; «и ещё Y» still adds one."""

import pytest
from talk import say

from api import llm
from api.extract import extract
from api.store import SessionStore

FIRST = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


@pytest.mark.parametrize(
    "text",
    [
        "а давай вместо Казани Нижний Новгород",
        "вместо Казани в Нижний Новгород",
        "не Казань, а Нижний Новгород",
    ],
)
def test_instead_of_replaces_the_city(store, text):
    r = say(store, FIRST)
    assert r.plan.spec.destinations == ["Казань"]
    r = say(store, text, r.session_id)
    assert r.plan.spec.destinations == ["Нижний Новгород"]


def test_instead_of_beats_the_model_that_keeps_the_old_city(monkeypatch, store):
    r = say(store, FIRST)
    both = {"destinations": ["Казань", "Нижний Новгород"]}
    monkeypatch.setattr(llm, "extract_spec", lambda m, s, until=None, today=None: both)
    r = say(store, "а давай вместо Казани Нижний Новгород", r.session_id)
    assert r.plan.spec.destinations == ["Нижний Новгород"]


@pytest.mark.parametrize(
    ("text", "cities"),
    [
        ("и ещё в Нижний Новгород", ["Нижний Новгород"]),
        ("Казань и Нижний Новгород", ["Казань", "Нижний Новгород"]),
    ],
)
def test_adding_a_city_still_adds(text, cities):
    assert extract(text)[0]["destinations"] == cities
