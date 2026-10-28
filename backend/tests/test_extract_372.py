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


@pytest.mark.parametrize(
    "text, town",
    [
        ("Хочу в Раменское", "Раменское"),
        ("Поедем в Жуковский", "Жуковский"),
        ("Хочу в Дивноморское на море", "Дивноморское"),
    ],
)
def test_trap2_unknown_town_with_adjective_ending_still_goes_to_geocoder(text, town):
    """TRAP-2: a town outside the catalogue that looks like an adjective stays a destination.

    Only an adjective of a KNOWN place («Петербургский») is dropped. Known limitation: «-ском»
    is not in NOT_ADJECTIVE, so «В Новосибирском театре» is Новосибирск (by design: «в Иркутском»).
    """
    assert town in extract(text)[0].get("destinations", [])
