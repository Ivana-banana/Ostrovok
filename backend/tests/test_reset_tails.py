"""#98: tails of the reset phrases (S5, S6) — «давай сначала», «новая поездка», a dead end after a booking.

All run on rules, without a model (TRAP-1).
"""

import pytest
from talk import say

from api.store import SessionStore

SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def to_stage(store, stage):
    r = say(store, SOCHI)
    for text, want in (("беру", "assemble"), ("да", "checkout"), ("подтверждаю", "booked")):
        if r.stage == stage:
            break
        r = say(store, text, r.session_id)
        assert r.stage == want
    assert r.stage == stage
    return r


@pytest.mark.parametrize("stage", ["compare", "assemble", "checkout", "booked"])
@pytest.mark.parametrize(
    "text",
    [
        "давай сначала",
        "давай всё сначала",
        "давайте по новой",
        "давай по новой",
        "новая поездка",
        "Хочу новую поездку",
        "заново",
        "давай заново",
        "давай заново!",
    ],
)
def test_reset_phrases_start_over(store, stage, text):
    r = to_stage(store, stage)
    r = say(store, text, r.session_id)
    assert r.stage == "clarify" and r.plan.spec.destinations == []
    assert r.cart.items == [] and r.voucher is None and "Начинаем заново" in r.reply


@pytest.mark.parametrize(
    "text",
    [
        "давай заново посчитаем цену",
        "хочу новый запрос по отелю",
        "давай сначала посмотрим отели",
        "давай заново пересчитаем",
        "заново посчитай цену",
        "сколько стоит новая поездка",
        "это не новая поездка",
        "давай сначала?",
    ],
)
def test_reset_words_inside_a_sentence_keep_the_plan(store, text):
    r = to_stage(store, "checkout")
    again = say(store, text, r.session_id)
    assert "Начинаем заново" not in again.reply and again.stage != "clarify"
    assert again.plan.spec.destinations == r.plan.spec.destinations
    assert again.cart.items


def test_a_question_about_a_new_trip_is_not_a_reset(store):
    r = to_stage(store, "checkout")
    again = say(store, "а новая поездка будет дешевле?", r.session_id)
    assert "Начинаем заново" not in again.reply and again.stage != "clarify"
    assert again.plan.spec.destinations == r.plan.spec.destinations


def test_inspire_other_variants_never_repeat_silently(store):
    """«тёплое море в ноябре»: «другие варианты» show places not shown yet (fixed by #451).

    The end of the pool is told in words, see test_more_destinations.py.
    """
    r = say(store, "тёплое море в ноябре")
    assert r.stage == "inspire"
    seen = {d.name for d in r.plan.destinations}
    for _ in range(5):
        r = say(store, "другие варианты", r.session_id)
        names = {d.name for d in r.plan.destinations}
        assert r.stage == "inspire" and names and not names & seen
        seen |= names


@pytest.mark.parametrize("text", ["тогда Казань", "другое направление", "а если на 2 дня позже"])
def test_after_the_voucher_there_is_a_next_step(store, text):
    r = to_stage(store, "booked")
    again = say(store, text, r.session_id)
    assert f"Бронь {r.voucher.booking_id} уже оформлена" in again.reply
    assert "начать заново" in again.reply.lower()
    assert again.voucher == r.voucher


def test_after_the_voucher_the_chip_starts_over(store):
    r = to_stage(store, "booked")
    again = say(store, "а если на 2 дня позже", r.session_id)
    chip = again.questions[0].options[0]
    fresh = say(store, chip, r.session_id)
    assert fresh.stage == "clarify" and fresh.plan.spec.destinations == [] and fresh.voucher is None
    assert f"Бронь {r.voucher.booking_id} остаётся оформленной" in fresh.reply
