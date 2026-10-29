"""#677: «Выбрать» of a hotel on assemble («третий отель») changes the hotel, the stage stays.

«беру …» on assemble is a consent and fills the cart; the board sends «<ordinal> отель».
"""

import pytest
from talk import say

from api.optimizer import hotel_rows
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def _hotel(r):
    return next(o for o in r.plan.offers if o.kind == "hotel" and o.offer_id in r.plan.selected)


def test_hotel_row_on_assemble_swaps_the_hotel(store):
    first = say(store, "Байкал на 4 ночи с 1 декабря вдвоём из Москвы")
    picked = say(store, "беру второй", first.session_id)
    assert picked.stage == "assemble"
    rows = hotel_rows(picked.plan.offers, picked.plan.selected)
    assert len(rows) >= 3
    r = say(store, "третий отель", first.session_id)
    assert r.stage == "assemble"
    assert (_hotel(r).title, _hotel(r).city) == (rows[2].title, rows[2].city)
    assert not r.cart.items
    assert rows[2].title in r.reply
    assert r.plan.timeline != picked.plan.timeline
