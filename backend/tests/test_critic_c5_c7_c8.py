"""#363: critic cards C5, C7 and C8 (eval/cases_critic.json). Offline, rules only (TRAP-1).

C5: «прилетаем 14 ноября в 01:30 ночи, улетаем 18-го» is 4 nights: a clock time is no length.
C7: six guests do not sleep in one standard room: two rooms, the price and a note say so.
C8: Анапа flies through GDZ, an airport closed to flights: no flight, a train and the reason.
"""

from datetime import date

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
C8 = "Анапа, 7 ночей с 1 августа, двое взрослых, самолётом из Москвы"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GIGACHAT_AUTH_KEY", raising=False)
    return SessionStore(tmp_path / "sessions.db")


def say(store, text):
    return chat(ChatRequest(message=text), store)


def test_C5_night_clock_time_is_no_length_and_the_return_day_ends_the_stay(store):
    spec = say(store, C5).plan.spec
    start = spec.date_from
    assert (start.day, start.month, spec.nights) == (14, 11, 4)
    assert spec.date_to == date(start.year, 11, 18)


def test_C7_six_guests_take_two_rooms_and_pay_for_them(store):
    six = say(store, C7)
    assert "2 номера" in six.reply
    four = say(store, C7_FOUR)
    price = {(o.title, o.meal, o.cancellation.refundable): o.price_rub for o in four.plan.offers
             if o.kind == "hotel"}  # fmt: skip
    pairs = [(o.price_rub, price[(o.title, o.meal, o.cancellation.refundable)])
             for o in six.plan.offers if o.kind == "hotel"]  # fmt: skip
    assert pairs
    assert all(p6 >= 1.3 * p4 for p6, p4 in pairs)


def test_C8_no_flight_through_a_closed_airport_a_train_and_the_reason(store):
    r = say(store, C8)
    legs = [o for o in r.plan.offers if o.kind == "transport"]
    assert legs and all(o.mode != "flight" for o in legs)
    assert any(o.mode == "train" for o in legs)
    assert "GDZ" in r.reply and "закрыт" in r.reply
