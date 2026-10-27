"""#364: critic cards C10 and C14 (eval/cases_critic.json). Offline, rules only (TRAP-1).

C10: Ольхон (Хужир) is reached from Иркутск (IKT): 256 km by road and the ferry МРС — Ольхон
(Wikipedia «Ольхон», research/domain.md), not from UUD across the lake.
C14: every time in the reply carries its zone (TRAP-5); a leg shows its duration, so 08:45 ->
10:30 between UTC+10 and UTC+2 reads as 9 h 45 min, not 1 h 45 min.
"""

import re

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore

C10 = "На Ольхон в августе вдвоём на 5 ночей из Москвы"
C14 = "Летим вдвоём из Владивостока в Калининград на 4 ночи с 12 декабря"
TIME = re.compile(r"\b\d{2}\.\d{2} \d{2}:\d{2}\b")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GIGACHAT_AUTH_KEY", raising=False)
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def chosen(r, kind):
    by_id = {o.offer_id: o for o in r.plan.offers}
    return [by_id[i] for i in r.plan.selected if by_id[i].kind == kind]


def test_C10_olkhon_flies_to_irkutsk_with_the_ferry_road(store):
    r = say(store, C10)
    assert r.stage == "compare"
    legs = chosen(r, "transport")
    assert legs and all("IKT" in x.title and "UUD" not in x.title for x in legs)
    transfers = chosen(r, "transfer")
    assert transfers
    for x in transfers:
        km = float(re.search(r"≈(\d+(?:\.\d+)?) км", x.title).group(1))
        assert km >= 250 and "паром" in x.title
    assert "IKT" in r.reply and "UUD" not in r.reply


def test_C10_TRAP_5_olkhon_plan_has_no_timeline_error(store):
    r = say(store, C10)
    r = say(store, "беру", r.session_id)
    assert r.stage == "assemble"
    assert not [i for i in r.plan.issues if i.severity == "error"]


def test_C14_TRAP_5_compare_reply_times_carry_zones_and_duration(store):
    r = say(store, C14)
    assert r.stage == "compare"
    rows = [x for x in r.reply.splitlines() if x.startswith("Рейс ")]
    assert rows
    for row in rows:
        assert re.search(r"вылет \d{2}\.\d{2} \d{2}:\d{2} \(UTC[+-]\d+\)", row), row
        assert len(re.findall(r"\d{2}:\d{2}", row)) == 2, row  # the start time is not repeated
        assert re.search(r"прилёт \d{2}\.\d{2} \d{2}:\d{2} \(UTC[+-]\d+\)", row), row
        hours = int(re.search(r"в пути (\d+) ч", row).group(1))
        assert hours >= 9, row  # never a flight under 2 h


def test_C14_TRAP_5_assemble_reply_every_time_has_a_zone(store):
    r = say(store, C14)
    r = say(store, "беру", r.session_id)
    assert r.stage == "assemble"
    plan = r.reply.split("План поездки:", 1)[1]
    for line in plan.splitlines():
        if TIME.search(line):
            assert re.search(r"\d{2}:\d{2} \(UTC[+-]\d+\)", line), line
    assert "(UTC+10)" in plan and "(UTC+2)" in plan


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("Казань из Москвы поездом, 3 ночи с 30 апреля, двое", "отправление", "прибытие"),
        (
            "На машине из Москвы в Сочи с собакой, двое взрослых, 10 ночей с 5 июля",
            "старт на машине",
            "приезд на машине",
        ),
    ],
)
def test_C14_TRAP_5_train_and_car_rows_use_their_own_verbs(store, text, start, end):
    """Review of #384: a train or a car has no «вылет» and no «прилёт»."""
    r = say(store, text)
    assert r.stage == "compare"
    rows = [x for x in r.reply.splitlines() if x.startswith("Рейс ")]
    assert rows
    for row in rows:
        assert "вылет" not in row and "прилёт" not in row, row
        assert f"· {start} " in row and f" → {end} " in row, row


def test_issue_line_has_one_period(store):
    r = say(store, C10)
    r = say(store, "беру", r.session_id)
    assert ".." not in r.reply
