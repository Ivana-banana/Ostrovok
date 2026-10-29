"""#661: numeric dates «28.12.2026», «28.12», «28.12–03.01» fill date_from / date_to."""

from datetime import date, timedelta

import pytest

from api.extract import extract


def dates(text):
    found = extract(text)[0]
    return found.get("date_from"), found.get("date_to")


def test_full_date_with_year():
    assert dates("Хочу в Сочи с 28.12.2026")[0] == date(2026, 12, 28)


def test_two_digit_year():
    assert dates("Хочу в Сочи с 28.12.27")[0] == date(2027, 12, 28)


def test_day_month_is_the_nearest_future_date():
    start = dates("Хочу в Сочи на 28.12")[0]
    assert start.month == 12 and start.day == 28
    assert date.today() <= start < date.today() + timedelta(days=366)


@pytest.mark.parametrize("text", ["Сочи 28.12-03.01", "Сочи с 28.12 по 03.01"])
def test_range_crosses_the_year(text):
    start, end = dates(text)
    assert (start.month, start.day, end.month, end.day) == (12, 28, 1, 3)
    assert end.year == start.year + 1


def test_invalid_date_is_ignored():
    assert dates("Хочу в Сочи с 31.02")[0] is None


@pytest.mark.parametrize(
    "text",
    [
        "Сочи, бюджет 1.500",
        "Сочи до 2.5 тыс",
        "Сочи на 2.5 млн",
        "Сочи, рейс в 10.30",
        "Сочи на 12.5 тыс",
        "Сочи на 2.5 недели",
        "Сочи на 1.5 месяца",
        "Сочи на 10.5 дней",
        "Сочи от 4.5 звезд",
        "Сочи на 4.5 звезды",
        "Сочи от 1.2 км до моря",
        "Сочи с 1.5 детьми",
    ],
)
def test_prices_and_times_are_no_dates(text):
    assert dates(text) == (None, None)


def test_word_month_wins_over_a_number():
    found = extract("Сочи в декабре на 1.5 недели")[0]
    assert found["month"] == 12 and "date_from" not in found
