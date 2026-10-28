"""#345: a place named inside a country wins over the country («в Тайланд на пхукет»).

The country is no second destination when a place of this country is in the message.
Lowercase and spelling variants of the place are TRAP-3."""

import pytest

from api.extract import extract


@pytest.mark.parametrize(
    ("text", "place"),
    [
        ("в Тайланд на пхукет с 5 по 15 декабря из Москвы вдвоём", "Пхукет"),  # TRAP-3
        ("в Тайланд, Пхукет", "Пхукет"),
        ("в Таиланд на Пхукет", "Пхукет"),
        ("в Тайланд, пхукет", "Пхукет"),  # TRAP-3
        ("в Турцию в Анталью", "Анталия"),
        ("в Турцию в Анталью с 5 по 15 декабря", "Анталия"),
        # «египет» is a catalogue stem of Хургада: the named place still wins over it
        ("в Египет в Шарм-эль-Шейх", "Шарм-эль-Шейх"),
        ("в Египет, Шарм-эль-Шейх", "Шарм-эль-Шейх"),
        ("в Египет, в Дахаб", "Дахаб"),
        ("в Египет, дахаб", "Дахаб"),  # TRAP-3
    ],
)
def test_place_inside_country_wins(text, place):
    assert extract(text)[0]["destinations"] == [place]


def test_bare_country_stays_a_destination():
    assert extract("в Тайланд")[0]["destinations"] == ["Таиланд"]


def test_two_countries_stay_two_destinations():
    assert extract("Хочу в Турцию, Египет")[0]["destinations"] == ["Турция", "Хургада"]


def test_lowercase_tail_place_must_be_of_the_named_country():
    # Губа is a place of Азербайджан, not of Грузия: no second destination
    assert extract("в Грузию, губа")[0]["destinations"] == ["Грузия"]
