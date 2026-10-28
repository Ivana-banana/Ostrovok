"""#176: «давай на месяц позже», «на полгода раньше», «на год вперёд» move the dates (calendar shift).

Rules only, no model (TRAP-1). Today is fixed: Tuesday 2026-10-27.
"""

from datetime import date

import pytest
from talk import say

from api import llm
from api.contracts import TripSpec
from api.dag import is_shift_note, shift_dates
from api.extract import too_long_note
from api.store import SessionStore


@pytest.fixture(autouse=True)
def today(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 27)

    monkeypatch.setattr("api.extract.date", FixedDate)
    monkeypatch.setattr("api.dag.date", FixedDate)


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def plan_for(store, dates="с 3 по 8 ноября"):
    return say(store, f"Сочи из Москвы {dates}, двое взрослых")


def spec_of(start, end, **kw):
    return TripSpec(
        destinations=["Сочи"],
        date_from=start,
        date_to=end,
        nights=(end - start).days,
        month=start.month,
        **kw,
    )


def dates_change(r):
    return next(c for c in r.plan.changes if c.what == "даты")


@pytest.mark.parametrize(
    "text", ["давай на месяц позже", "сдвинь на месяц позже", "на месяц вперёд"]
)
def test_a_month_later_moves_the_dates_and_keeps_the_nights(store, text):
    first = plan_for(store)
    assert first.stage == "compare"
    r = say(store, text, first.session_id)
    assert r.stage == "compare" and not r.cart.items
    assert r.plan.spec.date_from == date(2026, 12, 3) and r.plan.spec.date_to == date(2026, 12, 8)
    assert r.plan.spec.nights == 5
    change = dates_change(r)
    assert "03.11" in change.before and "03.12" in change.after
    assert "Было → стало" in r.reply and "03.12–08.12" in r.reply
    assert "Не понял" not in r.reply


def test_half_a_year_earlier(store):
    first = plan_for(store, "с 3 по 8 мая")
    assert first.plan.spec.date_from == date(2027, 5, 3)
    r = say(store, "а можно на полгода раньше?", first.session_id)
    assert r.plan.spec.date_from == date(2026, 11, 3) and r.plan.spec.date_to == date(2026, 11, 8)
    assert r.plan.spec.nights == 5
    change = dates_change(r)
    assert "03.05.2027" in change.before and "03.11" in change.after


def test_a_year_forward_and_two_months(store):
    first = plan_for(store)
    r = say(store, "на год вперёд", first.session_id)
    assert r.plan.spec.date_from == date(2027, 11, 3) and r.plan.spec.nights == 5
    r = say(store, "на два месяца позже", r.session_id)
    assert r.plan.spec.date_from == date(2028, 1, 3) and r.plan.spec.date_to == date(2028, 1, 8)


def test_the_end_of_the_month_goes_to_its_last_day(store):
    first = plan_for(store, "с 31 января по 3 февраля")
    assert first.plan.spec.date_from == date(2027, 1, 31)
    r = say(store, "давай на месяц позже", first.session_id)
    assert r.plan.spec.date_from == date(2027, 2, 28) and r.plan.spec.date_to == date(2027, 3, 3)
    assert r.plan.spec.nights == 3
    assert "28.02.2027" in dates_change(r).after


def test_leap_february():
    spec = spec_of(date(2028, 1, 31), date(2028, 2, 3))
    moved = shift_dates(spec, "на месяц позже")
    assert (moved.date_from, moved.date_to) == (date(2028, 2, 29), date(2028, 3, 3))
    back = shift_dates(spec_of(date(2028, 3, 31), date(2028, 4, 2)), "на месяц раньше")
    assert back.date_from == date(2028, 2, 29) and back.nights == 2


def test_a_short_stay_at_the_month_end_keeps_its_nights():
    # 29.01 + 1 month and 31.01 + 1 month would both be 28.02: the end follows the start
    moved = shift_dates(spec_of(date(2027, 1, 29), date(2027, 1, 31)), "на месяц позже")
    assert (moved.date_from, moved.date_to) == (date(2027, 2, 28), date(2027, 3, 2))


