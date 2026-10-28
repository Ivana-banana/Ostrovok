"""#152: a weekday stays in the named month; «в эти выходные» on Saturday or Sunday starts today."""

from datetime import date

import pytest

from api.extract import extract


def fix_today(monkeypatch, day: date) -> None:
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(day.year, day.month, day.day)

    monkeypatch.setattr("api.extract.date", FixedDate)


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 11, 28), date(2027, 11, 5)),  # no Friday left in this November
        (date(2026, 11, 25), date(2026, 11, 27)),  # still a Friday left
        (date(2026, 11, 20), date(2026, 11, 27)),
        (date(2026, 10, 27), date(2026, 11, 6)),
    ],
)
def test_weekday_does_not_leave_the_named_month(monkeypatch, today, expected):
    fix_today(monkeypatch, today)
    found, _ = extract("в Сочи в ноябре в пятницу, двое")
    assert found["date_from"] == expected and found["month"] == 11


@pytest.mark.parametrize(
    "today,start,end,nights",
    [
        (date(2026, 11, 28), date(2026, 11, 28), date(2026, 11, 29), 1),  # Saturday
        (date(2026, 11, 29), date(2026, 11, 29), date(2026, 11, 30), 1),  # Sunday
        (date(2026, 10, 27), date(2026, 10, 30), date(2026, 11, 1), 2),  # Tuesday
    ],
)
def test_these_weekend_on_a_weekend_day_is_not_next_friday(monkeypatch, today, start, end, nights):
    fix_today(monkeypatch, today)
    found, notes = extract("в Казань в эти выходные, двое")
    assert found["date_from"] == start and found["date_to"] == end and found["nights"] == nights
    assert any(n.startswith("даты:") for n in notes)


def test_next_weekend_on_saturday_is_the_next_friday(monkeypatch):
    fix_today(monkeypatch, date(2026, 11, 28))
    found, _ = extract("в Казань в следующие выходные, двое")
    assert found["date_from"] == date(2026, 12, 4) and found["date_to"] == date(2026, 12, 6)


def test_bare_weekend_on_saturday_stays_the_next_friday(monkeypatch):
    fix_today(monkeypatch, date(2026, 11, 28))
    found, _ = extract("в Казань на выходные, двое")
    assert found["date_from"] == date(2026, 12, 4) and found["nights"] == 2


def test_named_month_weekend_on_saturday_stays_in_that_month(monkeypatch):
    fix_today(monkeypatch, date(2026, 10, 31))
    found, _ = extract("в Казань в ноябре на выходные, двое")
    assert found["date_from"] == date(2026, 11, 6) and found["month"] == 11
