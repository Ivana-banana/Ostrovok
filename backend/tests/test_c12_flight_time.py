"""C12 (#366): «возьми рейс в 06:15» — a pick of the flight by its departure time.

No such flight: the reply names 06:15 and lists the times of the plan offers (TRAP-4).
A time that exists picks that leg, as «рейс N» does.
"""

import pytest
from talk import say

from api.contracts import TransportOffer
from api.model import leg_commands
from api.store import SessionStore

C12 = "Сочи на 5 ночей с 10 ноября вдвоём, самолётом из Москвы"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def _legs(r, chosen=True):
    return [
        o
        for o in r.plan.offers
        if isinstance(o, TransportOffer) and (o.offer_id in r.plan.selected) == chosen
    ]


def test_c12_no_flight_at_that_time_names_the_time_and_the_offers(store):
    r = say(store, C12)
    again = say(store, "возьми рейс в 06:15", r.session_id)
    assert "Рейса в 06:15 нет" in again.reply
    assert "План не меняю" not in again.reply
    there = [o for o in r.plan.offers if isinstance(o, TransportOffer) and o.destination == "Сочи"]
    for o in there:
        if o.mode != "car":
            assert f"{o.depart_at:%H:%M}" in again.reply  # times of the offers, not invented
    assert again.plan.selected == r.plan.selected


def test_c12_an_existing_time_picks_that_flight(store):
    r = say(store, C12)
    other = next(
        o for o in _legs(r, chosen=False) if o.mode == "flight" and o.destination == "Сочи"
    )
    again = say(store, f"возьми рейс в {other.depart_at:%H:%M}", r.session_id)
    assert other.offer_id in again.plan.selected
    assert "Рейса в" not in again.reply


@pytest.mark.parametrize(
    "text, want",
    [
        ("возьми рейс в 06:15", [("time", ("06:15", None), None)]),
        ("давай обратно вылет в 9:40", [("time", ("09:40", None), "back")]),
        ("возьми поезд в 07:50", [("time", ("07:50", "train"), None)]),
        ("давай перелёт с вылетом в 8:00", [("time", ("08:00", "flight"), None)]),
        ("рейс в 06:15 есть?", []),  # a question without a verb picks nothing
        ("возьми рейс 2", [("row", 1, None)]),
        # A time not right after the noun is no departure: the mode pick stays
        ("давай поезд чтобы прибыть к 18:00", [("mode", "train", None)]),
        ("давай перелёт заселение в 14:00", [("mode", "flight", None)]),
        ("возьми поезд ехать 7.30 часов", [("mode", "train", None)]),
    ],
)
def test_c12_time_command(text, want):
    assert leg_commands(text) == want


def test_c12_a_train_time_at_a_flight_plan_re_plans_by_train(store):
    """«возьми поезд в 07:50» at a plan by plane is a new mode, as «возьми поезд» is: no list
    of flight times."""
    r = say(store, C12)
    again = say(store, "возьми поезд в 07:50", r.session_id)
    assert "Рейса в" not in again.reply and "Перелёта в" not in again.reply
