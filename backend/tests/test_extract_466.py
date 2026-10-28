"""#466: tails of the landmark wish (#338) — a two-word name, «после 2 часов дня», a landmark that is the destination."""

import pytest

from api.extract import extract


@pytest.mark.parametrize(
    ("text", "wish"),
    [
        ("Отель рядом с Красной площадью, Москва, 3 ночи", "рядом: Красной площадью"),
        ("Питер, отель около Невского проспекта", "рядом: Невского проспекта"),
        ("Казань, отель у Технопарка", "рядом: Технопарка"),
    ],
)
def test_trap_3_landmark_of_two_words_keeps_the_noun(text, wish):
    wishes = extract(text)[0]["wishes"]
    assert wish in wishes and not any(w.startswith("рядом: ") and w != wish for w in wishes)


@pytest.mark.parametrize(
    "text", ["Сочи, заселение после 2 часов дня", "Сочи, заселение после 3 часов дня"]
)
def test_check_in_after_an_afternoon_hour_is_no_late_check_in(text):
    assert "поздний заезд" not in extract(text)[0].get("wishes", [])


@pytest.mark.parametrize("text", ["Казань, заселение после 2 ночи", "Сочи, заселение после 23:00"])
def test_check_in_at_night_stays_a_late_check_in(text):
    assert "поздний заезд" in extract(text)[0]["wishes"]


@pytest.mark.parametrize("text", ["Иркутск, хочу жить у Байкала", "Байкал, жить у Байкала"])
def test_trap_3_landmark_equal_to_a_destination_is_no_landmark(text):
    found, _ = extract(text)
    assert "Байкал" in found["destinations"]
    assert not any(w.startswith("рядом: ") for w in found.get("wishes", []))
