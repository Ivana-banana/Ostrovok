"""#200, TRAP-5: a transfer the user refused stays out of the timeline, the cart and the total.

«без трансфера» takes the transfers out of `plan.selected` (#193). The checker must not bring
them back by the pair «hotel + leg»: no transfer event, no check-in time from the transfer
arrival. The guest gets to the hotel on their own; the check-in is not earlier than the
arrival plus the road by data: the time of the pair's transfer offer, which offers.py derives
from the distance to the hub, never under OWN_WAY. The text names the road and the distance.
The refusal lives in `plan.selected` itself:
a selected hotel and leg whose transfer is offered but not selected. So it survives
/api/cart (an upsell, a new flight or hotel) and a page reload.
"""

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from api.contracts import ChatReply, ChatRequest, Plan, TripSpec
from api.fixtures import rank
from api.main import app, get_store
from api.model import chat
from api.offers import find_offers, transfer_id
from api.store import SessionStore
from api.timeline import OWN_WAY, build_timeline

SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых, отель до 40 тысяч, нужен завтрак"
KAZAN = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё."
# The airport (PES) is 182 km away. Ейск (KRR) was here before #363: KRR is closed (C8).
SORTAVALA = "Сортавала из Москвы, 3 ночи с 20 января"


@pytest.fixture
def client(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app), store
    app.dependency_overrides.clear()


def say(store, text, sid=None) -> ChatReply:
    return chat(ChatRequest(session_id=sid, message=text), store)


def at(store, stage: str, request: str = SOCHI) -> ChatReply:
    r = say(store, request)
    for text in ("беру", "да")[: {"assemble": 1, "checkout": 2}[stage]]:
        r = say(store, text, r.session_id)
    assert r.stage == stage
    return r


def session(http, r: ChatReply) -> ChatReply:
    """The page reload: the state the server keeps."""
    return ChatReply.model_validate(http.get(f"/api/session/{r.session_id}").json())


def cart(http, r: ChatReply, action: str, offer_id: str) -> ChatReply:
    res = http.post(
        "/api/cart", json={"session_id": r.session_id, "action": action, "offer_id": offer_id}
    )
    assert res.status_code == 200, res.text
    return session(http, r)


def by_id(r: ChatReply) -> dict:
    return {o.offer_id: o for o in r.plan.offers}


def transfer_ids(r: ChatReply) -> set[str]:
    return {o.offer_id for o in r.plan.offers if o.kind == "transfer"}


def selected(r: ChatReply, kind: str) -> list:
    offers = by_id(r)
    return [offers[i] for i in r.plan.selected if i in offers and offers[i].kind == kind]


def assert_no_transfer(r: ChatReply) -> None:
    """TRAP-5: nothing of a transfer in the selection, the timeline or the cart."""
    ids = transfer_ids(r)
    assert not ids & set(r.plan.selected)
    assert not any(e.offer_id in ids for e in r.plan.timeline)
    assert not any(e.title.startswith("Трансфер") for e in r.plan.timeline)
    assert all(o.kind != "transfer" for o in r.cart.items)
    assert r.cart.total_rub == sum(o.price_rub for o in r.cart.items)


