"""#365: critic cards C19, C20 and C21 (eval/cases_critic.json). Offline, rules only (TRAP-1).

«Today» is fixed: 2026-10-27, the date of the cards.
C19: «Питер и Москву, по 3 дня» is 3 days in each city: 5 nights for the trip, and Москва is named.
C20: «from 20 November» is the start day 20.11, not the first Saturday of the month (TRAP-3).
C21: «на ноябрьские» are the days off around 4 November; «недалеко» limits the road, and the
first answer shows destinations, not only the budget question.
"""

from datetime import date

import pytest
from talk import say

from api.extract import extract
from api.store import SessionStore

C19 = "Хотим Питер и Москву, по 3 дня, из Казани, вдвоём, с 5 декабря"
C20 = "Hi! Sochi, 3 nights from 20 November, 2 adults, from Moscow"
C21 = "На ноябрьские куда-нибудь недалеко вдвоём"


@pytest.fixture(autouse=True)
def today(monkeypatch):
    class Fixed(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 27)

    for module in ("api.extract", "api.dag", "api.model"):
        monkeypatch.setattr(f"{module}.date", Fixed)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GIGACHAT_AUTH_KEY", raising=False)
    return SessionStore(tmp_path / "sessions.db")


# C19


def test_C19_each_city_stay_is_summed_for_the_trip():
    found, notes = extract(C19)
    assert found["destinations"] == ["Санкт-Петербург", "Москва"]
    assert found["nights"] == 5  # 3 days + 3 days = 6 days, 5 nights
    assert any("по 3 дня" in n for n in notes)


@pytest.mark.parametrize(
    ("text", "nights"),
    [
        ("Питер и Москва, по 2 ночи, вдвоём", 4),
        ("Сочи, по 3 ночи, вдвоём", 3),  # one city: «по» is no sum
    ],
)
def test_C19_each_stay_counts_the_cities(text, nights):
    assert extract(text)[0]["nights"] == nights


def test_C19_moscow_is_not_lost_silently(store):
    r = say(store, C19)
    spec = r.plan.spec
    assert spec.nights >= 5
    assert "Москва" in r.reply
    hotels = {o.city for o in r.plan.offers if o.kind == "hotel"}
    # Either the whole route, or one city named as such with a question
    assert hotels >= {"Санкт-Петербург", "Москва"} or (r.questions and "Не вошло: Москва" in r.reply)


# C20


def test_C20_TRAP_3_english_start_day_is_read():
    found, notes = extract(C20)
    assert found["destinations"] == ["Сочи"]
    assert found["date_from"] == date(2026, 11, 20) and found["nights"] == 3
    assert not any(n.startswith("даты:") for n in notes)


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("Sochi from the 20th of November, 3 nights", date(2026, 11, 20), None),
        ("Sochi, November 20th, 3 nights", date(2026, 11, 20), None),
        ("Sochi 20-23 November", date(2026, 11, 20), date(2026, 11, 23)),
        ("Sochi from 20 to 23 November", date(2026, 11, 20), date(2026, 11, 23)),
        ("Sochi, November 20 - 23", date(2026, 11, 20), date(2026, 11, 23)),
        ("Kazan on 5 may, 2 nights", date(2027, 5, 5), None),
    ],
)
def test_C20_TRAP_3_english_date_forms(text, start, end):
    found, _ = extract(text)
    assert found["date_from"] == start
    assert found.get("date_to") == end


@pytest.mark.parametrize(
    "text",
    ["Sochi, we may stay 5 nights", "Sochi in November, 5 nights, 2 adults"],
)
def test_C20_TRAP_3_no_english_day_no_start(text):
    assert "date_from" not in extract(text)[0]


def test_C20_TRAP_3_reply_dates_are_the_said_dates(store):
    r = say(store, C20)
    spec = r.plan.spec
    assert spec.date_from == date(2026, 11, 20) and spec.date_to == date(2026, 11, 23)
    assert not any("точных дат не было" in a for a in spec.assumptions)


# C21


def test_C21_november_holidays_are_the_days_off_around_4_november():
    found, notes = extract(C21)
    # 4.11.2026 is a Wednesday: from Saturday 31.10 to the holiday
    assert found["date_from"] == date(2026, 10, 31) and found["date_to"] == date(2026, 11, 4)
    assert found["nights"] == 4 and found["max_travel_hours"] == 3.0
    assert any("ноябрьские" in n and "допущение" in n for n in notes)


def test_C21_named_length_keeps_the_holiday_start():
    found, _ = extract("На ноябрьские в Казань на 2 ночи")
    assert found["date_from"] == date(2026, 10, 31) and found["date_to"] == date(2026, 11, 2)
    assert found["nights"] == 2


@pytest.mark.parametrize(
    "text",
    ["После ноябрьских в Казань", "Казань, ноябрьский снег, 3 ночи"],
)
def test_C21_not_the_holidays(text):
    assert "date_from" not in extract(text)[0]


def test_C21_first_answer_shows_destinations_not_the_budget_question(store):
    r = say(store, C21)
    spec = r.plan.spec
    assert date(2026, 10, 31) <= spec.date_from <= date(2026, 11, 4)
    assert spec.max_travel_hours is not None
    assert not any(q.text.startswith("Какой бюджет") for q in r.questions)
    assert r.plan.destinations
