"""#176: «давай на месяц позже», «на полгода раньше», «на год вперёд» move the dates (calendar shift).

Rules only, no model (TRAP-1). Today is fixed: Tuesday 2026-10-27.
"""

from datetime import date

import pytest

from api import llm
from api.contracts import ChatRequest, TripSpec
from api.dag import is_shift_note, shift_dates
from api.extract import too_long_note
from api.model import chat
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


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


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


def test_the_shift_note_counts_days():
    moved = shift_dates(spec_of(date(2026, 11, 3), date(2026, 11, 8)), "на месяц позже")
    notes = [a for a in moved.assumptions if is_shift_note(a)]
    assert notes == ["даты: сдвинуты на 30 дн. позже по вашей просьбе"]


@pytest.mark.parametrize("text", ["беру, на месяц вперёд спасибо", "да, оплачу на месяц позже"])
def test_a_trailing_remark_stays_a_consent(store, text):
    first = plan_for(store)
    r = say(store, text, first.session_id)
    assert r.stage == "assemble"
    assert r.plan.spec.date_from == first.plan.spec.date_from
    assert not [c for c in r.plan.changes if c.what == "даты"]


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


def test_a_shift_into_the_recent_past_goes_to_next_year_with_a_note(store):
    # TRAP-5 neighbour of #159: 03.10 passed 24 days ago, the same rule as for a named day
    first = plan_for(store)
    r = say(store, "на месяц раньше", first.session_id)
    assert r.plan.spec.date_from == date(2027, 10, 3) and r.plan.spec.date_to == date(2027, 10, 8)
    assert any("уже прошло" in a and "2027" in a for a in r.plan.spec.assumptions)
    assert "уже прошло" in r.reply


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
