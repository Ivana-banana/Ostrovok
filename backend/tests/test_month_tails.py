"""#220: tails of a month negation: a dash, «не дорого» as a wish, «ли» in a doubt.

Rules only, no model (TRAP-1). Today is fixed: Tuesday 2026-10-27.
"""

from datetime import date

import pytest
from talk import say

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


def named(store):
    r = say(store, "Сочи из Москвы с 3 по 8 декабря, двое взрослых")
    assert r.stage == "compare"
    return r


@pytest.mark.parametrize(
    "text",
    ["в январе — не дорого", "в январе - не дорого", "в январе – не дорого", "январь — не жарко"],
)
def test_a_dash_before_a_neighbouring_negation_is_no_refusal(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3) and "Они остаются" not in r.reply


@pytest.mark.parametrize(
    "text",
    ["в январе — не", "январь — не вариант", "в январе — не поедем", "в январе — не получится"],
)
def test_a_dash_before_a_refusal_stays_a_refusal(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2026, 12, 3), date(2026, 12, 8), 12)
    assert "Они остаются" in r.reply


@pytest.mark.parametrize(
    "text", ["не дорого", "не дорого, в январе", "не очень дорого", "в январе — не дорого"]
)
def test_not_expensive_in_two_words_is_the_wish_cheap(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    assert "недорого" in r.plan.spec.wishes


def test_not_expensive_with_a_refused_month_is_still_the_wish(store):
    first = named(store)
    r = say(store, "не в январе, недорого", first.session_id)
    assert r.plan.spec.wishes == ["недорого"] and r.plan.spec.date_from.month == 12


@pytest.mark.parametrize("text", ["не дороже 100 тысяч", "не в январе"])
def test_other_negations_are_no_wish_cheap(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    assert "недорого" not in r.plan.spec.wishes


@pytest.mark.parametrize(
    "text",
    ["в январе, а не поедем ли в феврале", "в январе, а может не поедем ли в феврале"],
)
def test_a_doubt_between_two_months_asks_which_and_moves_nothing(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2026, 12, 3), date(2026, 12, 8), 12)
    assert "Январь или февраль?" in r.reply
    assert r.questions and len(r.questions[0].options) == 2
    assert not any(c.what in ("даты", "месяц") for c in r.plan.changes)
