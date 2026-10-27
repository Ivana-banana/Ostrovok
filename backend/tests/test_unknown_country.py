"""#346: a place outside the catalogue (Ушуайя) is not answered with unrelated cities (TRAP-2, S5).

No model (TRAP-1) and no network (TRAP-6): conftest turns the geocoder off. Without a country the
agent asks for it. With a country (in the message or in the next one) it offers the catalogue
cities of that country, never «Воронеж, Нижний Новгород, Минск» for a place in Patagonia.
"""

import pytest

from api import catalog
from api.contracts import ChatRequest
from api.model import NOT_IN_CATALOG, chat
from api.store import SessionStore

UNRELATED = ("Воронеж", "Нижний Новгород", "Минск")
ASK = "Ушуайя на 10 дней в январе, из Москвы"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def test_trap_2_unknown_city_without_country_asks_the_country(store):
    r = say(store, ASK)
    assert r.stage == "select" and r.plan.spec.destinations == ["Ушуайя"]
    assert NOT_IN_CATALOG in r.reply and "Ушуайя" in r.reply
    assert r.voucher is None and r.cart.items == []
    assert not any(c in r.reply for c in UNRELATED)
    assert [q.options for q in r.questions] == [[]]  # free text, no unrelated buttons
    assert "стран" in r.questions[0].text and "Ушуайя" in r.questions[0].text


def test_trap_2_the_country_answer_gives_the_cities_of_that_country(store):
    first = say(store, ASK)
    r = say(store, "Аргентина", first.session_id)
    assert r.stage == "select" and r.plan.spec.destinations == ["Ушуайя"]
    assert NOT_IN_CATALOG in r.reply and "Аргентин" in r.reply
    assert [q.options for q in r.questions] == [["Буэнос-Айрес"]]
    assert not any(c in r.reply for c in UNRELATED)
    again = say(store, "подтверждаю", r.session_id)  # a consent books nothing
    assert again.voucher is None and again.cart.items == []


def test_trap_2_country_in_the_first_message_is_used_at_once(store):
    r = say(store, "хочу в Ушуайю, Аргентина на 10 дней в январе, из Москвы")
    assert r.stage == "select" and NOT_IN_CATALOG in r.reply
    assert [q.options for q in r.questions] == [["Буэнос-Айрес"]]
    assert not any(c in r.reply for c in UNRELATED)


def test_trap_2_the_country_of_the_origin_is_not_the_country_of_the_place(store):
    r = say(store, "Ушуайя на 10 дней в январе, вылет из России")
    assert [q.options for q in r.questions] == [[]]


def test_trap_2_country_outside_the_catalogue_asks_again(store):
    assert catalog.country_of("Чили") is None
    r = say(store, "Пуэрто-Наталес на 10 дней в январе, Чили")
    assert r.stage == "select" and NOT_IN_CATALOG in r.reply
    assert not any(c in r.reply for c in UNRELATED)
    assert all(not q.options for q in r.questions)


@pytest.mark.parametrize(
    ("text", "country"),
    [
        ("Ушуайя, Аргентина", "Аргентина"),
        ("в Ушуайе в Аргентине", "Аргентина"),
        ("Ушуайя (Аргентина)", "Аргентина"),
        ("Остров Крит, Греция на 5 дней", "Греция"),
        ("Ушуайя, вылет из России", None),
        ("Ушуайя от Турции далеко", None),
        ("Кандалакша на 5 дней в июле, из Москвы", None),
    ],
)
def test_trap_3_country_in_text(text, country):
    assert catalog.country_in(text) == country


def test_trap_2_the_country_stays_when_the_dates_change(store):
    r = say(store, "Аргентина", say(store, ASK).session_id)
    later = say(store, "в феврале", r.session_id)
    assert later.plan.spec.destinations == ["Ушуайя"]
    assert [q.options for q in later.questions] == [["Буэнос-Айрес"]]


def test_trap_2_the_country_is_not_kept_for_another_unknown_place(store):
    r = say(store, "Аргентина", say(store, ASK).session_id)
    other = say(store, "давай в Кандалакшу", r.session_id)
    assert other.plan.spec.destinations[0].startswith("Кандалакш")
    assert [q.options for q in other.questions] == [[]]
