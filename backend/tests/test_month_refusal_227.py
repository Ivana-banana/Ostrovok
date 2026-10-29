"""#227: «январь или не едем» is a request for January, not a refusal; «январь нет, лучше февраль» asks about February.

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
    [
        "январь или не едем",
        "в январе, а то не едем",
        "январь подходит декабрь не подходит",
        "январь подходит, декабрь не подходит",
    ],
)
def test_a_neighbours_no_after_the_month_still_moves_the_dates(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2027, 1, 3), date(2027, 1, 8), 1)
    assert "Они остаются" not in r.reply


@pytest.mark.parametrize("text", ["в январе не поедем", "январь не подходит", "в январе не едем"])
def test_a_plain_refusal_stays_a_refusal(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2026, 12, 3), date(2026, 12, 8), 12)
    assert "Они остаются" in r.reply


@pytest.mark.parametrize(
    "text",
    [
        "в январе, нет, в феврале",
        "январь нет, лучше февраль",
        "в январе нет, давай в феврале",
        "январь — нет, лучше февраль",
        "в январе не, лучше в феврале",
        "не в январе, а в феврале",
    ],
)
def test_no_to_one_month_and_a_second_month_ask_about_the_second(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to) == (date(2026, 12, 3), date(2026, 12, 8))
    assert "Перенести на февраль" in r.reply and "январь" not in r.reply
    assert r.questions and r.questions[0].options == ["Перенести на февраль"]
