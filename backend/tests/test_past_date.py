"""#159: a date the user named that already passed is moved to next year with a note, not silently."""

from datetime import date

import pytest

from api.contracts import ChatRequest
from api.extract import extract
from api.model import chat
from api.store import SessionStore

TODAY = date(2026, 10, 27)


@pytest.fixture
def today(monkeypatch):
    """Fix «today» for the extractor and the reply: Tuesday 2026-10-27."""

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 27)

    monkeypatch.setattr("api.extract.date", FixedDate)
    monkeypatch.setattr("api.model.date", FixedDate)


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def year_notes(notes):
    return [n for n in notes if "уже прошло" in n]


def test_past_period_goes_to_next_year_with_a_note(today):
    found, notes = extract("Сочи из Москвы с 20 по 25 октября")
    assert found["date_from"] == date(2027, 10, 20) and found["date_to"] == date(2027, 10, 25)
    assert len(year_notes(notes)) == 1
    assert "20.10" in year_notes(notes)[0] and "2027" in year_notes(notes)[0]


def test_single_past_day_goes_to_next_year_with_a_note(today):
    found, notes = extract("Сочи 20 октября")
    assert found["date_from"] == date(2027, 10, 20)
    assert "2027" in year_notes(notes)[0]


@pytest.mark.parametrize(
    "text,start",
    [
        ("Сочи из Москвы с 28 по 30 октября", date(2026, 10, 28)),
        ("Сочи 27 октября", TODAY),  # today is not the past
        ("Сочи с 28 декабря по 3 января", date(2026, 12, 28)),
        ("Сочи с 3 по 8 ноября", date(2026, 11, 3)),
    ],
)
def test_dates_this_year_have_no_year_note(today, text, start):
    found, notes = extract(text)
    assert found["date_from"] == start
    assert not year_notes(notes)


def test_reply_shows_the_year_and_the_assumption_for_past_dates(today, store):
    r = say(store, "Сочи из Москвы с 20 по 25 октября, двое взрослых")
    assert r.plan.spec.date_from == date(2027, 10, 20)
    assert "20.10.2027–25.10.2027" in r.reply
    assert any("уже прошло" in a and "2027" in a for a in r.plan.spec.assumptions)
    assert "уже прошло" in r.reply  # the assumptions line of the reply


def test_reply_for_dates_this_year_has_no_year_and_no_note(today, store):
    r = say(store, "Сочи из Москвы с 28 по 30 октября, двое взрослых")
    assert r.plan.spec.date_from == date(2026, 10, 28)
    assert "28.10–30.10" in r.reply and "2026" not in r.reply
    assert not any("уже прошло" in a for a in r.plan.spec.assumptions)


def test_the_note_does_not_make_the_dates_a_guess(today, store):
    r = say(store, "Сочи из Москвы с 20 по 25 октября, двое взрослых")
    r = say(store, "на 7 ночей", r.session_id)
    assert r.plan.spec.date_from == date(2027, 10, 20)
    assert r.plan.spec.date_to == date(2027, 10, 27)


def test_new_future_dates_drop_the_old_year_note(today, store):
    r = say(store, "Сочи из Москвы с 20 по 25 октября, двое взрослых")
    r = say(store, "с 3 по 8 ноября", r.session_id)
    assert r.plan.spec.date_from == date(2026, 11, 3)
    assert not any("уже прошло" in a for a in r.plan.spec.assumptions)