def test_one_end_moves_by_a_month():
    spec = spec_of(date(2026, 11, 3), date(2026, 11, 8))
    moved = shift_dates(spec, "верни обратно на месяц позже")
    assert moved.date_from == date(2026, 11, 3) and moved.date_to == date(2026, 12, 8)
    assert moved.nights == 35


@pytest.mark.parametrize(
    ("text", "note"),
    [
        ("на месяц позже", "даты: сдвинуты на 1 мес. позже по вашей просьбе"),
        ("на 3 месяца раньше", "даты: сдвинуты на 3 мес. раньше по вашей просьбе"),
        ("на полгода позже", "даты: сдвинуты на 6 мес. позже по вашей просьбе"),
        ("на год позже", "даты: сдвинуты на 1 год позже по вашей просьбе"),
        ("на два года позже", "даты: сдвинуты на 2 года позже по вашей просьбе"),
        ("на 5 лет позже", None),
        ("на 5 дней позже", "даты: сдвинуты на 5 дн. позже по вашей просьбе"),
    ],
)
def test_the_shift_note_repeats_the_unit(text, note):
    spec = spec_of(date(2027, 11, 3), date(2027, 11, 8))
    notes = [a for a in shift_dates(spec, text).assumptions if is_shift_note(a)]
    assert notes == ([note] if note else [])


def test_the_notes_keep_the_running_sum_per_unit():
    spec = spec_of(date(2027, 11, 3), date(2027, 11, 8))
    once = shift_dates(spec, "на полгода позже")
    twice = shift_dates(once, "ещё на полгода позже и на два дня раньше")
    notes = sorted(a for a in twice.assumptions if is_shift_note(a))
    assert notes == [
        "даты: сдвинуты на 1 год позже по вашей просьбе",
        "даты: сдвинуты на 2 дн. раньше по вашей просьбе",
    ]


@pytest.mark.parametrize("text", ["беру, на месяц вперёд спасибо", "да, оплачу на месяц позже"])
def test_a_trailing_remark_stays_a_consent(store, text):
    first = plan_for(store)
    r = say(store, text, first.session_id)
    assert r.stage == "assemble"
    assert r.plan.spec.date_from == first.plan.spec.date_from
    assert not [c for c in r.plan.changes if c.what == "даты"]


@pytest.mark.parametrize(
    "text",
    [
        "берём, но на месяц позже",
        "оплачу, только сдвинь на месяц позже",
        "возьму, если на месяц позже",
        "давай на месяц позже, беру",
        "беру на месяц позже",
        "беру, на месяц позже",
    ],
)
def test_a_booking_word_does_not_hide_the_shift(store, text):
    first = plan_for(store)
    r = say(store, text, first.session_id)
    assert r.stage == "compare" and not r.cart.items
    assert r.plan.spec.date_from == date(2026, 12, 3) and r.plan.spec.nights == 5


@pytest.mark.parametrize("text", ["подтверждаю, на месяц позже", "подтверждаю на месяц позже"])
def test_a_shift_on_checkout_is_no_booking(store, text):
    r = plan_for(store)
    for step_text in ("беру", "да"):
        r = say(store, step_text, r.session_id)
    assert r.stage == "checkout" and r.cart.items
    r = say(store, text, r.session_id)
    assert r.stage == "compare" and r.voucher is None and not r.cart.items
    assert r.plan.spec.date_from == date(2026, 12, 3) and r.plan.spec.nights == 5
    assert "Было → стало" in r.reply


def test_a_year_in_the_diminutive_shifts_by_a_year(store):
    first = plan_for(store)
    r = say(store, "давай на годик позже", first.session_id)
    assert r.plan.spec.date_from == date(2027, 11, 3)


def test_days_forward_in_the_first_message_are_a_length_as_before(store):
    r = say(store, "Сочи на неделю вперёд, двое взрослых")
    assert (r.plan.spec.date_to - r.plan.spec.date_from).days == 7
    assert r.plan.spec.nights == 7
    assert not any("по вашей просьбе" in a for a in r.plan.spec.assumptions)
    r = say(store, "Сочи с 10 ноября на 5 дней вперёд, двое взрослых")
    assert r.plan.spec.date_from == date(2026, 11, 10) and r.plan.spec.nights == 4
    assert not any("по вашей просьбе" in a for a in r.plan.spec.assumptions)


