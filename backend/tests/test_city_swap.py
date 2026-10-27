"""#93: the agent never books a city the user did not name or explicitly choose (S5, TRAP-2).

Without a model (TRAP-1). The phrases of Ханой, Лиссабон, Хошимин (from eval/cases_unseen.json) and
Сестрорецк stand here with other cities: catalog.json (#8) has those four. Every city below is not in it.
The sessions answer the way the eval driver does: the first option
of a clarify question, then «1» / «беру» / «да» / «подтверждаю».
"""

import pytest

from api.contracts import ChatRequest, HotelOffer
from api.extract import extract
from api.fixtures import CATALOG
from api.model import chat
from api.store import SessionStore

ANSWERS = {"inspire": "1", "compare": "беру", "assemble": "да", "checkout": "подтверждаю"}
HANOI = "Кандалакша на 8 ночей, двое взрослых, бюджет 120 тысяч"
# Table (a): real cities outside the catalogue become the destination, then select.
OFF_CATALOG = [
    (HANOI, "Кандалакша"),
    ("Хочу в Тихвин в марте", "Тихвин"),
    ("Котлас на 10 дней", "Котлас"),
    ("Ухта на 3 дня", "Ухта"),
    ("Кандалакша, 5 ночей", "Кандалакша"),
    ("Вытегра в июле", "Вытегра"),
    ("Вытегра на 3 дня", "Вытегра"),
    ("Большеречье на 3 дня", "Большеречье"),
    ("Уренгой на 3 дня", "Уренгой"),
    ("Друскининкай на 3 дня", "Друскининкай"),
    ("Друскининкай в июле", "Друскининкай"),
    ("Мамоново на 3 дня", "Мамоново"),
    ("Подпорожье на 3 дня", "Подпорожье"),
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


# Table (b): ordinary first words are no city; the session goes on as on main (clarify, inspire).
ORDINARY = [
    "Отдых на неделю на море, недорого",
    "Отдых на выходные с детьми",
    "Каникулы на неделю с ребёнком",
    "Завтра на выходные куда-нибудь",
    "Город на выходные недалеко от Москвы",
    "Поход на выходные",
    "Рыбалка на неделю",
    "Природа на выходные",
    "Экскурсия на выходные",
    "Выходной на 2 дня",
    "Подругой на 3 дня",
    "Мамой на 3 дня",
    "Сыном на 3 дня в мае",
    "Детьми на 3 дня в мае",
    "Родителями на 3 дня в мае",
    "Коллегами на 3 дня в мае",
    "Небольшой на 2 ночи",
    "Другой на 3 ночи",
    "Простой в мае",
    "Мой на 3 дня",
    "Такой на 3 дня",
    "Домой на 3 дня",
    "Отдыхаем на 5 дней",
    "Перенеси на выходные",
    "Подвинь на 2 дня",
]


@pytest.mark.parametrize("text", ORDINARY)
def test_trap_2_ordinary_first_word_is_no_destination(store, text):
    assert "destinations" not in extract(text)[0]
    r = say(store, text)
    assert r.stage in ("clarify", "inspire") and r.plan.spec.destinations == []


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
    assert "Кандалакша" in picked.reply and name in picked.reply
    hotels = [o for o in picked.plan.offers if isinstance(o, HotelOffer)]
    assert hotels and all(h.city == name for h in hotels)


NO_DEST = "направление: не названо — если имели в виду город, напишите «в <город>»"


def test_trap_2_vetoed_first_word_gives_neutral_line_in_inspire(store):
    """#109: «Мамой на 3 дня» is no city; the reply says so without quoting the word."""
    r = say(store, "Мамой на 3 дня, в мае, бюджет 100 тысяч")
    assert r.stage == "inspire" and r.plan.spec.destinations == []
    assert NO_DEST in r.reply and "«Мамой»" not in r.reply


def test_trap_2_ordinary_phrase_is_not_quoted(store):
    r = say(store, "Хочу на 3 дня в мае, бюджет 100 тысяч")
    assert "«Хочу»" not in r.reply and "Не нашёл направление" not in r.reply


def test_trap_2_named_town_has_no_neutral_line(store):
    assert NO_DEST not in extract("Тихвин на 3 дня в мае")[1]
    assert extract("Тихвин на 3 дня в мае")[0]["destinations"] == ["Тихвин"]


def test_trap_2_neutral_line_goes_when_destination_is_chosen(store):
    r = say(store, "Мамой на 3 дня, в мае, бюджет 100 тысяч")
    assert NO_DEST in r.plan.spec.assumptions
    picked = say(store, "1", r.session_id)
    assert picked.plan.spec.destinations
    assert not any(a.startswith("направление: не названо") for a in picked.plan.spec.assumptions)
