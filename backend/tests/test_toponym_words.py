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
    ]:
        r = catalog.resolve(typo)
        assert r is not None and r["name"] == city
