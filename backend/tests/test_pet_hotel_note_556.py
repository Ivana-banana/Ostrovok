"""#556: the assemble text (the «Оформляем?» step) reminds of the pet when the chosen hotel
has no mark for animals."""

import pytest
from talk import say

from api.model import NO_PET_HOTEL
from api.store import SessionStore

KAZAN = "Казань на выходные 12–14 декабря, из Москвы, двое взрослых"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def hotels(r):
    return [o for o in r.plan.offers if o.kind == "hotel" and o.offer_id in r.plan.selected]


def test_trap5_assemble_text_reminds_of_the_pet_when_the_hotel_has_no_mark(store):
    r = say(store, KAZAN)
    say(store, "с нами едет собака", r.session_id)
    plan = say(store, "1", r.session_id)
    assert (
        plan.stage == "assemble" and hotels(plan) and not any(h.pets_allowed for h in hotels(plan))
    )
    assert NO_PET_HOTEL in plan.reply
    assert plan.reply.index(NO_PET_HOTEL) < plan.reply.index("Оформляем?")


def test_trap5_assemble_text_has_no_pet_reminder_without_a_pet(store):
    r = say(store, KAZAN)
    plan = say(store, "1", r.session_id)
    assert plan.stage == "assemble" and hotels(plan)
    assert NO_PET_HOTEL not in plan.reply
