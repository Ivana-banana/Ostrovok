"""#372 (TRAP-3): an adjective of a city name is no place; the noun in any case is."""

import pytest

from api.extract import extract


@pytest.mark.parametrize(
    "text", ["Хочу в Петербургский музей", "Нравится петербургская архитектура"]
)
def test_trap3_adjective_of_a_name_is_no_place(text):
    assert not extract(text)[0].get("destinations")


@pytest.mark.parametrize(
    "text", ["Хочу в Петербург на 3 ночи", "Поедем из Петербурга в Сочи", "Неделя в Иркутске"]
)
def test_trap3_noun_of_a_name_stays_a_place(text):
    spec = extract(text)[0]
    assert {*spec.get("destinations", []), spec.get("origin")} & {"Санкт-Петербург", "Иркутск"}
