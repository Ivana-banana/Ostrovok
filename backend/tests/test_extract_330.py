"""#330: «в дороге» and «сутки» set the travel time limit."""

import pytest

from api.extract import extract


@pytest.mark.parametrize(
    ("text", "hours"),
    [
        ("Москва, море в ноябре, не больше 20 ч в дороге", 20.0),
        ("из Казани на юг, до 10 часов в дороге", 10.0),
        ("из Москвы к морю, дорога не дольше суток", 24.0),
        ("из Москвы, в дороге не больше 12 часов", 12.0),
        ("из Москвы, перелет не дольше 6 ч", 6.0),
        ("из Москвы, в пути не более двух суток", 48.0),
        ("из Москвы, не больше 20 часов в пути", 20.0),  # the old form stays
    ],
)
def test_travel_limit_phrases_set_max_travel_hours(text, hours):
    found, _ = extract(text)
    assert found["max_travel_hours"] == hours


@pytest.mark.parametrize(
    "text",
    [
        "из Москвы, вылет до 10 часов утра",
        "дорога до Сочи, 5 ночей",
        "Сочи на сутки",
    ],
)
def test_no_travel_limit_without_a_limit_phrase(text):
    found, _ = extract(text)
    assert "max_travel_hours" not in found
