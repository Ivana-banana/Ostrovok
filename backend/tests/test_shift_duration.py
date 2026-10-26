"""#36: «Сдвинь на 3 дня» and «Продли на 2 дня» are not a trip length (review of PR #70)."""

import pytest

from api.extract import extract

# Verb + «на N дней/ночей/неделю»: a move of the dates or a change of the length, not a new length.
EDITS = [
    "Сдвинь на 3 дня",
    "Продли на 2 дня",
    "Продли поездку на 2 дня",
    "Продли на 2 ночи",
    "Продли на неделю",
    "Сократи на 2 дня",
    "Перенеси на 2 дня",
    "Передвинь на 3 дня",
    "Отложи на 3 дня",
    "Отодвинь на пару дней",
    "Удлини поездку на 3 дня",
]


@pytest.mark.parametrize("text", EDITS)
def test_edit_verb_with_days_is_not_a_new_length(text):
    found, notes = extract(text, has_destination=True)
    assert "nights" not in found and not any(n.startswith("длительность:") for n in notes)


@pytest.mark.parametrize(
    ("text", "nights"),
    [
        ("на 3 дня", 2),
        ("Сочи на 3 дня", 2),
        ("Поездка на 5 дней", 4),
        ("Только на 3 ночи", 3),
        ("Перенеси в Сочи на 3 дня", 2),  # the verb is far from the amount: a length
        ("Продли отель, 4 ночи", 4),
    ],
)
def test_plain_length_is_still_read(text, nights):
    assert extract(text, has_destination=True)[0].get("nights") == nights
