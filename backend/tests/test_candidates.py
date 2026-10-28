"""#139: cities joined by «или» are candidates; the second city is not lost silently.

Without a model (TRAP-1). The plan covers the first city. The reply names the others and gives
a chip «План: {город}» for each one that has offers. The departure city is no destination.
"""

import pytest
from talk import say

from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def hotel_titles(r):
    return [o.title for o in r.plan.offers if o.kind == "hotel"]


@pytest.mark.parametrize(
    "text, first, second",
    [
        ("Сочи или Анапа в июле", "Сочи", "Анапа"),
        ("Казань или Нижний на 3 дня с бабушкой", "Казань", "Нижний Новгород"),
        ("Либо Сочи, либо Анапа", "Сочи", "Анапа"),
        ("Сочи/Анапа в июле", "Сочи", "Анапа"),
        ("Сочи, Анапа", "Сочи", "Анапа"),
    ],
)
def test_candidates_name_the_second_city_and_give_a_chip(store, text, first, second):
    r = say(store, text)
    assert r.stage == "compare"
    assert r.plan.spec.destinations == [first, second]
    assert hotel_titles(r) and all(first in t for t in hotel_titles(r))
    assert f"Сравниваю сначала {first}." in r.reply
    assert f"{second} — кнопкой ниже." in r.reply
    assert [q.options for q in r.questions] == [[f"План: {second}"]]
    line = next(a for a in r.plan.spec.assumptions if a.startswith("варианты:"))
    assert second in line
    assert second in r.reply.split("Допущения:")[1]


def test_candidate_chip_plans_that_city_without_the_notice(store):
    r = say(store, "Сочи или Анапа в июле")
    r = say(store, r.questions[0].options[0], r.session_id)  # «План: Анапа»
    assert r.stage == "compare"
    assert r.plan.spec.destinations == ["Анапа"]
    assert hotel_titles(r) and all("Анапа" in t for t in hotel_titles(r))
    assert "Сравниваю сначала" not in r.reply
    assert not [a for a in r.plan.spec.assumptions if a.startswith("варианты:")]
    assert r.plan.spec.date_from.month == 7  # the month stays: no new search from zero
    assert r.questions == []


def test_three_candidates_give_two_chips(store):
    r = say(store, "Хочу в Сочи или Анапу или Геленджик")
    assert "Сравниваю сначала Сочи." in r.reply
    assert [q.options for q in r.questions] == [["План: Анапа", "План: Геленджик"]]


def test_no_chip_for_a_candidate_without_offers(store):
    r = say(store, "Хочу в Сочи или в Пномпень")
    assert r.stage == "compare"
    assert "Сравниваю сначала Сочи." in r.reply
    assert "Не вошло: Пномпень." in r.reply
    assert "кнопкой" not in r.reply
    assert r.questions == []


def test_candidates_notice_does_not_repeat_on_the_next_turn(store):
    r = say(store, "Сочи или Анапа в июле")
    r = say(store, "беру", r.session_id)
    assert r.stage == "assemble"
    assert "Сравниваю сначала" not in r.reply
    assert any(a.startswith("варианты:") for a in r.plan.spec.assumptions)


def test_candidates_with_a_child_show_the_notice_after_the_party_question(store):
    r = say(store, "Сочи или Анапа с ребёнком 5 лет")
    assert r.stage == "clarify"
    r = say(store, "Один взрослый", r.session_id)
    assert r.stage == "compare"
    assert "Сравниваю сначала Сочи." in r.reply
    assert r.questions[0].options == ["План: Анапа"]


def test_one_city_has_no_candidates_notice(store):
    r = say(store, "Сочи в июле")
    assert "Сравниваю сначала" not in r.reply
    assert r.questions == []


def test_route_and_candidates_keep_their_own_notices(store):
    r = say(store, "Питер на 2 ночи, затем Казань на 3 ночи")
    assert "В плане только: Санкт-Петербург" in r.reply
    assert "Сравниваю сначала" not in r.reply


def test_departure_city_is_no_destination(store):
    r = say(store, "Москва — Питер — Казань")
    assert r.plan.spec.origin == "Москва"
    assert r.plan.spec.destinations == ["Санкт-Петербург", "Казань"]
    hotels = hotel_titles(r)
    assert hotels and all("Санкт-Петербург" in t for t in hotels)
    assert "В плане только: Санкт-Петербург" in r.reply
    assert [q.options for q in r.questions] == [["План: Санкт-Петербург", "План: Казань"]]


def test_departure_city_named_with_from_is_no_candidate(store):
    r = say(store, "Из Питера в Москву или Казань")
    assert r.plan.spec.origin == "Санкт-Петербург"
    assert r.plan.spec.destinations == ["Москва", "Казань"]
    assert "Сравниваю сначала Москва." in r.reply


def test_the_default_departure_city_among_candidates_is_dropped(store):
    r = say(store, "Москва или Питер")
    assert r.plan.spec.destinations == ["Санкт-Петербург"]
    assert "Сравниваю сначала" not in r.reply
