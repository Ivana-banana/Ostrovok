"""#87, #116: a stem is not a city, and a city is not a stem (TRAP-2).

«Добрич» (Bulgaria) is a city, «Добрый день» is a greeting. «Городской на 3 дня» is an
adjective before a length, «Ханой» and «Уренгой» are cities. Without a model (TRAP-1).
"""

import pytest

from api.extract import extract


@pytest.mark.parametrize("text", ["Добрич в мае", "Добрич на 3 дня", "Хочу в Добрич в мае"])
def test_trap_2_dobrich_is_a_city(text):
    assert extract(text)[0]["destinations"] == ["Добрич"]


@pytest.mark.parametrize(
    "text",
    [
        "Добрый день, хочу на море",
        "Доброе утро",
        "добрый вечер",
        "Добрый",
        "Доброго дня, хочу на море",
        "Добрая погода на 3 дня",
        "Добрый день на 3 дня",
    ],
)
def test_trap_2_greeting_is_no_city(text):
    assert "destinations" not in extract(text)[0]


@pytest.mark.parametrize("text", ["Городской на 3 дня", "Лесной на 3 дня", "Речной на 3 дня"])
def test_trap_2_adjective_before_a_length_is_no_city(text):
    assert "destinations" not in extract(text)[0]


@pytest.mark.parametrize(
    ("text", "city"),
    [
        ("Ханой на 3 дня", "Ханой"),
        ("Уренгой на 3 дня", "Уренгой"),
        ("Хочу в Ханой на 3 дня", "Ханой"),
    ],
)
def test_trap_2_city_in_oy_stays_a_city(text, city):
    assert extract(text)[0]["destinations"] == [city]
