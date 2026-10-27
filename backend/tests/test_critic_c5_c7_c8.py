"""#363: critic cards C5, C7 and C8 (eval/cases_critic.json). Offline, rules only (TRAP-1).

C5: «прилетаем 14 ноября в 01:30 ночи, улетаем 18-го» is 4 nights: a clock time is no length.
C7: six guests do not sleep in one standard room: two rooms, the price and a note say so.
C8: Воронеж flies through VOZ, an airport closed to flights (research/domain.md, 27.10.2026):
no flight to it, a train or a flight through the nearest open airport, and the reason.
"""

from datetime import date, time, timedelta

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore

C5 = (
    "Билеты у нас уже куплены, прилетаем в Калининград 14 ноября в 01:30 ночи, улетаем 18-го. "
    "Нужен только отель и трансфер из аэропорта, двое взрослых"
)
C7 = "Анапа, 7 ночей с 1 августа, двое взрослых и четверо детей 3, 5, 8 и 12 лет"
C7_FOUR = "Анапа, 7 ночей с 1 августа, четверо взрослых"
C8 = "Воронеж, 7 ночей с 1 августа, двое взрослых, самолётом из Москвы"
C8_FAR = "Воронеж из Новосибирска, 7 ночей с 1 августа, двое взрослых"


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


def test_C5_night_clock_time_is_no_length_and_the_return_day_ends_the_stay(store):
    spec = say(store, C5).plan.spec
    start = spec.date_from
    assert (start.day, start.month, spec.nights) == (14, 11, 4)
    assert spec.date_to == date(start.year, 11, 18)


def test_C5_a_return_day_before_the_start_day_is_in_the_next_month(store):
    spec = say(store, "Калининград с 28 ноября, улетаем 3-го, двое взрослых").plan.spec
    assert (spec.date_from.day, spec.date_from.month) == (28, 11)
    assert (spec.date_to.day, spec.date_to.month, spec.nights) == (3, 12, 5)


def test_C7_six_guests_take_two_rooms_and_pay_for_them(store):
    six = say(store, C7)
    assert "2 номера" in six.reply
    four = say(store, C7_FOUR)
    price = {(o.title, o.meal, o.cancellation.refundable): o.price_rub for o in four.plan.offers
             if o.kind == "hotel"}  # fmt: skip
    pairs = [(o.price_rub, price[(o.title.removesuffix(" (2 номера)"), o.meal,
              o.cancellation.refundable)]) for o in six.plan.offers if o.kind == "hotel"]  # fmt: skip
    assert pairs
    assert all(p6 >= 1.3 * p4 for p6, p4 in pairs)
    r = say(store, "беру", six.session_id)
    assert all(o.title.endswith("(2 номера)") for o in chosen(r, "hotel"))  # the cart line


def test_C8_no_flight_to_a_closed_airport_a_train_and_the_reason(store):
    r = say(store, C8)
    legs = [o for o in r.plan.offers if o.kind == "transport"]
    assert legs and all(o.mode != "flight" for o in legs)
    assert any(o.mode == "train" for o in legs)
    assert "Аэропорт VOZ (Воронеж) закрыт для рейсов" in r.reply


def test_C8_far_origin_flies_through_the_nearest_open_airport_to_the_cart(store):
    """No train from Новосибирск to Воронеж: the flight lands at DME, the road is by distance."""
    r = say(store, C8_FAR)
    r = say(store, "беру", r.session_id)
    assert r.stage == "assemble"
    legs = chosen(r, "transport")
    assert legs and all("VOZ" not in o.title for o in legs)
    assert any(o.mode == "flight" and "через DME" in o.title for o in legs)
    rides = [o for o in chosen(r, "transfer") if "DME" in o.title]
    assert rides
    # 426 km: 100 km at 35 km/h, the rest at the car speed — not 12 h at the city speed.
    assert all(o.arrive_at - o.depart_at < timedelta(hours=8) for o in rides)
    assert not [i for i in r.plan.issues if i.severity == "error"]


def test_C8_substitute_airport_is_in_the_country_of_the_place(store):
    """Курск (URS closed): the nearest open airport by the line is GME in Беларусь; the
    substitute is a Russian one, and the flight time is to it."""
    r = say(store, "Курск из Новосибирска, 7 ночей с 1 августа, двое взрослых")
    flights = [o for o in r.plan.offers if o.kind == "transport" and o.mode == "flight"]
    assert flights and all("через DME" in o.title and "GME" not in o.title for o in flights)
    assert "GME" not in r.reply


def test_C8_gelendzhik_return_leaves_before_the_airport_closes(store):
    """GDZ takes flights 08:30-20:00 MSK (research/domain.md, 27.10.2026)."""
    r = say(store, "Геленджик из Москвы, 3 ночи с 20 января")
    back = [o for o in r.plan.offers if o.kind == "transport" and o.origin == "Геленджик"]
    assert back and all(o.depart_at.time() <= time(20, 0) for o in back if o.mode == "flight")


def test_C8_far_substitute_airport_gives_way_to_a_train(store):
    """Белгород (EGO closed): DME is 542 km away, farther than catalog.AIRPORT_KM; a train goes
    from Санкт-Петербург, so the cart has the train, not a flight and a 7-hour taxi."""
    r = say(store, "Белгород из Санкт-Петербурга, 7 ночей с 1 августа, двое взрослых")
    legs = [o for o in r.plan.offers if o.kind == "transport"]
    assert legs and all(o.mode != "flight" for o in legs)
    r = say(store, "беру", r.session_id)
    assert any(o.mode == "train" for o in chosen(r, "transport"))
    assert not [o for o in chosen(r, "transfer") if "DME" in o.title]


def test_C5_own_tickets_no_flight_a_night_transfer_and_the_night_before(store):
    """#408: own tickets — the cart has the hotel and the transfer from 01:30, no transport;
    the 11-hour wait for the check-in is a warning fixed by the night before."""
    r = say(store, C5)
    assert r.plan.spec.own_transport and r.plan.spec.arrive_at.hour == 1
    assert not [o for o in r.plan.offers if o.kind == "transport"]
    warn = next(i for i in r.plan.issues if "заезд с 14:00" in i.text)
    assert any(f.endswith("-prev") for f in warn.fixes)
    r = say(store, "да", say(store, "беру", r.session_id).session_id)
    assert r.stage == "checkout"
    assert not [o for o in r.cart.items if o.kind == "transport"]
    ride = next(o for o in r.cart.items if o.kind == "transfer")
    assert "аэропорт KGD →" in ride.title and time(1, 30) <= ride.depart_at.time() <= time(2, 30)


def test_C5_own_departure_time_gets_a_transfer_before_it(store):
    r = say(store, "Билеты уже есть, Калининград с 14 ноября, улетаем 18-го в 15:00, двое взрослых")
    r = say(store, "беру", r.session_id)
    ride = next(o for o in chosen(r, "transfer") if "→ аэропорт" in o.title)
    assert ride.arrive_at <= r.plan.spec.leave_at - timedelta(hours=2)
