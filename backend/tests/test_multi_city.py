"""#128: a route through several cities is not planned as one trip; the second city is not lost silently.

Without a model (TRAP-1). The plan covers the first city only. The reply says so, names the lost
city, and offers one chip per city. The lost city stays in the plan as an assumption line.
Candidates («Сочи или Анапа») are no route: their reply stays as it was.
"""

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore

ROUTE = "Из Москвы в Питер на 2 ночи, потом в Казань на 2 ночи"
ROUTES = [
    ROUTE,
    "Питер на 2 ночи, затем Казань на 3 ночи",
    "Поеду в Питер, а после в Казань",
    "Из Москвы в Питер, оттуда в Казань",
    "Сочи 3 дня, Анапа 4 дня",
]
FIRST_OF = {"Сочи 3 дня, Анапа 4 дня": ("Сочи", "Анапа")}


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


@pytest.mark.parametrize("text", ROUTES)
def test_route_says_that_the_plan_covers_the_first_city_only(store, text):
    first, second = FIRST_OF.get(text, ("Санкт-Петербург", "Казань"))
    r = say(store, text)
    assert r.stage == "compare"
    assert r.plan.spec.destinations == [first, second]
    hotels = [o for o in r.plan.offers if o.kind == "hotel"]
    assert hotels
    assert all(first in o.title for o in hotels)
    assert f"Пока планирую только {first}" in r.reply
    assert f"{first} → {second}" in r.reply
    assert "пока не" in r.reply
    assert [q.options for q in r.questions] == [[f"Спланировать {first}", f"Спланировать {second}"]]
    # The lost city stays visible in the plan.
    line = next(a for a in r.plan.spec.assumptions if a.startswith("маршрут:"))
    assert second in line
    assert second in r.reply.split("Допущения:")[1]


def test_route_chip_plans_the_chosen_city_without_the_notice(store):
    r = say(store, ROUTE)
    r = say(store, r.questions[0].options[1], r.session_id)  # «Спланировать Казань»
    assert r.stage == "compare"
    assert r.plan.spec.destinations == ["Казань"]
    hotels = [o for o in r.plan.offers if o.kind == "hotel"]
    assert hotels
    assert all("Казань" in o.title for o in hotels)
    assert "Пока планирую только" not in r.reply
    assert not [a for a in r.plan.spec.assumptions if a.startswith("маршрут:")]
    assert r.questions == []


def test_route_notice_does_not_repeat_on_the_next_turn(store):
    r = say(store, ROUTE)
    r = say(store, "беру", r.session_id)
    assert r.stage == "assemble"
    assert "Пока планирую только" not in r.reply
    assert any(a.startswith("маршрут:") for a in r.plan.spec.assumptions)  # still visible


@pytest.mark.parametrize(
    "text",
    ["Сочи или Анапа", "Хочу в Сочи или Анапу после Нового года", "Из Москвы в Питер на 2 ночи"],
)
def test_candidates_and_one_city_have_no_route_notice(store, text):
    r = say(store, text)
    assert "Пока планирую только" not in r.reply
    assert not [a for a in r.plan.spec.assumptions if a.startswith("маршрут:")]
    assert r.questions == []
