"""#391: «X, Страна …» names the place X: the country after the comma only clarifies it.

TRAP-2: Ушуайя is outside the catalogue; the message must keep it as the destination without
an LLM key, so the agent can read the country («Аргентина») from the same message (#346)."""

import pytest

from api import catalog
from api.extract import extract


@pytest.mark.parametrize(
    ("text", "city"),
    [
        ("Ушуайя, Аргентина на 10 дней в январе", "Ушуайя"),  # TRAP-2: outside the catalogue
        ("Ушуайя на 10 дней в январе", "Ушуайя"),
        ("Анталья, Турция на неделю", "Анталия"),
        ("Бали, Индонезия на 10 дней", "Бали"),
        ("Ушуайя, Аргентина в январе", "Ушуайя"),
    ],
)
def test_place_before_its_country_is_the_destination(text, city):
    spec, _ = extract(text)
    assert spec["destinations"] == [city]


def test_country_stays_readable_from_the_message():
    text = "Ушуайя, Аргентина на 10 дней в январе"
    assert extract(text)[0]["destinations"] == ["Ушуайя"]
    assert catalog.country_in(text) == "Аргентина"


@pytest.mark.parametrize("text", ["Ушуайя, Аргентина", "Ушуайя, Аргентина."])
def test_bare_place_and_country_is_the_place(text):
    assert extract(text)[0].get("destinations") == ["Ушуайя"]


@pytest.mark.parametrize(
    "text",
    [
        "Привет, Аргентина на 10 дней",
        "Пожалуйста, Турция в ноябре",
        "Бюджет, Турция на неделю",
        "Итак, Турция в ноябре",
        "Значит, Турция в ноябре",
        "Слушай, Турция в ноябре",
        "Варианты, Таиланд в январе",
    ],
)
def test_trap_3_filler_word_before_a_country_is_not_a_place(text):
    """A word of speech before «, Страна» is no destination (#391 review)."""
    first = text.split(",")[0]
    assert first not in (extract(text)[0].get("destinations") or [])
