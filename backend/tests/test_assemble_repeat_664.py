"""#664: the assemble reply repeats the choice first: hotel title and price from the offers."""

import pytest
from talk import say

from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def test_assemble_starts_with_the_picked_hotel(store):
    first = say(store, "Байкал на 4 ночи с 1 декабря вдвоём из Москвы")
    r = say(store, "беру второй", first.session_id)
    assert r.stage == "assemble"
    assert r.reply.startswith("Ваш выбор:")
    hotel = next(o for o in r.plan.offers if o.kind == "hotel" and o.offer_id in r.plan.selected)
    assert hotel.title in r.reply
    assert f"{round(hotel.price_rub):,}".replace(",", " ") in r.reply.replace("\xa0", " ")
    assert "План поездки:" in r.reply
