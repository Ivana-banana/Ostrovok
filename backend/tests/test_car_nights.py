"""#321: the tails of #276 (C4, eval/cases_critic.json) — a car with nights on the road.

1. «Допущения» name the earlier start and the later return home.
2. Inspiration counts a car by the hours at the wheel, not by the hours with the nights.
3. A timeline error with a fix «рейс N»: the pick clears the error and «беру» goes on.
4-5. The daily limit MAX_DRIVE is 12 h, a team estimate (D-026; professional drivers in the
   EU: 9-10 h, Regulation (EC) 561/2006). Москва → Санкт-Петербург (11.3 h) is one day;
   Москва → Казань (12 h 50 min by the offers model) takes 2 days: a known compromise.
Offline, without an LLM key (TRAP-1); dates, times and ids only from offers (TRAP-4).
"""

from datetime import date, timedelta

import pytest

from api import offers
from api.contracts import ChatRequest, TransportOffer, TripSpec
from api.model import chat
from api.offers import MAX_DRIVE, drive_time, resolve_place, road_nights, transport_offers
from api.store import SessionStore

C4 = "На машине из Москвы в Сочи с собакой, двое взрослых, 10 ночей с 5 июля"
SEA = "На машине из Москвы на море в июле, двое взрослых, 7 ночей, не больше 20 часов в пути"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GIGACHAT_AUTH_KEY", raising=False)
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def car(origin: str, place: str) -> list[TransportOffer]:
    spec = TripSpec(
        origin=origin,
        destinations=[place],
        date_from=date(2027, 7, 5),
        date_to=date(2027, 7, 8),
        adults=2,
        modes=["car"],
    )
    return transport_offers(spec, resolve_place(origin), resolve_place(place))


def test_C4_TRAP_5_assumptions_name_the_earlier_start_and_the_later_return(store):
    r = say(store, C4)
    assert r.stage == "compare"
    line = next(x for x in r.reply.splitlines() if x.startswith("Допущения:"))
    assert "выезд 03.07, возврат 17.07" in line
    assert "ночёвок в пути: 2 в каждую сторону (оценка)" in line


def test_TRAP_5_no_road_note_without_nights_on_the_road(store):
    r = say(store, "На машине из Москвы в Нижний Новгород, двое взрослых, 3 ночи с 5 июля")
    assert r.stage == "compare"
    assert "ночёвок в пути" not in r.reply


def test_TRAP_5_drive_time_leaves_out_the_nights_on_the_road():
    there, back = car("Москва", "Сочи")
    for leg in (there, back):
        nights = road_nights(leg)
        assert nights == 2
        elapsed = leg.arrive_at - leg.depart_at
        assert elapsed > timedelta(days=2)
        assert drive_time(leg) < elapsed - timedelta(hours=24)
        assert drive_time(leg) / (nights + 1) <= MAX_DRIVE


def test_TRAP_5_inspiration_filters_a_car_by_hours_at_the_wheel(store):
    r = say(store, SEA)
    assert r.stage == "inspire" and r.plan.spec.max_travel_hours == 20
    yeysk = next(d for d in r.plan.destinations if d.name == "Ейск")
    assert not yeysk.violates  # 18 h at the wheel pass «не больше 20 часов»
    assert yeysk.travel_hours > 24  # door to door with the night, as before #321
    road = [t for t in yeysk.tradeoffs if t.startswith("за рулём ")]
    assert road and road[0].endswith(" ч, 1 ночёвка в пути")
    card = next(x for x in r.reply.splitlines() if "Ейск" in x)
    assert road[0] in card and f"в пути {yeysk.travel_hours:g} ч" not in card
    assert card.count("за рулём") == 1  # not again in the minuses


def test_TRAP_5_moscow_kazan_takes_two_days_a_known_compromise():
    """The offers model drives 12 h 50 min (the road is 1.3 x the straight line): just over
    MAX_DRIVE, so one night on the road. No tolerance on top of the estimate (D-026)."""
    for leg in car("Москва", "Казань"):
        assert drive_time(leg) > MAX_DRIVE
        assert road_nights(leg) == 1
        assert drive_time(leg) / 2 <= MAX_DRIVE


@pytest.mark.parametrize("place", ["Санкт-Петербург", "Нижний Новгород"])
def test_TRAP_5_a_drive_within_the_daily_limit_is_one_day(place):
    for leg in car("Москва", place):
        assert drive_time(leg) <= MAX_DRIVE and road_nights(leg) == 0
        assert leg.depart_at.date() == leg.arrive_at.date()
        assert "ночёв" not in leg.title


def test_TRAP_5_max_drive_is_12_hours():
    assert MAX_DRIVE == timedelta(hours=12)


class _NonstopFirst:
    """The synthetic port plus a car there without a night on the road: it starts on the
    check-in day and comes the next morning, so the paid night is lost (an error)."""

    def __init__(self, real):
        self.real = real

    def legs(self, spec, origin, place):
        legs = self.real.legs(spec, origin, place)
        good = next(x for x in legs if x.mode == "car" and x.origin == origin["name"])
        start = good.depart_at.replace(day=spec.date_from.day, month=spec.date_from.month)
        bad = good.model_copy(
            update={
                "offer_id": "t-nonstop-car",
                "title": f"На своей машине {origin['name']} → {place['name']}, без ночёвки",
                "depart_at": start,
                "arrive_at": start + timedelta(hours=24, minutes=20),
            }
        )
        return [bad, *legs]


def test_C4_TRAP_5_gate_fix_by_row_clears_the_error_and_lets_take_go(store, monkeypatch):
    monkeypatch.setattr(offers, "TRANSPORT", _NonstopFirst(offers.TRANSPORT))
    r = say(store, C4)
    assert r.stage == "compare"
    r = say(store, "рейс 1", r.session_id)  # the user picks the leg without a night on the road
    assert "t-nonstop-car" in r.plan.selected
    r = say(store, "беру", r.session_id)
    assert r.stage == "compare" and not r.cart.items
    error = next(i for i in r.plan.issues if i.severity == "error")
    rows = [o.offer_id for o in r.plan.offers if o.kind == "transport"]
    fix = next(x for x in error.fixes if x in rows)  # a leg of the plan (TRAP-4)
    n = rows.index(fix) + 1
    assert f"рейс {n} — " in r.reply
    r = say(store, f"рейс {n}", r.session_id)
    assert fix in r.plan.selected and "t-nonstop-car" not in r.plan.selected
    r = say(store, "беру", r.session_id)
    assert r.stage == "assemble"
    assert not [i for i in r.plan.issues if i.severity == "error"]
