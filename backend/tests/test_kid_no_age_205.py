"""#205: a child named without an age never changes the ages of the plan to 7.

Without a model (TRAP-1). The price and the party of the plan change only on a stated reason.
"""

import pytest
from talk import say

from api.extract import extract
from api.store import SessionStore

FIRST = "Сочи из Москвы с 3 по 8 декабря, двое взрослых и дочка 5 лет"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


@pytest.mark.parametrize(
    "text", ["без дочери не поедем", "с дочкой", "едем с дочкой", "без сына не поедем"]
)
def test_child_without_age_keeps_the_ages_of_the_plan(store, text):
    first = say(store, FIRST)
    assert first.plan.spec.children_ages == [5]
    again = say(store, text, first.session_id)
    assert again.plan.spec.children_ages == [5]
    assert again.plan.spec.adults == 2
    assert again.plan.bundles == first.plan.bundles
    assert not any("возраст ребёнка" in a for a in again.plan.spec.assumptions)


def test_extract_with_kept_ages_adds_nobody():
    found, notes = extract("с дочкой", kept_ages=[5])
    assert "children_ages" not in found
    assert not any("возраст ребёнка" in n for n in notes)


def test_without_kept_ages_the_default_stays():
    found, _ = extract("с дочкой")
    assert found["children_ages"] == [7]


def test_a_named_age_or_a_count_still_changes_the_ages():
    assert extract("с дочкой 9 лет", kept_ages=[5])[0]["children_ages"] == [9]
    assert extract("теперь с двумя детьми", kept_ages=[5])[0]["children_ages"] == [7, 7]
    assert extract("и ещё один ребёнок", kept_ages=[5])[0]["children_ages"] == [7]
