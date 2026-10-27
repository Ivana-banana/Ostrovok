"""#323 (C1, eval/cases_critic.json): a transport line names its seats and child fares.

The label comes from the same fare rules as the price (offers._passengers, TRAP-4): a train
gives half a fare from 5 to 9 years and a free place without a seat under 5; a plane gives a
child fare from 2 to 11 years and an infant without a seat under 2. The cart, the voucher and
the web show the offer title, so the label is in the title. Offline, rules only (TRAP-1).
"""

from datetime import date

import pytest

from api.contracts import ChatRequest, TransportOffer, TripSpec
from api.model import chat
from api.offers import _fares, resolve_place, transport_offers
from api.store import SessionStore

C1 = (
    "Едем я, муж, двое детей 6 и 9 лет и бабушка с дедушкой, в Анапу на 7 ночей с 1 июля, "
    "поездом из Москвы"
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GIGACHAT_AUTH_KEY", raising=False)
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def legs(mode: str, adults: int, kids: list[int], place: str = "Анапа") -> list[TransportOffer]:
    spec = TripSpec(
        origin="Москва",
        destinations=[place],
        date_from=date(2027, 7, 1),
        date_to=date(2027, 7, 8),
        adults=adults,
        children_ages=kids,
        modes=[mode],
    )
    found = transport_offers(spec, resolve_place("Москва"), resolve_place(place))
    assert found and all(x.mode == mode for x in found)
    return found


def test_C1_TRAP_4_train_title_names_six_seats_and_two_child_fares():
    for leg in legs("train", 4, [6, 9]):
        assert ", 6 мест (2 детских тарифа)" in leg.title


def test_TRAP_4_train_under_five_travels_free_without_a_seat():
    for leg in legs("train", 2, [3, 7]):
        assert ", 3 места (1 детский тариф, 1 ребёнок до 5 лет без места)" in leg.title


def test_TRAP_4_flight_child_fare_and_infant_without_a_seat():
    for leg in legs("flight", 2, [1, 4, 12], place="Сочи"):
        assert ", 4 места (1 детский тариф, 1 младенец без места)" in leg.title


def test_TRAP_4_adults_only_name_the_seats_without_a_child_note():
    for leg in legs("train", 2, []):
        assert ", 2 места" in leg.title and "детск" not in leg.title
    for leg in legs("train", 1, []):
        assert ", 1 место" in leg.title


def test_TRAP_4_the_label_follows_the_price_rules():
    """The seats and the child fares add up to the fare units of the price, not apart from it."""
    spec = TripSpec(adults=4, children_ages=[3, 6, 9, 11])
    # train: 4 adults + 11 years full, 6 and 9 half, 3 free -> 4 + 1 + 0.5 + 0.5 = 6
    assert _fares(spec, "train") == 6
    leg = legs("train", 4, [3, 6, 9, 11])[0]
    assert ", 7 мест (2 детских тарифа, 1 ребёнок до 5 лет без места)" in leg.title


def test_TRAP_4_car_title_has_no_seats():
    for leg in legs("car", 4, [6, 9], place="Тула"):
        assert "мест" not in leg.title.replace("ночёвок", "")


def test_C1_TRAP_4_cart_and_voucher_lines_show_the_seats(store):
    r = say(store, C1)
    spec = r.plan.spec
    assert r.stage == "compare"
    r = say(store, "беру", r.session_id)
    r = say(store, "да", r.session_id)
    assert r.stage == "checkout"
    seats = spec.adults + sum(a >= 5 for a in spec.children_ages)
    kids = sum(5 <= a < 10 for a in spec.children_ages)
    trains = [o for o in r.cart.items if o.kind == "transport"]
    assert trains and all(o.mode == "train" for o in trains)
    for o in trains:
        assert f", {seats} мест" in o.title and f"({kids} детских тарифа)" in o.title
    r = say(store, "подтверждаю", r.session_id)
    assert r.stage == "booked" and r.voucher
    assert [o.title for o in r.voucher.items if o.kind == "transport"] == [o.title for o in trains]