def assert_own_way_check_in(r: ChatReply) -> None:
    """TRAP-5: the check-in follows the arrival and the own way, never a transfer arrival."""
    hotel = selected(r, "hotel")[0]
    out = next(x for x in selected(r, "transport") if x.destination == hotel.city)
    events = r.plan.timeline
    check_in = next(e for e in events if e.title.startswith("Заселение"))
    pair = by_id(r)[transfer_id(hotel.offer_id, out.offer_id)]
    road = max(OWN_WAY[out.mode], pair.arrive_at - out.arrive_at)
    assert road >= timedelta(minutes=30)  # the road is never zero
    at_hotel = next((e.at for e in events if e.title.startswith("Приезд в отель")), check_in.at)
    assert at_hotel == out.arrive_at + road and check_in.at >= at_hotel
    own = next(e for e in events if "до отеля добираетесь сами" in e.title.lower())
    assert own.at == out.arrive_at and own.offer_id == out.offer_id
    km = re.search(r"≈\S+ км", pair.title)
    assert km and km.group(0) in own.title
    hours, minutes = divmod(int(road.total_seconds() // 60), 60)
    if hours and minutes:
        span = f"{hours} ч {minutes:02d} мин"
    else:
        span = f"{hours} ч" if hours else f"{minutes} мин"
    assert own.title.endswith(f"на дорогу закладываем {span}")


def test_trap_5_without_transfer_the_timeline_says_own_way_and_no_transfer_time(client):
    """Sochi: the flight lands 17:40. «Заселение 19:25» came from the removed transfer with no
    word about it; now the timeline says the guest goes alone and names the road and the km."""
    _, store = client
    r = at(store, "assemble")
    assert selected(r, "transfer")
    r2 = say(store, "давайте без трансфера", r.session_id)
    assert_no_transfer(r2)
    assert_own_way_check_in(r2)
    back = next(x for x in selected(r2, "transport") if x.destination == r2.plan.spec.origin)
    hubs = ("Выезд в аэропорт", "Выезд на вокзал")
    leave = next(e for e in r2.plan.timeline if e.title.startswith(hubs))
    assert "добираетесь сами" in leave.title and leave.offer_id == back.offer_id
    assert leave.at < back.depart_at


def test_trap_5_refusal_survives_an_upsell_click_and_a_reload(client):
    http, store = client
    r = at(store, "checkout")
    r2 = say(store, "давайте без трансфера", r.session_id)
    assert_no_transfer(r2)
    hotel = selected(r2, "hotel")[0]
    early = f"{hotel.offer_id}-early"
    assert early in by_id(r2)
    now = cart(http, r2, "add", early)
    assert early in now.plan.selected and early in {o.offer_id for o in now.cart.items}
    assert_no_transfer(now)
    assert now.cart.total_rub == r2.cart.total_rub + by_id(now)[early].price_rub
    again = session(http, now)
    assert_no_transfer(again)
    assert again.plan.timeline == now.plan.timeline


def test_trap_5_refusal_survives_a_new_flight(client):
    http, store = client
    r = at(store, "checkout")
    r2 = say(store, "давайте без трансфера", r.session_id)
    hotel = selected(r2, "hotel")[0]
    leg = next(x for x in selected(r2, "transport") if x.destination == hotel.city)
    alt = next(
        o
        for o in r2.plan.offers
        if o.kind == "transport"
        and o.mode != "car"
        and o.offer_id != leg.offer_id
        and (o.origin, o.destination) == (leg.origin, leg.destination)
    )
    now = cart(http, r2, "add", alt.offer_id)
    assert alt.offer_id in now.plan.selected and leg.offer_id not in now.plan.selected
    assert_no_transfer(now)
    assert now.cart.total_rub == r2.cart.total_rub - leg.price_rub + alt.price_rub
    assert_own_way_check_in(now)


def test_trap_5_refusal_survives_a_new_hotel(client):
    http, store = client
    r = at(store, "checkout")
    r2 = say(store, "давайте без трансфера", r.session_id)
    old = selected(r2, "hotel")[0]
    other = next(o for o in r2.plan.offers if o.kind == "hotel" and o.offer_id != old.offer_id)
    now = cart(http, r2, "add", other.offer_id)
    assert other.offer_id in now.plan.selected and old.offer_id not in now.plan.selected
    assert_no_transfer(now)
    assert now.cart.total_rub == r2.cart.total_rub - old.price_rub + other.price_rub
    assert_own_way_check_in(session(http, now))


def test_trap_5_one_removed_transfer_keeps_the_other_with_a_new_hotel(client):
    """The refusal is per direction: the return transfer still comes with the new hotel."""
    http, store = client
    r = at(store, "checkout")
    hotel = selected(r, "hotel")[0]
    out = next(x for x in selected(r, "transport") if x.destination == hotel.city)
    back = next(x for x in selected(r, "transport") if x.origin == hotel.city)
    mid = cart(http, r, "remove", transfer_id(hotel.offer_id, out.offer_id))
    other = next(o for o in mid.plan.offers if o.kind == "hotel" and o.offer_id != hotel.offer_id)
    now = cart(http, mid, "add", other.offer_id)
    in_cart = {o.offer_id for o in now.cart.items}
    assert transfer_id(other.offer_id, out.offer_id) not in in_cart | set(now.plan.selected)
    assert transfer_id(other.offer_id, back.offer_id) in in_cart & set(now.plan.selected)
    assert now.cart.total_rub == sum(o.price_rub for o in now.cart.items)
    assert_own_way_check_in(now)


def test_trap_5_refusal_survives_a_train_in_the_chat(client):
    """«без трансфера», then «давай поездом» at assemble: the new legs bring no transfer."""
    _, store = client
    r = at(store, "assemble")
    r2 = say(store, "давайте без трансфера", r.session_id)
    r3 = say(store, "давай поездом", r2.session_id)
    assert "Транспорт заменён" in r3.reply
    assert all(x.mode == "train" for x in selected(r3, "transport"))
    assert_no_transfer(r3)


def test_trap_5_refusal_survives_a_better_hotel_in_the_chat(client):
    """«без трансфера», then «отель получше» (#184): the new hotel brings no transfer."""
    _, store = client
    r = at(store, "assemble", KAZAN)
    r2 = say(store, "давайте без трансфера", r.session_id)
    r3 = say(store, "отель получше", r2.session_id)
    assert "Отель получше" in r3.reply
    assert selected(r3, "hotel")[0].offer_id != selected(r2, "hotel")[0].offer_id
    assert_no_transfer(r3)
    assert_own_way_check_in(r3)


def test_trap_5_refusal_survives_a_direct_flight_in_the_chat(client):
    """«без трансфера», then «без пересадок» (#184): the direct leg brings no transfer."""
    _, store = client
    r = at(store, "assemble", KAZAN)
    r = say(store, "возьми самолёт", r.session_id)
    r = say(store, "давайте без трансфера", r.session_id)
    out = next(x for x in selected(r, "transport") if x.destination == "Казань")
    stops = out.model_copy(update={"changes": 1, "title": out.title + ", пересадок: 1"})
    offers = [stops if o.offer_id == out.offer_id else o for o in r.plan.offers]
    store.put(r.model_copy(update={"plan": r.plan.model_copy(update={"offers": offers})}))
    r2 = say(store, "без пересадок", r.session_id)
    assert out.offer_id not in r2.plan.selected
    assert all(x.changes == 0 for x in selected(r2, "transport"))
    assert_no_transfer(r2)
    assert_own_way_check_in(r2)


def test_trap_5_far_airport_keeps_the_road_by_data_and_the_night_check_in(client):
    """Сортавала: the airport of catalog.json (PES) is 182 km away. Without the transfer the road
    is still ≈6 h, not a flat 1 h 30 min: the check-in comes near midnight, and the late
    check-in warning stays."""
    _, store = client
    r = at(store, "assemble", SORTAVALA)
    with_transfer = r.plan.timeline
    r2 = say(store, "давайте без трансфера", r.session_id)
    assert_no_transfer(r2)
    assert_own_way_check_in(r2)
    hotel = selected(r2, "hotel")[0]
    out = next(x for x in selected(r2, "transport") if x.destination == hotel.city)
    own = next(e for e in r2.plan.timeline if "добираетесь сами" in e.title)
    assert "≈185.5 км" in own.title  # the distance of catalog.json, not a flat road
    check_in = next(e for e in r2.plan.timeline if e.title.startswith("Заселение"))
    assert check_in.at - out.arrive_at > OWN_WAY[out.mode] + timedelta(hours=1)
    was = next(e for e in with_transfer if e.title.startswith("Заселение"))
    assert check_in.at == was.at  # the same road, from the same distance
    assert any("поздний заезд" in i.text for i in r2.plan.issues)
    back = next(x for x in selected(r2, "transport") if x.origin == hotel.city)
    leave = next(e for e in r2.plan.timeline if e.title.startswith("Выезд в аэропорт"))
    assert back.depart_at - leave.at > timedelta(hours=2) + OWN_WAY[back.mode]


def plan_without_transfers(city: str, day_in: date, day_out: date) -> Plan:
    spec = TripSpec(
        origin="Москва",
        destinations=[city],
        date_from=day_in,
        date_to=day_out,
        nights=(day_out - day_in).days,
    )
    offers = find_offers(spec, city)
    picked, _ = rank(spec, offers)
    kinds = {o.offer_id: o.kind for o in offers}
    return Plan(spec=spec, offers=offers, selected=[i for i in picked if kinds[i] != "transfer"])


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
def test_trap_5_checker_reads_only_selected_transfers(city, day_in, day_out):
    """The checker never derives a transfer that is not selected; its fixes are plan offers,
    and a leg it offers as a fix brings no transfer either (the user refused it)."""
    plan = plan_without_transfers(city, day_in, day_out)
    offers = {o.offer_id: o for o in plan.offers}
    events, issues = build_timeline(plan)
    assert not any(offers.get(e.offer_id) and offers[e.offer_id].kind == "transfer" for e in events)
    assert not any(e.title.startswith("Трансфер") for e in events)
    assert [e.at for e in events] == sorted(e.at for e in events)
    assert all(set(i.fixes) <= set(offers) for i in issues)
    assert all(offers[f].kind != "transfer" for i in issues for f in i.fixes)
    legs = [offers[i] for i in plan.selected if offers[i].kind == "transport"]
    if any(x.mode != "car" for x in legs):
        assert any("добираетесь сами" in e.title for e in events)
