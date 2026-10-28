"""C4 (#276, eval/cases_critic.json): a long drive, the timeline gate and the car return.

«На машине из Москвы в Сочи с собакой, двое взрослых, 10 ночей с 5 июля». The road is about
24 h at the wheel. Before the fix the car left at 08:00 and drove without a stop, the
timeline found «ночь заезда оплачена, но не используется» without a fix, and «беру» still
moved the plan to assemble. The return car left at 10:00, before the 12:00 check-out.
All runs are offline, without an LLM key (TRAP-1); times come only from offers (TRAP-4).
"""

from datetime import date, datetime, timedelta

import pytest
from talk import say

from api import model
from api.contracts import TimelineIssue, TransportOffer, TripSpec
from api.offers import MAX_DRIVE, resolve_place, transport_offers
from api.store import SessionStore
from api.timeline import build_timeline

C4 = "На машине из Москвы в Сочи с собакой, двое взрослых, 10 ночей с 5 июля"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def car_legs(day_from: date = date(2027, 7, 5), nights: int = 10) -> list[TransportOffer]:
    spec = TripSpec(
        origin="Москва",
        destinations=["Сочи"],
        date_from=day_from,
        date_to=day_from + timedelta(days=nights),
        adults=2,
        pets=True,
        modes=["car"],
    )
    legs = transport_offers(spec, resolve_place("Москва"), resolve_place("Сочи"))
    assert legs and all(x.mode == "car" for x in legs)
    return legs


def selected(r, kind: str):
    by_id = {o.offer_id: o for o in r.plan.offers}
    return [by_id[i] for i in r.plan.selected if by_id[i].kind == kind]


def test_C4_TRAP_5_long_drive_stops_overnight_and_arrives_on_check_in_day():
    there, back = car_legs()
    for leg in (there, back):
        hours = (leg.arrive_at - leg.depart_at).total_seconds() / 3600
        stops = (leg.arrive_at.date() - leg.depart_at.date()).days
        assert hours > MAX_DRIVE.total_seconds() / 3600  # the road is longer than one day
        assert stops >= 1 and f"ночёвок в пути: {stops}" in leg.title
        # Each driving day starts at the same clock time and lasts no more than MAX_DRIVE.
        last_start = leg.depart_at + timedelta(days=stops)
        assert leg.arrive_at.astimezone(leg.depart_at.tzinfo) - last_start <= MAX_DRIVE
    # The car comes to Sochi on the check-in day: the paid night is used.
    assert there.arrive_at.date() == date(2027, 7, 5)
    assert 8 <= there.arrive_at.hour < 23


def test_C4_TRAP_5_return_car_starts_after_check_out():
    _, back = car_legs()
    assert back.depart_at.date() == date(2027, 7, 15)
    assert back.depart_at.time() >= datetime(2027, 7, 15, 12, 0).time()  # HotelOffer.check_out


def test_C4_TRAP_5_short_drive_stays_one_day():
    spec = TripSpec(
        origin="Москва",
        destinations=["Суздаль"],
        date_from=date(2027, 2, 5),
        date_to=date(2027, 2, 8),
        adults=2,
        modes=["car"],
    )
    legs = transport_offers(spec, resolve_place("Москва"), resolve_place("Суздаль"))
    assert legs
    for leg in legs:
        assert leg.depart_at.date() == leg.arrive_at.date()
        assert "ночёв" not in leg.title


def test_C4_TRAP_5_timeline_warns_on_a_continuous_drive_over_12_hours(store):
    r = say(store, C4)
    car = selected(r, "transport")[0]
    plan = r.plan.model_copy(deep=True)
    # A provider without overnight stops: 24 h at the wheel, still on the check-in day.
    start = datetime.combine(date(2027, 7, 4), datetime.min.time(), car.depart_at.tzinfo)
    nonstop = car.model_copy(
        update={
            "offer_id": "t-nonstop",
            "title": "На своей машине Москва → Сочи, 04.07 00:00",
            "depart_at": start,
            "arrive_at": start + timedelta(hours=24, minutes=20),
        }
    )
    plan.offers.append(nonstop)
    plan.selected = [i if i != car.offer_id else "t-nonstop" for i in plan.selected]
    _, issues = build_timeline(plan)
    long = [i for i in issues if "без ночёвки" in i.text]
    assert long and long[0].severity == "warn"
    assert car.offer_id in long[0].fixes  # the leg with the overnight stops (TRAP-4: an id)


def test_C4_TRAP_5_plan_with_a_timeline_error_does_not_reach_assemble(store, monkeypatch):
    real = model.build_timeline

    def broken(plan):
        events, issues = real(plan)
        bad = TimelineIssue(severity="error", text="Ночь заезда оплачена, но не используется")
        return events, [bad, *issues]

    r = say(store, C4)
    monkeypatch.setattr(model, "build_timeline", broken)
    r = say(store, "беру", r.session_id)
    assert r.stage == "compare"
    assert "Ночь заезда оплачена, но не используется" in r.reply
    assert not r.cart.items


def test_C4_TRAP_5_full_chat_offline_reaches_checkout_without_errors(store, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GIGACHAT_AUTH_KEY", raising=False)
    r = say(store, C4)
    assert r.stage == "compare"
    r = say(store, "беру", r.session_id)
    assert r.stage == "assemble"
    assert not [i for i in r.plan.issues if i.severity == "error"]
    hotel = selected(r, "hotel")[0]
    there, back = sorted(selected(r, "transport"), key=lambda x: x.depart_at)
    assert there.arrive_at.date() == hotel.date_in
    assert back.depart_at.astimezone(there.arrive_at.tzinfo).time() >= hotel.check_out
    titles = [e.title for e in r.plan.timeline]
    out = next(i for i, x in enumerate(titles) if x.startswith("Выезд из отеля"))
    assert titles[out + 1].startswith("Старт на машине: Сочи")  # check-out first, then the road
    r = say(store, "да", r.session_id)
    assert r.stage == "checkout" and r.cart.items
