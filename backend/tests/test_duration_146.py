"""#146 (D-038): «на пару ночей» is 2 nights; «на пару дней позже» in a first message is no length."""

import pytest
from talk import say

from api.extract import DEFAULT_NIGHTS
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
        ("Сочи, сократить траты на 3 дня в ноябре", 2),
        ("Сочи в ноябре дней на 5", 4),
        ("Сочи в ноябре на 5 дней", 4),
        ("Сочи, сократить траты на 3 дня, на 4 ночи в ноябре", 4),
    ],
)
def test_pair_and_days_rule(store, text, nights):
    assert say(store, text).plan.spec.nights == nights


@pytest.mark.parametrize("shift", ["дней позже", "ночей позже", "дней раньше", "ночей раньше"])
def test_pair_shift_is_no_length(store, shift):
    text = f"Сочи в ноябре на пару {shift}"
    assert say(store, text).plan.spec.nights == DEFAULT_NIGHTS
