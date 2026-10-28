"""TRAP-3 (#306): a capitalised ordinary word after «из/в» is not a place."""

from api.extract import extract


def test_trap_3_capitalised_word_after_preposition_is_no_place():
    found, _ = extract("Из Выбора в Сочи на неделю в июне")
    assert found.get("origin") != "Выборг"
    assert found["destinations"] == ["Сочи"]


def test_trap_3_case_form_of_vyborg_is_the_city():
    found, _ = extract("Из Выборга в Сочи на неделю в июне")
    assert found["origin"] == "Выборг"


def test_trap_3_doubled_last_letter_still_resolves():
    from api import catalog

    for typo, city in [
        ("Стамбулл", "Стамбул"),
        ("Барселонаа", "Барселона"),
        ("Тбилисии", "Тбилиси"),
        ("Ереванн", "Ереван"),
        ("Калининградд", "Калининград"),
        ("Лазаревскоее", "Лазаревское"),
        ("Петропавловск-Камчатскийй", "Петропавловск-Камчатский"),
        ("Переславль-Залесскийй", "Переславль-Залесский"),
    ]:
        r = catalog.resolve(typo)
        assert r is not None and r["name"] == city, typo


def test_trap_3_instrumental_of_compound_names_resolves():
    from api import catalog

    for written, city in [
        ("Тель-Авивом", "Тель-Авив"),
        ("Шарм-эль-Шейхом", "Шарм-эль-Шейх"),
        ("Южно-Сахалинском", "Южно-Сахалинск"),
        ("Горно-Алтайском", "Горно-Алтайск"),
        ("Плая-дель-Карменом", "Плая-дель-Кармен"),
    ]:
        r = catalog.resolve(written)
        assert r is not None and r["name"] == city, written


def test_trap_3_adjective_of_krasnodar_is_not_krasnoyarsk():
    from api.catalog import respelled

    for word in ("Краснодарский", "Краснодарская", "Краснодарское"):
        assert respelled(word) is None
    for text in ("Хочу на Краснодарский берег в июле", "Из Краснодарский в Сочи"):
        found = extract(text)[0]
        assert "Красноярск" not in [*found.get("destinations", []), found.get("origin")]