def test_the_length_note_goes_with_a_dropped_length(store):
    first = say(store, "Сочи на 5 ночей в ноябре, двое взрослых")
    before = [a for a in first.plan.spec.assumptions if a.startswith("длительность")]
    r = say(store, "а на два дня вперёд?", first.session_id)
    assert r.plan.spec.nights == 5
    after = [a for a in r.plan.spec.assumptions if a.startswith("длительность")]
    assert after == before
    assert not any("1 ночь" in a for a in after)


def test_a_month_shift_on_assemble_moves_the_dates(store):
    second = say(store, "беру", plan_for(store).session_id)
    assert second.stage == "assemble"
    r = say(store, "давай на месяц позже", second.session_id)
    assert r.stage == "compare" and not r.cart.items
    assert r.plan.spec.date_from == date(2026, 12, 3)


def test_the_model_sees_a_shift_but_cannot_move_the_dates(monkeypatch, store):
    first = plan_for(store)
    calls = []

    def stub(message, spec, until=None, today=None):
        calls.append(message)
        return {"date_from": date(2027, 6, 1), "nights": 12}

    monkeypatch.setattr(llm, "extract_spec", stub)
    r = say(store, "давай на месяц позже", first.session_id)
    assert calls == ["давай на месяц позже"]
    assert r.plan.spec.date_from == date(2026, 12, 3) and r.plan.spec.nights == 5


def test_an_odd_amount_still_gets_the_hint(store):
    first = plan_for(store)
    r = say(store, "давай на полтора месяца позже", first.session_id)
    assert "Не понял" in r.reply and "на месяц позже" in r.reply
    assert r.plan.spec.date_from == first.plan.spec.date_from


@pytest.mark.parametrize("text", ["на месяц раньше", "на 10 дней раньше", "на три недели раньше"])
def test_a_shift_into_the_recent_past_is_refused(store, text):
    # The year of a shift is known: 03.11 minus 10 days is 24.10.2026, not 2027
    first = plan_for(store)
    r = say(store, text, first.session_id)
    assert r.plan.spec.date_from == first.plan.spec.date_from
    assert r.plan.spec.date_to == first.plan.spec.date_to
    assert "Заезд пришёлся бы на" in r.reply and "2026: эта дата уже прошла" in r.reply
    assert "ничего не применено" in r.reply
    assert not r.plan.changes and not any("уже прошло" in a for a in r.plan.spec.assumptions)


def test_a_shift_far_into_the_past_is_refused(store):
    first = plan_for(store)
    r = say(store, "на полгода раньше", first.session_id)
    assert r.plan.spec.date_from == first.plan.spec.date_from
    assert "ничего не применено" in r.reply and "уже прошл" in r.reply
    assert not r.plan.changes


def test_a_shift_to_today_is_no_past(store):
    first = plan_for(store)
    r = say(store, "на неделю раньше", first.session_id)
    assert r.plan.spec.date_from == date(2026, 10, 27)
    assert not any("уже прошло" in a for a in r.plan.spec.assumptions)


def test_the_length_note_has_the_right_plural(store):
    first = say(store, "Сочи на 1 ночь в ноябре, двое взрослых")
    assert first.plan.spec.nights == 1
    r = say(store, "с 1 ноября по 15 февраля", first.session_id)
    assert any(a.endswith("взято 1 ночь") for a in r.plan.spec.assumptions)
    assert not any("1 ночей" in a for a in r.plan.spec.assumptions)


@pytest.mark.parametrize(
    ("n", "word"),
    [(1, "ночь"), (2, "ночи"), (5, "ночей"), (11, "ночей"), (21, "ночь"), (7, "ночей")],
)
def test_too_long_note_plural(n, word):
    assert too_long_note("слишком долго", n).endswith(f"взято {n} {word}")
