"""#146 (D-038): «на пару ночей» is 2 nights; «на пару дней позже» in a first message is no length."""

import pytest
from talk import say

from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


@pytest.mark.parametrize(
    ("text", "nights"),
    [
        ("Сочи на пару ночей в ноябре", 2),
        ("Сочи на пару суток в ноябре", 2),
        ("Сочи на пару дней в ноябре", 2),
        ("Сочи на 2 дня в ноябре", 1),
        ("Сочи, сократить траты на 3 дня, на 4 ночи в ноябре", 4),
    ],
)
def test_pair_and_days_rule(store, text, nights):
    assert say(store, text).plan.spec.nights == nights


def test_pair_of_days_later_is_no_length(store):
    assert say(store, "Сочи в ноябре на пару дней позже").plan.spec.nights != 2
