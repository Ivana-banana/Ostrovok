"""#167: a foreign sum is no rubles also after «за/на/по», «у.е.» is no ruble, «в сутки» recounts.

Without a model (TRAP-1). The model must not write 1000 euro as 1000 rubles (TRAP-4).
"""

import pytest
from talk import say

from api.extract import extract
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def budget_notes(notes):
    return [n for n in notes if n.startswith("бюджет:")]


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("в Европу за 2000 евро", "евро"),
        ("Сочи на 2000 долларов", "долларах"),
        ("Сочи, 1000 евро на двоих", "евро"),
        ("Сочи, по 500 евро на каждого", "евро"),
        ("Сочи, 1000 евро на человека", "евро"),
        ("Сочи на 5 ночей, 1000 евро в сутки", "евро"),
        ("Сочи, по 100 долларов за ночь", "долларах"),
        ("бюджет 2000 у.е.", "у.е."),
        ("Сочи, бюджет 2000 у. е.", "у.е."),
        ("Сочи, 2000 уе на двоих", "у.е."),
        ("Сочи, до 3 тыс. у.е.", "у.е."),
        ("Сочи, потолок 2000 условных единиц", "у.е."),
    ],
)
def test_foreign_sum_is_no_rubles(text, currency):
    found, notes = extract(text)
    assert "budget_rub" in found and found["budget_rub"] is None
    (note,) = budget_notes(notes)
    assert currency in note and "не пересчитываю" in note


@pytest.mark.parametrize(
    "text",
    [
        "Сочи, виза за 80 евро, бюджет 100 тысяч",
        "бюджет 100 тысяч, кофе по 3 евро",
        "Сочи на 5 ночей, 3000 рублей в сутки",
        "Сочи за 50 тысяч рублей",
    ],
)
def test_ruble_sum_is_still_rubles(text):
    found, notes = extract(text)
    assert found.get("budget_rub") is not None and not budget_notes(
        [n for n in notes if "не пересчитываю" in n]
    )


def test_foreign_per_night_end_to_end_keeps_budget_unset(store):
    spec = say(store, "Сочи в ноябре, 1000 евро в сутки").plan.spec
    assert spec.budget_rub is None
    assert any("не пересчитываю" in a for a in spec.assumptions)


def test_per_night_budget_follows_a_new_number_of_nights(store):
    first = say(store, "Сочи в ноябре на 4 ночи, до 3000 рублей в сутки")
    assert first.plan.spec.budget_rub == 12_000
    second = say(store, "лучше на 6 ночей", first.session_id)
    spec = second.plan.spec
    assert spec.nights == 6 and spec.budget_rub == 18_000
    assert any(a.startswith("бюджет: 3 000 ₽ × 6 ноч") for a in spec.assumptions)
    assert not any("× 4 ноч" in a for a in spec.assumptions)


def test_per_night_budget_with_party_follows_nights(store):
    first = say(store, "Сочи в ноябре вдвоём на 3 ночи, 2000 ₽ на человека в сутки")
    assert first.plan.spec.budget_rub == 12_000
    spec = say(store, "на 5 ночей", first.session_id).plan.spec
    assert spec.nights == 5 and spec.budget_rub == 20_000


def test_a_total_budget_does_not_follow_nights(store):
    first = say(store, "Сочи в ноябре на 4 ночи, бюджет 60 тысяч рублей")
    spec = say(store, "лучше на 6 ночей", first.session_id).plan.spec
    assert spec.nights == 6 and spec.budget_rub == 60_000


def test_a_new_budget_in_the_same_turn_wins(store):
    first = say(store, "Сочи в ноябре на 4 ночи, до 3000 рублей в сутки")
    spec = say(store, "на 6 ночей, бюджет 50 тысяч", first.session_id).plan.spec
    assert spec.nights == 6 and spec.budget_rub == 50_000


@pytest.mark.parametrize(
    "text",
    [
        "а виза за 80 евро нужна",
        "а там кофе по 3 евро?",
        "Стамбул, экскурсия по 30 долларов",
        "Сочи, виза за 80 евро",
        "билет в музей за 15 евро",
        "такси из аэропорта за 40 евро",
        "сколько стоит страховка около 50 евро за неделю",
        "отель за 100 долларов",
    ],
)
def test_price_of_an_item_is_no_budget(text):
    found, notes = extract(text)
    assert "budget_rub" not in found and not budget_notes(notes)


def test_item_price_keeps_the_session_budget(store):
    first = say(store, "Сочи в ноябре на 4 ночи, бюджет 100 тысяч")
    assert first.plan.spec.budget_rub == 100_000
    for text in ("а виза за 80 евро нужна", "а там кофе по 3 евро?"):
        spec = say(store, text, first.session_id).plan.spec
        assert spec.budget_rub == 100_000
        assert not any("не пересчитываю" in a for a in spec.assumptions)


def test_per_night_budget_of_one_night_follows_new_nights(store):
    first = say(store, "Сочи в ноябре на 1 ночь, 3000 ₽ в сутки")
    assert first.plan.spec.nights == 1 and first.plan.spec.budget_rub == 3_000
    spec = say(store, "на 5 ночей", first.session_id).plan.spec
    assert spec.nights == 5 and spec.budget_rub == 15_000
