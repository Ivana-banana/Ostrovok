"""#632: «купите билеты в музей» keeps the own tickets; the departure shifts in the last city's zone.
Offline (TRAP-1)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from api.dag import _moved
from api.extract import NOT_OWN_RE


@pytest.mark.parametrize(
    "text",
    ["купите билеты в музей янтаря", "нужны билеты в театр", "найди билеты на концерт"],
)
def test_non_transport_tickets_keep_the_own_tickets(text):
    assert not NOT_OWN_RE.search(text)


@pytest.mark.parametrize(
    "text",
    [
        "подбери перелёт",
        "найди рейс",
        "купите билеты на самолёт",
        "купите ж/д билеты",
        "нужны авиабилеты",
        "нужны билеты на поезд",
        "без своих билетов",
    ],
)
def test_transport_tickets_end_the_own_tickets(text):
    assert NOT_OWN_RE.search(text)


def test_trap_5_departure_shifts_in_the_zone_of_the_last_city():
    at = datetime(2026, 11, 18, 15, 0, tzinfo=ZoneInfo("Asia/Vladivostok"))
    first = _moved(at, at - at, ["Москва", "Владивосток"])
    last = _moved(at, at - at, ["Москва", "Владивосток"], last=True)
    assert first.tzinfo != last.tzinfo
    assert last.utcoffset().total_seconds() == 10 * 3600
