"""#190: a new month after named dates moves the dates: the month and the dates never disagree.

Rules only, no model (TRAP-1). Today is fixed: Tuesday 2026-10-27.
"""

from datetime import date

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore


@pytest.fixture(autouse=True)
def today(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 27)

    for module in ("extract", "dag", "model"):
        monkeypatch.setattr(f"api.{module}.date", FixedDate)


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def named(store, dates="с 3 по 8 декабря"):
    r = say(store, f"Сочи из Москвы {dates}, двое взрослых")
    assert r.stage == "compare"
    return r


def agree(r):
    s = r.plan.spec
    assert s.month == s.date_from.month
    assert s.nights == (s.date_to - s.date_from).days


def test_next_month_after_named_dates_moves_them_keeping_day_and_length(store):
    first = named(store)
    r = say(store, "лучше в январе", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.nights, s.month) == (
        date(2027, 1, 3),
        date(2027, 1, 8),
        5,
        1,
    )
    agree(r)
    change = next(c for c in r.plan.changes if c.what == "даты")
    assert "03.12" in change.before and "03.01" in change.after
    assert "Было → стало" in r.reply and "03.01.2027–08.01.2027" in r.reply
    assert "→ 03.12" not in r.reply and "03.12–08.12" not in r.reply.split("\n", 2)[2]
    assert not any(a.startswith("даты:") for a in s.assumptions)  # the dates stay named


def test_the_cart_dates_follow_the_new_month(store):
    first = named(store)
    r = say(store, "лучше в январе", first.session_id)
    r = say(store, "беру", r.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3)
    assert all("03.12" not in i.title for i in r.cart.items)


def test_summer_after_named_dates(store):
    first = named(store)
    r = say(store, "давай летом", first.session_id)
    s = r.plan.spec
    assert s.month == 6 and s.date_from == date(2027, 6, 3) and s.nights == 5
    agree(r)


def test_the_move_survives_the_next_message(store):
    first = named(store)
    r = say(store, "лучше в январе", first.session_id)
    r = say(store, "на 4 ночи", r.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3) and r.plan.spec.nights == 4


def test_the_end_of_the_month_is_clamped(store):
    first = named(store, "с 29 по 31 января")
    r = say(store, "лучше в феврале", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.nights) == (date(2027, 2, 28), date(2027, 3, 2), 2)
    agree(r)


def test_the_past_check_in_goes_to_next_year(store):
    first = named(store, "с 3 по 8 декабря")
    r = say(store, "лучше в октябре", first.session_id)  # 03.10.2026 is past
    assert r.plan.spec.date_from == date(2027, 10, 3)


def test_the_explicit_date_wins_over_the_month_move(store):
    first = named(store)
    r = say(store, "лучше в январе, с 10 по 15 января", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to) == (date(2027, 1, 10), date(2027, 1, 15))


def test_the_same_month_changes_nothing(store):
    first = named(store)
    r = say(store, "лучше в декабре", first.session_id)
    assert (r.plan.spec.date_from, r.plan.spec.date_to) == (date(2026, 12, 3), date(2026, 12, 8))


def test_guessed_dates_move_as_before(store):
    first = say(store, "Сочи из Москвы в ноябре, двое взрослых")
    guess = first.plan.spec.date_from
    assert any("точных дат не было" in a for a in first.plan.spec.assumptions)
    r = say(store, "лучше в январе", first.session_id)
    s = r.plan.spec
    assert s.month == 1 and s.date_from.month == 1 != guess.month
    assert any("точных дат не было" in a for a in s.assumptions)  # still a guess
    assert s.date_from.weekday() == 5  # the guess picks a Saturday
    agree(r)


@pytest.mark.parametrize(
    "text",
    [
        "не в январе, а в декабре",
        "только не в январе",
        "а в январе там холодно?",
        "в январе мы уже были в Сочи",
    ],
)
def test_a_month_that_is_no_request_changes_neither_dates_nor_month(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.nights, s.month) == (
        date(2026, 12, 3),
        date(2026, 12, 8),
        5,
        12,
    )
    assert not any(c.what in ("даты", "месяц") for c in r.plan.changes)
    agree(r)


