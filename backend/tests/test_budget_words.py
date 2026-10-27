"""#158: a budget is never read wrong in silence.

Foreign currency is not turned into rubles. «На каждого» and «в сутки» multiply, and the
assumption says so. «Полмиллиона» and «миллион» are numbers. Without a model (TRAP-1).
"""

import pytest

from api.contracts import ChatRequest
from api.extract import extract
from api.model import chat
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def budget_notes(notes):
    return [n for n in notes if n.startswith("бюджет:")]


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("Сочи в ноябре, бюджет 1000 евро", "евро"),
        ("Сочи в ноябре, бюджет €1000", "евро"),
        ("Сочи в ноябре, $1500", "долларах"),
        ("Сочи в ноябре, бюджет 1500 долларов", "долларах"),
        ("Сочи в ноябре, бюджет 1500$", "долларах"),
        ("Сочи в ноябре, до 2 тысяч евро", "евро"),
        ("Сочи в ноябре, бюджет 3к usd", "долларах"),
    ],
)
def test_foreign_currency_is_no_rubles(text, currency):
    found, notes = extract(text)
    assert "budget_rub" in found and found["budget_rub"] is None  # the model cannot fill it
    (note,) = budget_notes(notes)
    assert currency in note and "не пересчитываю" in note and "в рублях" in note


def test_foreign_currency_end_to_end_keeps_budget_unset(store):
    reply = say(store, "Сочи в ноябре, бюджет 1000 евро")
    spec = reply.plan.spec
    assert spec.budget_rub is None
    assert any("не пересчитываю" in a for a in spec.assumptions)
    assert not any(a.startswith("бюджет: не задан") for a in spec.assumptions)
    assert "не укладывается в бюджет" not in reply.reply


@pytest.mark.parametrize(
    ("text", "budget"),
    [
        ("бюджет 100 000 руб, отель у моря, как в Европе, 5 евро кофе", 100_000),
        ("бюджет 150 тысяч рублей, это около 1500 евро", 150_000),
        ("80 тыс руб, виза 80 евро", 80_000),
        ("100к руб и 200 евро наличными", 100_000),
        ("бюджет 100 тысяч, 5 ночей, 2 евро чаевые", 100_000),
        ("Стамбул, 100 тысяч, обмен 1 лира", 100_000),
        ("бюджет 100 тысяч, билеты по $200", 100_000),
        ("бюджет 90 тысяч, кофе 3 евро, отель 5000 ₽ за ночь", 90_000),
    ],
)
def test_ruble_sum_wins_over_a_foreign_word_elsewhere(text, budget):
    found, notes = extract(text)
    assert found["budget_rub"] == budget and not budget_notes(notes)


def test_a_new_ruble_budget_replaces_the_foreign_note(store):
    first = say(store, "Сочи в ноябре, бюджет 1000 евро")
    second = say(store, "бюджет 90 тысяч рублей", first.session_id)
    spec = second.plan.spec
    assert spec.budget_rub == 90_000
    assert not any("не пересчитываю" in a for a in spec.assumptions)


@pytest.mark.parametrize(
    "text",
    [
        "по 50 тысяч на каждого, нас двое",
        "бюджет по 50 тысяч на каждого, вдвоём",
        "50 000 ₽ на человека, едем вдвоём",
        "50к с человека, вдвоём",
    ],
)
def test_per_person_budget_multiplies_by_adults(text):
    found, notes = extract(text)
    assert found["adults"] == 2 and found["budget_rub"] == 100_000
    (note,) = budget_notes(notes)
    assert "50 000" in note and "2 взрослых" in note


def test_per_person_without_a_party_word_uses_the_spec_adults():
    found, notes = extract("по 50 тысяч на каждого", adults=3)
    assert found["budget_rub"] == 150_000 and "3 взрослых" in budget_notes(notes)[0]


def test_per_person_end_to_end(store):
    spec = say(store, "Сочи в ноябре, по 50 тысяч на каждого, вдвоём").plan.spec
    assert spec.budget_rub == 100_000 and spec.adults == 2
    assert "бюджет: 50 000 ₽ × 2 взрослых" in spec.assumptions


