"""#431: the phrases of the hotel filter chips of the web UI are read as the spec fields.

The chips send chat messages (web/src/hotels.ts). Each phrase must set its field in a plan
that already exists, with no new search. Without a model (TRAP-1).
"""

import pytest
from talk import say

from api.store import SessionStore

START = "Сочи на 5 ночей с 10 ноября вдвоём, самолётом из Москвы"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


@pytest.mark.parametrize(
    ("phrase", "meal"),
    [
        ("С завтраком", "BB"),
        ("С полупансионом", "HB"),
        ("С полным пансионом", "FB"),
        ("Всё включено", "AI"),
        ("Без питания", "RO"),
    ],
)
def test_meal_phrase(store, phrase, meal):
    first = say(store, START)
    second = say(store, phrase, first.session_id)
    assert second.plan.spec.meal == meal
    assert second.plan.spec.destinations == first.plan.spec.destinations


def test_refundable_phrase(store):
    first = say(store, START)
    second = say(store, "С бесплатной отменой", first.session_id)
    assert second.plan.spec.refundable is True
    assert second.plan.spec.destinations == first.plan.spec.destinations


def test_hotel_budget_per_night_phrase(store):
    first = say(store, START)
    second = say(store, "Отель до 8000 ₽ за ночь", first.session_id)
    assert second.plan.spec.hotel_budget_rub == 8000 * 5
    assert second.plan.spec.budget_rub == first.plan.spec.budget_rub
    assert second.plan.spec.destinations == first.plan.spec.destinations
