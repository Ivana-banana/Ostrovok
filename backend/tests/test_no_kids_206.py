"""#206: after «без детей» only a listed negation word makes a double negation.

Words that start with «ни» (Ницца, Нижний, Нидерланды, ниже) are no negation. A dash with
spaces, a bracket and a line break close the clause. Without a model (TRAP-1).
"""

import pytest

from api.extract import extract


@pytest.mark.parametrize(
    "text",
    [
        "без детей в Ниццу",
        "без детей в Ниццу в мае",
        "без детей в Нижний Новгород",
        "без детей в Нидерланды",
        "без детей ниже 100 тысяч",
        "без детей – не дороже 100 тысяч",
        "без детей — не дороже 100 тысяч",
        "без детей - не дороже 100 тысяч",
        "без детей (не дороже 100 тысяч)",
        "без детей\nне дороже 100 тысяч",
    ],
)
def test_no_kids_gives_zero_children(text):
    found, notes = extract(text)
    assert found.get("children_ages") == []
    assert not any("возраст ребёнка" in n for n in notes)


@pytest.mark.parametrize(
    "text",
    [
        "без детей не поедем",
        "без детей никуда не поедем",
        "без детей никак нельзя",
        "без детей никогда не ездим",
        "без детей ничего не получится",
        "без детей ни за что",
    ],
)
def test_double_negation_keeps_children(text):
    found, _ = extract(text)
    assert found.get("children_ages") == [7, 7]


@pytest.mark.parametrize(
    "text",
    [
        "без детей — не поедем",
        "без детей - не поедем",
        "без детей – никуда",
        "без детей (никуда)",
        "без детей\nникуда",
        "без детей\nне поедем",
    ],
)
def test_double_negation_across_dash_bracket_newline_keeps_children(text):
    found, _ = extract(text)
    assert found.get("children_ages") == [7, 7]


@pytest.mark.parametrize(
    "text",
    [
        "без детей — не больше 100 тысяч",
        "без детей (не более 100 тысяч)",
        "без детей\nне позже 10 мая",
        "без детей – не раньше июня",
        "без детей не дороже 100 тысяч",
    ],
)
def test_budget_or_date_tail_is_no_negation(text):
    found, _ = extract(text)
    assert found.get("children_ages") == []
