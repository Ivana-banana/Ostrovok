"""#295: the messages of the parameter panel (web/src/params.ts) and the «party not named» note.

The panel sends plain chat text. The rules in extract.py read it. A regex edit that breaks one
form breaks the edit window without a sound. Without a model (TRAP-1).
"""

from datetime import date
from pathlib import Path

import pytest

from api.contracts import ChatRequest
from api.extract import extract
from api.model import PARTY_UNKNOWN, chat
from api.store import SessionStore

PARAMS_TS = Path(__file__).parents[2] / "web/src/params.ts"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def test_the_forms_below_are_the_forms_of_the_panel():
    """The strings in this file are the templates of params.ts; a web edit fails here first."""
    ts = PARAMS_TS.read_text(encoding="utf-8")
    assert "`Вылет из ${city.trim()}`" in ts
    assert "`с ${day(from)} по ${day(to)}`" in ts
    assert '`Едем ${partyText(adults, ages)}${ages.length ? "" : ", без детей"}`' in ts
    assert "`Бюджет до ${Math.round(rubles)} ₽ на всю поездку`" in ts
    assert (
        '"взрослых: 2 (состав не назван)"' in ts
        and PARTY_UNKNOWN == "взрослых: 2 (состав не назван)"
    )


# The panel is open on a built plan: the plan has a city (has_destination) or has none yet.
HAS_PLAN = pytest.mark.parametrize("has_destination", [False, True])


@HAS_PLAN
@pytest.mark.parametrize(
    "city", ["Москва", "Казань", "Санкт-Петербург", "Нижний Новгород", "Екатеринбург", "Сочи"]
)
def test_origin_form(city, has_destination):
    found, _ = extract(f"Вылет из {city}", has_destination)
    assert found == {"origin": city}  # no destination, no mode


@HAS_PLAN
@pytest.mark.parametrize(
    "text,start,end,nights",
    [
        ("с 12 декабря по 14 декабря", (12, 12), (12, 14), 2),
        ("с 5 ноября по 12 ноября", (11, 5), (11, 12), 7),
        ("с 30 ноября по 2 декабря", (11, 30), (12, 2), 2),
    ],
)
def test_dates_form(text, start, end, nights, has_destination):
    found, _ = extract(text, has_destination)
    assert (found["date_from"].month, found["date_from"].day) == start
    assert (found["date_to"].month, found["date_to"].day) == end
    assert found["nights"] == nights
    assert found["date_from"] >= date.today()  # a past day moves to the next year


def test_dates_form_over_new_year_goes_to_the_next_year():
    found, _ = extract("с 30 декабря по 3 января")
    assert found["date_to"].year == found["date_from"].year + 1
    assert found["nights"] == 4


@HAS_PLAN
@pytest.mark.parametrize(
    "text,adults,ages",
    [
        ("Едем 2 взрослых, дети 5 и 9 лет", 2, [5, 9]),
        ("Едем 2 взрослых, без детей", 2, []),
        ("Едем 1 взрослый, без детей", 1, []),
        ("Едем 3 взрослых, без детей", 3, []),
        ("Едем 1 взрослый, ребёнок 3 года", 1, [3]),
        ("Едем 2 взрослых, ребёнок 11 лет", 2, [11]),
        ("Едем 4 взрослых, дети 1, 5 и 12 лет", 4, [1, 5, 12]),
    ],
)
def test_party_form(text, adults, ages, has_destination):
    found, _ = extract(text, has_destination)
    assert found == {"adults": adults, "children_ages": ages}


@HAS_PLAN
@pytest.mark.parametrize("rubles", [45_000, 120_000, 1_000_000])
def test_budget_form(rubles, has_destination):
    found, _ = extract(f"Бюджет до {rubles} ₽ на всю поездку", has_destination)
    assert found == {"budget_rub": rubles}  # a trip budget, not a hotel budget


# The forms through the whole agent: every panel edit lands in the plan spec.
def test_every_panel_form_changes_the_plan(store):
    r = say(store, "Сочи с 7 по 14 ноября")
    sid = r.session_id
    r = say(store, "Вылет из Казань", sid)
    assert r.plan.spec.origin == "Казань" and r.plan.spec.destinations == ["Сочи"]
    r = say(store, "с 12 декабря по 14 декабря", sid)
    spec = r.plan.spec
    assert (spec.date_from.month, spec.date_from.day) == (12, 12)
    assert (spec.date_to.month, spec.date_to.day) == (12, 14) and spec.nights == 2
    r = say(store, "Едем 2 взрослых, дети 5 и 9 лет", sid)
    assert r.plan.spec.adults == 2 and r.plan.spec.children_ages == [5, 9]
    r = say(store, "Едем 2 взрослых, без детей", sid)
    assert r.plan.spec.adults == 2 and r.plan.spec.children_ages == []
    r = say(store, "Бюджет до 150000 ₽ на всю поездку", sid)
    assert r.plan.spec.budget_rub == 150_000
    assert r.plan.spec.destinations == ["Сочи"] and r.plan.spec.origin == "Казань"


# Problem 1: the party is not named, 2 adults are taken, and the chip says «по умолчанию».
@pytest.mark.parametrize(
    "text",
    [
        "Казань со среды по пятницу",
        "Сочи с 12 по 15 декабря",
        "Казань на выходные",
        "Тёплое море в ноябре, недорого",
    ],
)
def test_party_note_without_a_child(store, text):
    r = say(store, text)
    assert r.plan.spec.adults == 2 and r.plan.spec.children_ages == []
    assert PARTY_UNKNOWN in r.plan.spec.assumptions
    assert not any("едет" in q.text for q in r.questions)  # no child, no question


def test_party_note_goes_when_the_panel_names_the_party(store):
    r = say(store, "Казань со среды по пятницу")
    assert PARTY_UNKNOWN in r.plan.spec.assumptions
    r = say(store, "Едем 2 взрослых, без детей", r.session_id)
    assert PARTY_UNKNOWN not in r.plan.spec.assumptions


def test_party_note_stays_when_other_field_is_edited(store):
    r = say(store, "Казань со среды по пятницу")
    r = say(store, "Бюджет до 90000 ₽ на всю поездку", r.session_id)
    assert PARTY_UNKNOWN in r.plan.spec.assumptions


def test_party_named_in_the_first_message_has_no_note(store):
    r = say(store, "Казань со среды по пятницу, нас трое")
    assert r.plan.spec.adults == 3 and PARTY_UNKNOWN not in r.plan.spec.assumptions
