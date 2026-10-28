"""#575: the README re-plan «с нами едет собака» on a trip by plane keeps the plan. A plane
takes a small pet as a condition of the airline, not a refusal; a real dead end offers places
that keep the wishes (the sea)."""

import pytest
from talk import say

from api.model import _pet_neighbours
from api.store import SessionStore

SEA = "Хочу к тёплому морю в ноябре, недорого, с ребёнком 5 лет. Летим из Москвы."


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def chosen(r):
    return [o for o in r.plan.offers if o.offer_id in r.plan.selected]


def test_trap4_readme_dog_on_a_flight_keeps_the_plan_and_names_the_condition(store):
    r = say(store, SEA)
    picked = say(store, "Анталия", r.session_id)
    assert any(o.kind == "transport" and o.mode == "flight" for o in chosen(picked))
    later = say(store, "давай на два дня позже", r.session_id)
    dog = say(store, "с нами едет собака", r.session_id)
    assert dog.plan.spec.pets and dog.plan.spec.destinations == later.plan.spec.destinations
    kinds = {o.kind for o in chosen(dog)}
    assert {"hotel", "transport"} <= kinds and dog.stage != "select"
    assert all(o.pets_allowed for o in chosen(dog) if o.kind in ("hotel", "transport"))
    assert "уточнить у авиакомпании" in dog.reply
    assert "животных не берут" not in dog.reply


def test_trap4_without_a_pet_the_flight_names_no_condition(store):
    r = say(store, SEA)
    picked = say(store, "Анталия", r.session_id)
    assert "авиакомпании" not in picked.reply
    assert not any("авиакомпании" in o.title for o in picked.plan.offers)


def test_trap2_dead_end_neighbours_keep_the_sea_wish(store):
    r = say(store, SEA)
    spec = r.plan.spec.model_copy(update={"pets": True, "destinations": ["Сочи"]})
    near = [p for _, p in _pet_neighbours(spec)]
    assert near and all(p.get("sea") for p in near)  # the nearest one was Красная Поляна