def test_per_child_does_not_multiply():
    found, notes = extract("вдвоём с ребёнком 5 лет, по 30 тысяч на каждого ребёнка")
    assert found["budget_rub"] == 30_000 and not budget_notes(notes)


def test_children_are_not_counted_in_per_person():
    found, notes = extract("2 взрослых и ребёнок 5 лет, по 40 тысяч на человека")
    assert found["adults"] == 2 and found["budget_rub"] == 80_000
    assert "дети не считаются" in budget_notes(notes)[0]


def test_per_night_budget_multiplies_by_nights():
    found, notes = extract("Сочи на 5 ночей, до 3000 рублей в сутки")
    assert found["nights"] == 5 and found["budget_rub"] == 15_000
    (note,) = budget_notes(notes)
    assert "3 000" in note and "× 5 ноч" in note


def test_per_night_with_unknown_nights_uses_the_spec_nights_and_says_so():
    found, notes = extract("до 3000 рублей в сутки", nights=4)
    assert found["budget_rub"] == 12_000 and "4 ноч" in budget_notes(notes)[0]
    found, notes = extract("до 3000 рублей в сутки")
    assert found["budget_rub"] == 21_000 and "из плана" in budget_notes(notes)[0]


def test_per_night_end_to_end(store):
    spec = say(store, "Сочи в ноябре на 4 ночи, до 3000 рублей в сутки").plan.spec
    assert spec.nights == 4 and spec.budget_rub == 12_000
    assert any(a.startswith("бюджет: 3 000 ₽ × 4 ноч") for a in spec.assumptions)


def test_per_person_and_per_night_together():
    found, notes = extract("вдвоём на 3 ночи, 2000 ₽ на человека в сутки")
    assert found["budget_rub"] == 12_000
    assert "× 2 взрослых" in budget_notes(notes)[0] and "× 3 ноч" in budget_notes(notes)[0]


@pytest.mark.parametrize(
    ("text", "budget"),
    [
        ("бюджет полмиллиона", 500_000),
        ("потолок миллион", 1_000_000),
        ("до миллиона рублей", 1_000_000),
        ("бюджет полтора миллиона", 1_500_000),
        ("бюджет миллион", 1_000_000),
        ("Сочи, лимит полмиллиона на всё", 500_000),
    ],
)
def test_million_words(text, budget):
    found, notes = extract(text)
    assert found["budget_rub"] == budget and not budget_notes(notes)


def test_million_words_end_to_end(store):
    assert say(store, "Сочи в ноябре, бюджет полмиллиона").plan.spec.budget_rub == 500_000


@pytest.mark.parametrize(
    "text",
    [
        "до моря 1000 км",
        "у нас цена выросла в 2 раза",
        "прошли 50 тысяч шагов",
        "хочу уехать 1 мая",
        "миллион впечатлений",
        "ехать в сутки пути, а потом отдых",
        "в 3 раза дешевле",
    ],
)
def test_no_false_budget(text):
    found, notes = extract(text)
    assert "budget_rub" not in found and not budget_notes(notes)


def test_per_night_word_far_from_the_sum_is_no_multiplier():
    found, notes = extract("Сочи в сутки не уложимся, бюджет 80 тысяч, 5 ночей")
    assert found["budget_rub"] == 80_000 and not budget_notes(notes)


def test_plain_budgets_stay():
    for text, budget in [
        ("бюджет 80 тысяч", 80_000),
        ("до 50 тысяч на всё", 50_000),
        ("150к на всё", 150_000),
        ("итог 150 000 ₽", 150_000),
        ("вдвоём, 60 тысяч на двоих", 60_000),
    ]:
        found, notes = extract(text)
        assert found["budget_rub"] == budget and not budget_notes(notes), text


def test_a_sum_that_counts_steps_is_skipped_for_the_next_sum():
    assert extract("прошли 50 тысяч шагов, бюджет 80 тысяч")[0]["budget_rub"] == 80_000
