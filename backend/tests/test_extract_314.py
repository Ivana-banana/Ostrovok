"""#314: «не летаем» bans the flight; a long flight only is no ban."""

import pytest

from api.extract import extract


@pytest.mark.parametrize(
    "text", ["Не летаем, из Сочи в горы", "мы не летаем, Казань на выходные", "я не летаю, Казань"]
)
def test_we_do_not_fly_bans_the_flight(text):
    assert extract(text)[0]["modes"] == ["train", "car"]


@pytest.mark.parametrize("text", ["не летаем долго, Сочи", "мы не летаем больше 4 часов, Сочи"])
def test_no_long_flight_is_no_ban(text):
    assert "modes" not in extract(text)[0]
