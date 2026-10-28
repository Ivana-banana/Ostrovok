"""#424: own tickets on a re-plan — the shift moves their times, «подбери перелёт» ends them,
the clock time holds on the dates of the model and on «14.11». Offline (TRAP-1)."""

import json
from datetime import date, time
from pathlib import Path

import pytest
from talk import say

from api import llm
from api.contracts import ChatReply
from api.fixtures import future_date
from api.store import SessionStore

OWN = "Билеты уже есть, прилетаем в Калининград 14 ноября в 01:30, улетаем 18-го в 15:00, из Москвы, двое взрослых"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GIGACHAT_AUTH_KEY", raising=False)
    return SessionStore(tmp_path / "sessions.db")


def test_shift_moves_the_own_arrival_and_departure(store):
    r = say(store, OWN)
    was = r.plan.spec
    spec = say(store, "сдвинь на 2 дня позже", r.session_id).plan.spec
    assert (spec.date_from - was.date_from).days == 2
    assert spec.arrive_at.date() == spec.date_from and spec.arrive_at.time() == time(1, 30)
    assert (spec.leave_at - was.leave_at).days == 2 and spec.leave_at.time() == time(15, 0)


def test_pick_a_flight_ends_the_own_tickets(store):
    r = say(store, OWN)
    assert r.plan.spec.own_transport
    r = say(store, "подбери перелёт", r.session_id)
    spec = r.plan.spec
    assert not spec.own_transport and spec.arrive_at is None and spec.leave_at is None
    assert [o for o in r.plan.offers if o.kind == "transport"]


def test_clock_time_holds_on_the_date_only_the_model_found(monkeypatch, store):
    day = future_date(11, 20)
    monkeypatch.setattr(
        llm, "extract_spec", lambda m, s, until=None, today=None: {"date_from": day, "nights": 3}
    )
    r = say(store, "Билеты уже есть, прилетаем в Калининград в 01:30, из Москвы, двое взрослых")
    spec = r.plan.spec
    assert spec.own_transport and spec.date_from == day
    assert spec.arrive_at.date() == day and spec.arrive_at.time() == time(1, 30)


def test_day_with_a_dot_sets_the_own_arrival(store):
    r = say(
        store, "Билеты уже есть, Калининград, прилетаем 14.11 в 01:30, из Москвы, двое взрослых"
    )
    spec = r.plan.spec
    assert spec.date_from == future_date(11, 14)
    assert spec.arrive_at.date() == future_date(11, 14) and spec.arrive_at.time() == time(1, 30)


def test_fixture_round_trips_a_set_arrival():
    path = Path(__file__).resolve().parents[2] / "tests/fixtures/chat_reply.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    spec = ChatReply.model_validate(fixture).plan.spec
    assert spec.arrive_at is not None and spec.arrive_at.date() == date(2026, 12, 11)
