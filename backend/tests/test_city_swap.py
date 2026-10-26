"""#93: the agent never books a city the user did not name or explicitly choose (S5, TRAP-2).

Without a model (TRAP-1). The sessions answer the way the eval driver does: the first option
of a clarify question, then «1» / «беру» / «да» / «подтверждаю».
"""

import pytest

from api.contracts import ChatRequest, HotelOffer
from api.extract import extract
from api.fixtures import CATALOG
from api.model import chat
from api.store import SessionStore

ANSWERS = {"inspire": "1", "compare": "беру", "assemble": "да", "checkout": "подтверждаю"}
HANOI = "Ханой на 8 ночей, двое взрослых, бюджет 120 тысяч"
OFF_CATALOG = [
    (HANOI, "Ханой"),
    ("Хочу в Лиссабон в марте", "Лиссабон"),
    ("Хошимин на 10 дней", "Хошимин"),
    ("Ухта на выходные", "Ухта"),
    ("Ханой на неделю", "Ханой"),
]


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def walk(store, text, limit=8):
    """Like the eval driver: stops on select or booked; clarify takes the first option."""
    r = say(store, text)
    for _ in range(limit):
        if r.stage in ("booked", "select"):
            break
        if r.stage == "clarify":
            text = " ".join(q.options[0] for q in r.questions if q.options) or "пока не знаю"
        else:
            text = ANSWERS[r.stage]
        r = say(store, text, r.session_id)
    return r


@pytest.mark.parametrize(("text", "city"), OFF_CATALOG)
def test_trap_2_unknown_first_word_is_the_destination(text, city):
    assert extract(text)[0]["destinations"] == [city]


@pytest.mark.parametrize(
    "text",
    ["Домой на 3 дня", "Отдыхаем на 5 дней", "Перенеси на выходные", "Подвинь на 2 дня"],
)
def test_trap_2_ordinary_first_word_is_no_destination(text):
    assert "destinations" not in extract(text)[0]


@pytest.mark.parametrize(("text", "city"), OFF_CATALOG)
def test_trap_2_unknown_city_is_not_swapped_for_a_catalogue_city(store, text, city):
    r = walk(store, text)
    assert r.stage == "select" and r.voucher is None and r.cart.items == []
    assert r.plan.spec.destinations == [city]
    assert city in r.reply and "нет в каталоге" in r.reply
    options = r.questions[0].options
    assert 2 <= len(options) <= 3 and set(options) <= set(CATALOG)
    # Anything but a pick keeps the named city and books nothing.
    again = say(store, "подтверждаю", r.session_id)
    assert again.stage == "select" and again.plan.spec.destinations == [city]
    assert again.voucher is None and again.cart.items == []


def test_trap_2_explicit_pick_is_said_in_the_reply(store):
    r = walk(store, HANOI)
    name = r.questions[0].options[0]
    picked = say(store, "1", r.session_id)
    assert picked.stage == "compare" and picked.plan.spec.destinations == [name]
    assert "Ханой" in picked.reply and name in picked.reply
    hotels = [o for o in picked.plan.offers if isinstance(o, HotelOffer)]
    assert hotels and all(h.city == name for h in hotels)
