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


def test_check_in_time_is_not_the_hotel_rule_637(store):
    r = booked(store)
    a = say(store, "ранний заезд", r.session_id)
    assert "обычно с " in a.reply and "уточните у отеля" in a.reply


@pytest.mark.parametrize("text", ["раннее заселение", "возможен ранний чекин?"])
def test_early_check_in_wordings_637(store, text):
    r = booked(store)
    a = say(store, text, r.session_id)
    assert "гарантии нет" in a.reply and "Правка для новой поездки" not in a.questions[0].text


def test_arrive_a_day_earlier_is_a_date_edit_637(store):
    r = booked(store)
    a = say(store, "хочу заехать раньше на день", r.session_id)
    assert "гарантии нет" not in a.reply
    assert "заехать раньше на день" in a.questions[0].text


def test_other_city_and_transfer_is_an_edit_637(store):
    r = booked(store)
    a = say(store, "другой город и трансфер", r.session_id)
    assert not a.reply.startswith("Трансфер") and "Трансфера" not in a.reply
    assert "другой город и трансфер" in a.questions[0].text


@pytest.mark.parametrize("text", ["трансфер до отеля в Сочи?", "трансфер для двоих взрослых"])
def test_transfer_remark_of_the_booked_trip_is_not_an_edit_637(store, text):
    r = booked(store)
    a = say(store, text, r.session_id)
    assert a.reply.startswith("Трансфер уже в брони")


@pytest.mark.parametrize("text", ["ранний заезд 20 ноября", "ранний заезд на 3 ночи"])
def test_early_check_in_with_booked_dates_is_not_an_edit_637(store, text):
    r = booked(store)
    a = say(store, text, r.session_id)
    assert "гарантии нет" in a.reply and "Правка для новой поездки" not in a.questions[0].text


def test_early_check_in_price_question_is_not_a_budget_edit_637(store):
    r = booked(store)
    a = say(store, "ранний заезд за 1000 рублей?", r.session_id)
    assert "гарантии нет" in a.reply and "Цены раннего заезда в брони нет" in a.reply
