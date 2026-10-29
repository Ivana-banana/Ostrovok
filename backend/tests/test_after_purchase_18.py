"""#18: after the voucher: early check-in, a date shift, a transfer to the booked hotel."""

import pytest
from talk import say

from api.contracts import HotelOffer
from api.store import SessionStore

SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def booked(store, *steps):
    r = say(store, SOCHI)
    for text in ("беру", *steps, "да", "подтверждаю"):
        r = say(store, text, r.session_id)
    assert r.stage == "booked"
    return r


@pytest.mark.parametrize("text", ["ранний заезд", "можно заселиться раньше?"])
def test_early_check_in_gives_the_time_and_no_price(store, text):
    r = booked(store)
    hotel = next(o for o in r.voucher.items if isinstance(o, HotelOffer))
    a = say(store, text, r.session_id)
    assert f"с {hotel.check_in:%H:%M}" in a.reply and hotel.title in a.reply
    assert "гарантии нет" in a.reply and "₽" not in a.reply  # TRAP-4: no price of our own
    assert a.voucher == r.voucher and a.stage == "booked"
    assert a.questions[0].options[0] == "Изменить бронь"
    assert "Правка для новой поездки" not in a.questions[0].text  # it is not an edit


def test_early_check_in_without_a_hotel_in_the_booking_says_so(store):
    r = booked(store)
    bare = r.voucher.model_copy(update={"items": [o for o in r.voucher.items if o.kind != "hotel"]})
    store.update(r.session_id, lambda cur: cur.model_copy(update={"voucher": bare}))
    a = say(store, "ранний заезд", r.session_id)
    assert "времени заселения у меня нет" in a.reply


def test_date_shift_after_the_voucher_stays_an_edit(store):
    r = booked(store)
    a = say(store, "сдвинь даты на два дня", r.session_id)
    assert a.reply.startswith(f"Бронь {r.voucher.booking_id} уже оформлена.")
    assert "сдвинь даты на два дня" in a.questions[0].text
    assert a.voucher == r.voucher


def test_transfer_missing_from_the_booking_is_offered_from_the_catalogue(store):
    r = booked(store, "без трансфера")
    assert not [o for o in r.voucher.items if o.kind == "transfer"]
    a = say(store, "трансфер до отеля", r.session_id)
    assert "Трансфер: аэропорт AER → Отель «Панорама»" in a.reply
    assert "1 200 ₽ (синтетика)" in a.reply  # TRAP-7: the price of an offer, marked
    assert "отдельная докупка" in a.reply
    assert a.voucher == r.voucher and a.cart == r.cart  # the booking stays
    assert store.last_attempt(r.session_id)[1] == r.voucher


def test_transfer_already_in_the_booking_is_not_offered_again(store):
    r = booked(store)
    a = say(store, "трансфер до отеля", r.session_id)
    assert a.reply.startswith(f"Трансфер уже в брони {r.voucher.booking_id}")
    assert "₽" not in a.reply