def test_a_move_drops_the_old_notes_about_the_dates(store):
    first = named(store, "с 20 по 25 октября")  # a slip: the day passed a week ago
    assert any("уже прошло" in a for a in first.plan.spec.assumptions)
    r = say(store, "лучше в январе", first.session_id)
    s = r.plan.spec
    assert s.date_from == date(2027, 1, 20)
    assert not any("уже прошло" in a for a in s.assumptions)
    assert not any(a.startswith("даты:") for a in s.assumptions)


def test_a_move_drops_the_shift_note(store):
    first = named(store)
    r = say(store, "на неделю позже", first.session_id)
    assert any("сдвинуты" in a for a in r.plan.spec.assumptions)
    r = say(store, "лучше в январе", r.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 10)
    assert not any("сдвинуты" in a for a in r.plan.spec.assumptions)


def test_a_slip_into_the_past_by_a_move_says_so(store):
    first = named(store)
    r = say(store, "лучше в октябре", first.session_id)  # 03.10 passed three weeks ago
    s = r.plan.spec
    assert s.date_from == date(2027, 10, 3)
    assert "даты: 03.10 уже прошло — взят 2027 год" in s.assumptions
    r = say(store, "на 4 ночи", r.session_id)  # the named dates stay named
    assert r.plan.spec.date_from == date(2027, 10, 3)


def stays(r, first, stage="compare"):
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.nights, s.month) == (
        date(2026, 12, 3),
        date(2026, 12, 8),
        5,
        12,
    )
    assert not any(c.what in ("даты", "месяц") for c in r.plan.changes)
    assert r.stage == stage and not r.cart.items and r.plan.spec == first.plan.spec


@pytest.mark.parametrize(
    "text", ["январь не подходит", "в январе не получится", "в январе нельзя", "кроме января"]
)
def test_a_rejection_after_the_month_keeps_the_dates_and_is_no_consent(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    stays(r, first)
    assert "03.12–08.12" in r.reply and "остаются" in r.reply


@pytest.mark.parametrize(
    "text",
    [
        "давай в январе?",
        "может, в январе?",
        "можно в январе?",
        "в январе мы уже были в Сочи",
        "не в декабре, лучше в январе",
        "а в январе там холодно?",
    ],
)
def test_an_unclear_month_asks_and_is_no_consent(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    stays(r, first)
    assert "03.12–08.12 вы назвали сами" in r.reply and "Перенести на январь" in r.reply
    assert r.stage != "assemble"


def test_the_question_has_a_chip_that_moves_the_dates(store):
    first = named(store)
    r = say(store, "давай в январе?", first.session_id)
    assert "Перенести на январь (тот же день и длина) или назовёте даты?" in r.reply
    chip = r.questions[0].options[0]
    r = say(store, chip, r.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3) and r.plan.spec.nights == 5


def test_a_contrast_asks_about_the_month_after_the_comma(store):
    first = named(store)
    r = say(store, "не в январе, а в феврале", first.session_id)
    stays(r, first)
    assert "Перенести на февраль" in r.reply and "январь" not in r.reply


def test_a_contrast_with_the_own_month_keeps_the_dates(store):
    first = named(store)
    r = say(store, "не в январе, а в декабре", first.session_id)
    stays(r, first)
    assert "остаются" in r.reply


def test_a_month_among_other_words_is_still_a_request(store):
    first = named(store)
    r = say(store, "давай без пересадок, в январе", first.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3) and r.stage == "compare"


def test_dates_that_reach_into_the_month_are_not_moved(store):
    first = named(store, "с 30 декабря по 4 января")
    r = say(store, "лучше в январе", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to) == (date(2026, 12, 30), date(2027, 1, 4))
    assert "заходят в январь" in r.reply and r.stage == "compare"


@pytest.mark.parametrize("text", ["отпуск до января", "после января"])
def test_a_limit_word_before_the_month_is_no_request(store, text):
    first = named(store)
    r = say(store, f"бюджет 100 тысяч, {text}", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2026, 12, 3), date(2026, 12, 8), 12)
