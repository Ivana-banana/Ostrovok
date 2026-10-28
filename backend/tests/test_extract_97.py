"""#97: «Нижний Тагил» is no «Нижний Новгород»; «в Ухту» is the city «Ухта» (TRAP-3)."""

import pytest

from api.extract import _nominative, extract


@pytest.mark.parametrize("has_destination", [False, True])
@pytest.mark.parametrize(
    "text", ["Нижний Тагил на 3 дня", "Нижний Тагил, 3 ночи", "Нижнего Тагила на выходные"]
)
def test_trap3_nizhny_tagil_is_not_nizhny_novgorod(text, has_destination):
    found, _ = extract(text, has_destination)
    assert "Нижний Новгород" not in found.get("destinations", [])


def test_trap3_nizhny_tagil_is_named_outside_the_catalogue():
    assert extract("Нижний Тагил на 3 дня")[0]["destinations"] == ["Нижний Тагил"]
    assert extract("Хочу в Нижний Тагил")[0]["destinations"] == ["Нижний Тагил"]
    found, notes = extract("Нижний Тагил, 3 ночи", has_destination=True)
    assert "destinations" not in found
    assert any("Нижний Тагил" in n and "нет в каталоге" in n for n in notes)


@pytest.mark.parametrize(
    "text",
    [
        "Нижний Новгород на выходные",
        "Нижний Новгород, 3 ночи",
        "Хочу в Нижний Новгород",
        "Поехать в Нижнем Новгороде на неделю",
        "Нижний на выходные",
        "Нижний, 3 ночи",
    ],
)
def test_trap3_nizhny_novgorod_still_works(text):
    assert extract(text)[0]["destinations"] == ["Нижний Новгород"]


def test_trap3_lower_floor_is_no_city():
    assert "destinations" not in extract("Хочу номер на нижнем этаже")[0]


@pytest.mark.parametrize("text", ["Ну тогда в Ухту", "Хочу в Ухту на 3 дня", "Поехали в Ухту"])
def test_trap3_accusative_ukhta_is_named_in_the_nominative(text):
    assert extract(text)[0]["destinations"] == ["Ухта"]


@pytest.mark.parametrize(
    "word, name",
    [
        ("Ухту", "Ухта"),
        ("Ялту", "Ялта"),
        ("Малибу", "Малибу"),
        ("Баку", "Баку"),
        ("Бонну", "Бонну"),
        ("Турку", "Турку"),
        ("Мачу-Пикчу", "Мачу-Пикчу"),
        ("Тарту", "Тарту"),
        ("Тимбукту", "Тимбукту"),
        ("Омску", "Омск"),
        ("Оренбургу", "Оренбург"),
    ],
)
def test_trap3_accusative_u_only_after_hta_and_lta(word, name):
    assert _nominative(word) == name


@pytest.mark.parametrize("city", ["Турку", "Тарту", "Омск", "Оренбург"])
def test_trap3_u_names_stay_as_written_in_a_phrase(city):
    assert extract(f"Хочу в {city}")[0]["destinations"] == [city]


@pytest.mark.parametrize(
    "text, city",
    [
        ("Нижнекамск на 3 дня", "Нижнекамск"),
        ("Хочу в Нижнекамск", "Нижнекамск"),
        ("Нижневартовск на 3 дня", "Нижневартовск"),
    ],
)
def test_trap3_nizhnekamsk_is_no_nizhny_novgorod(text, city):
    found, _ = extract(text)
    assert "Нижний Новгород" not in found.get("destinations", [])


@pytest.mark.parametrize(
    "text",
    [
        "Минеральные воды полезны, хочу санаторий в мае",
        "Великий Новгородский кремль посмотреть",
        "Великий Новгородская область",
    ],
)
def test_trap3_a_common_phrase_with_a_city_name_is_no_city(text):
    assert "destinations" not in extract(text)[0]


@pytest.mark.parametrize(
    "text, city",
    [
        ("Минеральные воды в мае", "Минеральные Воды"),
        ("Минеральные воды", "Минеральные Воды"),
        ("Минеральные Воды на 3 дня", "Минеральные Воды"),
        ("Поездка в Минеральных Водах", "Минеральные Воды"),
        ("Хочу в Великом Новгороде", "Великий Новгород"),
        ("Великий Новгород в мае", "Великий Новгород"),
        ("Набережные Челны на выходные", "Набережные Челны"),
        ("в Старой Руссе на выходные", "Старая Русса"),
    ],
)
def test_trap3_two_word_cities_keep_working(text, city):
    assert extract(text)[0]["destinations"] == [city]
