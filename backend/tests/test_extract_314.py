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


@pytest.mark.parametrize(
    "text",
    [
        "не летаю лоукостерами, Бали",
        "не летаем без багажа, Сочи",
        "не летаем экономом",
        "не летаем Аэрофлотом, Сочи",
        "не летаю ночью, Сочи",
        "не летаю с детьми ночью",
    ],
)
def test_a_fly_refusal_with_a_detail_is_no_ban(text):
    assert "train" not in extract(text)[0].get("modes", [])


@pytest.mark.parametrize(
    "text", ["мы вообще не летаем", "не летаем вообще, Казань", "не летаю и всё"]
)
def test_we_do_not_fly_at_clause_end_bans_the_flight(text):
    assert extract(text)[0]["modes"] == ["train", "car"]


def test_we_do_not_fly_on_a_new_line_bans_the_flight():
    assert extract("не летаем\nКазань на выходные")[0]["modes"] == ["train", "car"]


@pytest.mark.parametrize("text", ["в январе, не летаем, подтверждаю", "январь — не летаем"])
def test_no_fly_after_a_month_refuses_the_month(text):
    assert "train" not in extract(text)[0].get("modes", [])


@pytest.mark.parametrize(
    "text",
    ["в ноябре, без перелёта", "в ноябре, без самолёта, с ребёнком", "в декабре, не хочу летать"],
)
def test_other_fly_bans_after_a_month_stay(text):
    assert extract(text)[0]["modes"] == ["train", "car"]
