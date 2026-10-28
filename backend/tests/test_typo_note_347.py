"""#347: a typo in a city is corrected, and the answer says so (TRAP-3).

The note is an assumption «город: Калининрад → Калининград». A case form («Калининграда») and an
alias («Ебург», «Питер») are no typos: no note."""

import pytest

from api.extract import extract


def city_notes(text):
    return [n for n in extract(text)[1] if n.startswith("город:")]


def test_trap3_typo_in_destination_is_noted():
    found, notes = extract("Хочу в Калининрад на неделю")
    assert found["destinations"] == ["Калининград"]
    assert "город: Калининрад → Калининград" in notes


def test_trap3_typo_in_origin_is_noted():
    found, notes = extract("Летим из Калининрад в Сочи")
    assert found["origin"] == "Калининград"
    assert "город: Калининрад → Калининград" in notes


@pytest.mark.parametrize(
    "text",
    [
        "Хочу в Калининград на неделю",
        "Хочу в Калининграда на неделю",
        "Поеду из Калининграда в Сочи",
        "Хочу в Питер",
        "Хочу в Ебург",
        "Из Ебурга в Сочи",
        "Хочу в Москве",
    ],
)
def test_trap3_case_form_and_alias_make_no_note(text):
    assert city_notes(text) == []


def test_trap3_note_is_not_repeated():
    notes = extract("Хочу в Калининрад, в Калининрад на неделю")[1]
    assert notes.count("город: Калининрад → Калининград") <= 1
