"""Timeline checker (T8, #14): local time, DST, docking issues and their fixes (S2, S6)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from api.contracts import ChatReply, ChatRequest, Plan, TripSpec
from api.fixtures import rank
from api.main import app, get_store
from api.model import chat
from api.offers import find_offers, offers_for_place, resolve_place, transfer_id
from api.store import SessionStore
from api.timeline import build_timeline

KAZAN = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё."


def trip(city: str, day_in: date, day_out: date) -> Plan:
    """Plan with the default selection (hotel and the cheapest legs) of real offers."""
    spec = TripSpec(
        origin="Москва",
        destinations=[city],
        date_from=day_in,
        date_to=day_out,
        nights=(day_out - day_in).days,
    )
    offers = find_offers(spec, city)
    selected, _ = rank(spec, offers)
    return Plan(spec=spec, offers=offers, selected=selected)


def parts(plan: Plan):
    by_id = {o.offer_id: o for o in plan.offers}
    hotel = next(by_id[i] for i in plan.selected if by_id[i].kind == "hotel")
    legs = [by_id[i] for i in plan.selected if by_id[i].kind == "transport"]
    out = next(x for x in legs if x.destination == hotel.city)
    back = next(x for x in legs if x.origin == hotel.city)
    return hotel, out, back


def replace(plan: Plan, *new, select: list[str] | None = None) -> Plan:
    """The plan with offers swapped by offer_id; `select` replaces the selection."""
    swap = {o.offer_id: o for o in new}
    offers = [swap.get(o.offer_id, o) for o in plan.offers]
    return plan.model_copy(update={"offers": offers, "selected": select or plan.selected})


def assert_fixes_exist(plan: Plan, issues):
    """TRAP-4: a fix is an offer of the plan, never an invented id."""
    ids = {o.offer_id for o in plan.offers}
    for i in issues:
        assert set(i.fixes) <= ids and len(set(i.fixes)) == len(i.fixes), i


def with_arrival(plan: Plan, arrive: datetime) -> Plan:
    """The outbound leg lands at `arrive`; the transfer leaves 40 minutes later."""
    hotel, out, _ = parts(plan)
    tr = next(o for o in plan.offers if o.offer_id == transfer_id(hotel.offer_id, out.offer_id))
    ride = tr.arrive_at - tr.depart_at
    leg = out.model_copy(update={"arrive_at": arrive, "depart_at": arrive - timedelta(hours=4)})
    start = (arrive.astimezone(ZoneInfo("UTC")) + timedelta(minutes=40)).astimezone(arrive.tzinfo)
    moved = tr.model_copy(update={"depart_at": start, "arrive_at": start + ride})
    return replace(plan, leg, moved)


def kinds_of(plan: Plan, ids: list[str]) -> list[str]:
    by_id = {o.offer_id: o for o in plan.offers}
    return [by_id[i].kind for i in ids]


def test_s6_morning_train_warns_and_offers_early_check_in_night_and_flight():
    """S6: the night train lands at 07:10, check-in is from 14:00."""
    plan = trip("Казань", date(2026, 12, 11), date(2026, 12, 13))
    _hotel, out, _ = parts(plan)
    assert out.mode == "train" and out.arrive_at.strftime("%H:%M") == "07:10"
    events, issues = build_timeline(plan)
    assert len(issues) == 1 and issues[0].severity == "warn"
    assert "07:10" in issues[0].text and "14:00" in issues[0].text
    fixes = issues[0].fixes
    assert fixes[0].endswith("-early") and fixes[1].endswith("-prev")
    assert kinds_of(plan, fixes) == ["extra", "extra", "transport", "transport"]
    assert_fixes_exist(plan, issues)
    flights = [o for o in plan.offers if o.offer_id in fixes[2:]]
    assert all(o.mode == "flight" and o.arrive_at.strftime("%H:%M") == "10:30" for o in flights[:1])
    assert [e.at for e in events] == sorted(e.at for e in events)
    assert all(e.at.utcoffset() == timedelta(hours=3) for e in events)  # Moscow and Kazan


def test_bought_early_check_in_or_night_before_removes_the_issue():
    plan = trip("Казань", date(2026, 12, 11), date(2026, 12, 13))
    _, issues = build_timeline(plan)
    for fix in issues[0].fixes[:2]:
        bought = plan.model_copy(update={"selected": [*plan.selected, fix]})
        events, left = build_timeline(bought)
        assert left == [], fix
    early = plan.model_copy(update={"selected": [*plan.selected, issues[0].fixes[0]]})
    events, _ = build_timeline(early)
    assert next(e for e in events if e.title.startswith("Заселение")).at.hour == 7  # on arrival


def test_alternative_flight_is_a_fix_that_really_fixes():
    plan = trip("Казань", date(2026, 12, 11), date(2026, 12, 13))
    _, issues = build_timeline(plan)
    flight = next(f for f in issues[0].fixes if f.startswith("t-"))
    _, _, back = parts(plan)
    hotel, _out, _ = parts(plan)
    swapped = plan.model_copy(update={"selected": [hotel.offer_id, flight, back.offer_id]})
    assert build_timeline(swapped)[1] == []


def test_s2_late_arrival_marks_night_transfer_and_late_check_in():
    """S2: Antalya, 10 November, landing at 23:40 local time."""
    plan = trip("Анталия", date(2026, 11, 10), date(2026, 11, 17))
    istanbul = ZoneInfo("Europe/Istanbul")
    plan = with_arrival(plan, datetime(2026, 11, 10, 23, 40, tzinfo=istanbul))
    events, issues = build_timeline(plan)
    assert len(issues) == 1 and issues[0].severity == "warn"
    text = issues[0].text
    assert "23:40" in text and "ночной трансфер в 00:20" in text and "поздний заезд" in text
    assert "Предупредите отель" in text
    assert_fixes_exist(plan, issues)
    arrival = next(e for e in events if e.title.startswith("Прилёт"))
    assert arrival.at.isoformat().startswith("2026-11-10T23:40:00+03:00")
    checkin = next(e for e in events if e.title.startswith("Заселение"))
    assert checkin.at.date() == date(2026, 11, 11) and checkin.at.hour < 5  # after midnight


def test_trap_5_dst_change_between_arrival_and_check_in():
    """TRAP-5: 28 March 2027, Berlin moves 02:00 -> 03:00. Landing at 00:10, check-in at 14:00:
    the wait is 11 h 15 min, not 12 h 15 min that two wall clocks of one zone would show."""
    berlin = {"name": "Берлин", "lat": 52.52, "lon": 13.405, "tz": "Europe/Berlin"}
    spec = TripSpec(
        origin="Москва",
        destinations=["Берлин"],
        date_from=date(2027, 3, 28),
        date_to=date(2027, 3, 30),
        nights=2,
    )
    offers = offers_for_place(spec, berlin, resolve_place("Москва"))
    selected, _ = rank(spec, offers)
    plan = Plan(spec=spec, offers=offers, selected=selected)
    tz = ZoneInfo("Europe/Berlin")
    plan = with_arrival(plan, datetime(2027, 3, 28, 0, 10, tzinfo=tz))
    hotel, _out, _ = parts(plan)
    assert hotel.tz == "Europe/Berlin"
    events, issues = build_timeline(plan)
    warn = next(i for i in issues if "ждать" in i.text)
    assert "в отель вы приедете в 01:45" in warn.text
    assert "ждать 11 ч 15 мин" in warn.text
    transfer = next(e for e in events if e.title.startswith("Трансфер: аэропорт"))
    checkin = next(e for e in events if e.title.startswith("Заселение"))
    assert transfer.at.isoformat().endswith("+01:00") and checkin.at.isoformat().endswith("+02:00")
    assert checkin.at == datetime(2027, 3, 28, 14, 0, tzinfo=tz)


def test_trap_5_check_in_inside_the_dst_gap_moves_on_not_back():
    """A check-in time inside the skipped hour is a real instant after the gap."""
    berlin = {"name": "Берлин", "lat": 52.52, "lon": 13.405, "tz": "Europe/Berlin"}
    spec = TripSpec(
        origin="Москва", date_from=date(2027, 3, 28), date_to=date(2027, 3, 30), nights=2
    )
    offers = offers_for_place(spec, berlin, resolve_place("Москва"))
    hotel = next(o for o in offers if o.kind == "hotel")
    hotel = hotel.model_copy(update={"check_in": datetime(2027, 3, 28, 2, 30).time()})
    plan = Plan(spec=spec, offers=[hotel], selected=[hotel.offer_id])
    events, _ = build_timeline(plan)
    at = next(e.at for e in events if e.title.startswith("Заселение"))
    assert at.isoformat() == "2027-03-28T03:30:00+02:00"


def test_return_before_the_check_out_date_is_an_error_with_other_flights():
    plan = trip("Анталия", date(2026, 11, 10), date(2026, 11, 17))
    _hotel, _out, back = parts(plan)
    early = back.model_copy(
        update={
            "depart_at": back.depart_at - timedelta(days=2),
            "arrive_at": back.arrive_at - timedelta(days=2),
        }
    )
    plan = replace(plan, early)
    _, issues = build_timeline(plan)
    assert [i.severity for i in issues] == ["error"]
    assert "раньше выезда из отеля 17.11" in issues[0].text
    assert issues[0].fixes and all(f.startswith("t-") for f in issues[0].fixes)
    assert early.offer_id not in issues[0].fixes
    assert_fixes_exist(plan, issues)


def test_transfer_too_late_for_the_flight_is_an_error():
    plan = trip("Анталия", date(2026, 11, 10), date(2026, 11, 17))
    hotel, _, back = parts(plan)
    tr = next(o for o in plan.offers if o.offer_id == transfer_id(hotel.offer_id, back.offer_id))
    late = tr.model_copy(
        update={
            "depart_at": back.depart_at - timedelta(minutes=70),
            "arrive_at": back.depart_at - timedelta(minutes=30),
        }
    )
    plan = replace(plan, late)
    _, issues = build_timeline(plan)
    assert [i.severity for i in issues] == ["error"]
    assert "останется 30 мин" in issues[0].text and "не меньше 2 ч" in issues[0].text
    assert_fixes_exist(plan, issues)


def test_short_connection_after_landing_is_an_error_with_other_legs():
    plan = trip("Анталия", date(2026, 11, 10), date(2026, 11, 17))
    hotel, out, _ = parts(plan)
    tr = next(o for o in plan.offers if o.offer_id == transfer_id(hotel.offer_id, out.offer_id))
    ride = tr.arrive_at - tr.depart_at
    start = out.arrive_at + timedelta(minutes=10)
    quick = tr.model_copy(update={"depart_at": start, "arrive_at": start + ride})
    plan = replace(plan, quick)
    _, issues = build_timeline(plan)
    assert [i.severity for i in issues] == ["error"]
    assert "Короткая стыковка" in issues[0].text and "через 10 мин" in issues[0].text
    assert issues[0].fixes and all(f.startswith("t-") for f in issues[0].fixes)
    assert_fixes_exist(plan, issues)


def test_arrival_after_the_check_in_date_is_an_error():
    plan = trip("Анталия", date(2026, 11, 10), date(2026, 11, 17))
    istanbul = ZoneInfo("Europe/Istanbul")
    plan = with_arrival(plan, datetime(2026, 11, 11, 9, 0, tzinfo=istanbul))
    _, issues = build_timeline(plan)
    assert issues[0].severity == "error" and "11.11" in issues[0].text
    assert_fixes_exist(plan, issues)


def test_long_wait_before_the_evening_flight_offers_late_check_out():
    plan = trip("Анталия", date(2026, 11, 10), date(2026, 11, 17))
    hotel, out, _back = parts(plan)
    evening = next(
        o
        for o in plan.offers
        if o.kind == "transport" and o.origin == hotel.city and o.depart_at.hour == 21
    )
    plan = plan.model_copy(update={"selected": [hotel.offer_id, out.offer_id, evening.offer_id]})
    events, issues = build_timeline(plan)
    assert [i.severity for i in issues] == ["warn"]
    assert "Выезд из отеля в 12:00" in issues[0].text and "ждать" in issues[0].text
    assert issues[0].fixes[0].endswith("-late")
    assert_fixes_exist(plan, issues)
    late = issues[0].fixes[0]
    bought = plan.model_copy(update={"selected": [*plan.selected, late]})
    events, left = build_timeline(bought)
    assert left == []
    assert next(e for e in events if e.title.startswith("Выезд из отеля")).at.hour == 18


@pytest.mark.parametrize(
    ("city", "day_in", "day_out"),
    [
        ("Казань", date(2026, 12, 11), date(2026, 12, 13)),
        ("Анталия", date(2026, 11, 8), date(2026, 11, 15)),
        ("Сочи", date(2026, 11, 20), date(2026, 11, 23)),
        ("Екатеринбург", date(2027, 3, 27), date(2027, 3, 29)),
        ("Дубай", date(2026, 11, 10), date(2026, 11, 17)),
    ],
)
def test_default_selection_has_no_errors_and_local_events(city, day_in, day_out):
    plan = trip(city, day_in, day_out)
    hotel, _out, _back = parts(plan)
    events, issues = build_timeline(plan)
    assert all(i.severity == "warn" for i in issues)
    assert_fixes_exist(plan, issues)
    tz = ZoneInfo(hotel.tz)
    local = {e.title.split(":")[0]: e.at for e in events}
    assert local["Заселение"].utcoffset() == local["Заселение"].astimezone(tz).utcoffset()
    assert [e.at for e in events] == sorted(e.at for e in events)
    assert len({e.title for e in events}) == len(events)


def test_no_hotel_and_empty_plan_do_not_fail():
    spec = TripSpec(origin="Москва", date_from=date(2026, 12, 11), date_to=date(2026, 12, 13))
    assert build_timeline(Plan(spec=spec)) == ([], [])
    plan = trip("Казань", date(2026, 12, 11), date(2026, 12, 13))
    _hotel, out, back = parts(plan)
    legs = plan.model_copy(update={"selected": [out.offer_id, back.offer_id]})
    events, issues = build_timeline(legs)
    assert [e.offer_id for e in events] == [out.offer_id] * 2 + [back.offer_id] * 2
    assert issues == []


@pytest.fixture
def client(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app), store
    app.dependency_overrides.clear()


def at_checkout(store) -> ChatReply:
    r = chat(ChatRequest(message=KAZAN), store)
    for text in ("беру", "да"):
        r = chat(ChatRequest(session_id=r.session_id, message=text), store)
    assert r.stage == "checkout"
    return r


def add(client, r, offer_id):
    res = client.post(
        "/api/cart", json={"session_id": r.session_id, "action": "add", "offer_id": offer_id}
    )
    assert res.status_code == 200, res.text
    return ChatReply.model_validate(client.get(f"/api/session/{r.session_id}").json())


def test_cart_early_check_in_fixes_the_issue(client):
    http, store = client
    r = at_checkout(store)
    assert [i.severity for i in r.plan.issues] == ["warn"]
    early = r.plan.issues[0].fixes[0]
    now = add(http, r, early)
    assert now.plan.issues == [] and early in now.plan.selected
    assert early in {o.offer_id for o in now.cart.items}


def test_cart_replaced_flight_and_hotel_are_what_the_checker_sees(client):
    """The review of #11: a swap in the cart must reach plan.selected and the timeline."""
    http, store = client
    r = at_checkout(store)
    flight = next(f for f in r.plan.issues[0].fixes if f.startswith("t-"))
    now = add(http, r, flight)
    assert flight in now.plan.selected and now.plan.issues == []
    arrival = next(e for e in now.plan.timeline if e.title.startswith("Прилёт"))
    assert arrival.at.strftime("%H:%M") == "10:30"

    old_hotel = next(i for i in now.plan.selected if i.startswith("h-"))
    other = next(o for o in now.plan.offers if o.kind == "hotel" and o.offer_id != old_hotel)
    swapped = add(http, now, other.offer_id)
    assert other.offer_id in swapped.plan.selected and old_hotel not in swapped.plan.selected
    assert any(other.title in e.title for e in swapped.plan.timeline)
    assert not any(old_hotel_title(now, old_hotel) in e.title for e in swapped.plan.timeline)


def old_hotel_title(reply: ChatReply, offer_id: str) -> str:
    return next(o.title for o in reply.plan.offers if o.offer_id == offer_id)


def test_cart_remove_hotel_keeps_the_timeline_and_the_return_restores_it(client):
    http, store = client
    r = at_checkout(store)
    hotel = next(i for i in r.plan.selected if i.startswith("h-"))
    http.post("/api/cart", json={"session_id": r.session_id, "action": "remove", "offer_id": hotel})
    mid = ChatReply.model_validate(http.get(f"/api/session/{r.session_id}").json())
    assert hotel in mid.plan.selected and mid.plan.timeline == r.plan.timeline
    back = add(http, mid, hotel)
    assert back.plan.selected.count(hotel) == 1
