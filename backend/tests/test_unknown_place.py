"""#88: a city outside catalog.json in the chat path (S5, TRAP-2). Without a model (TRAP-1).

The sessions answer the way the eval driver does: the first option of a clarify question,
then «беру» / «да» / «подтверждаю»; select stops the walk.
"""

import pytest

from api import catalog
from api.contracts import ChatRequest, HotelOffer
from api.model import chat
from api.store import SessionStore

ANSWERS = {"inspire": "1", "compare": "беру", "assemble": "да", "checkout": "подтверждаю"}


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def walk(store, text, limit=8):
    r = chat(ChatRequest(session_id=None, message=text), store)
    for _ in range(limit):
        if r.stage in ("booked", "select"):
            break
        if r.stage == "clarify":
            text = " ".join(q.options[0] for q in r.questions if q.options) or "пока не знаю"
        else:
            text = ANSWERS[r.stage]
        r = chat(ChatRequest(session_id=r.session_id, message=text), store)
    return r


@pytest.mark.parametrize(
    ("text", "city"),
    [("Хочу в Лиссабон в марте", "Лиссабон"), ("поехать в Хошимин на 10 дней", "Хошимин")],
)
def test_trap_2_catalogue_city_reaches_voucher(store, text, city):
    r = walk(store, text)
    assert r.stage == "booked" and r.voucher is not None
    assert r.plan.spec.destinations == [city]


def test_trap_2_geocoder_city_reaches_voucher(store, monkeypatch, geo):
    """Тихвин is not in catalog.json; the geocoder knows it, so the chat books it."""
    assert catalog.resolve("Тихвин") is None
    monkeypatch.setattr(catalog, "_get", geo("Тихвин"))
    r = walk(store, "Хочу в Тихвин в марте")
    assert r.stage == "booked" and r.voucher is not None
    hotels = [o for o in r.plan.offers if isinstance(o, HotelOffer)]
    assert hotels and all(h.city == "Тихвин" for h in hotels)


def test_trap_2_unknown_place_says_variants_are_not_nearby(store):
    """Offline, the place has no coordinates: the reply says so and never calls the variants near."""
    r = walk(store, "Хочу в Тихвин в марте")
    assert r.stage == "select" and r.questions[0].options
    # #20: the variants are places of catalog.json, not the seven names of fixtures.py
    assert all(catalog.resolve(n)["name"] == n for n in r.questions[0].options)
    assert "Где это место, не знаю" in r.reply and "не по близости" in r.reply
