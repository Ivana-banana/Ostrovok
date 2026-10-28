"""#503: after the voucher «Изменить бронь» opens a new trip: the same terms plus the user's edit."""

from datetime import timedelta

import pytest
from talk import say

from api.store import SessionStore

SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def booked(store):
    r = say(store, SOCHI)
    for text in ("беру", "да", "подтверждаю"):
        r = say(store, text, r.session_id)
    assert r.stage == "booked"
    return r


def test_change_booking_shifts_dates_and_keeps_the_rest(store):
    r = booked(store)
    asked = say(store, "а если на 2 дня позже", r.session_id)
    assert asked.voucher == r.voucher
    assert asked.questions[0].options[0] == "Изменить бронь"
    assert "а если на 2 дня позже" in asked.questions[0].text  # the user sees the edit
    new = say(store, "Изменить бронь", r.session_id)
    old, now = r.plan.spec, new.plan.spec
    assert new.stage == "compare" and new.voucher is None and new.cart.items == []
    assert now.destinations == old.destinations and now.nights == old.nights
    assert (now.adults, now.children_ages) == (old.adults, old.children_ages)
    assert now.date_from == old.date_from + timedelta(days=2)
    assert now.date_to == old.date_to + timedelta(days=2)
    assert f"Бронь {r.voucher.booking_id} остаётся оформленной" in new.reply
    assert "не переоформлена" in new.reply
    # the first voucher stays in the store, untouched
    assert store.last_attempt(r.session_id)[1] == r.voucher


def test_two_edits_before_the_press_both_apply(store):
    r = booked(store)
    say(store, "а если на 2 дня позже", r.session_id)
    say(store, "и трое взрослых", r.session_id)
    new = say(store, "Изменить бронь", r.session_id)
    assert new.plan.spec.adults == 3
    assert new.plan.spec.date_from == r.plan.spec.date_from + timedelta(days=2)


def test_change_booking_without_an_edit_copies_the_trip(store):
    r = booked(store)
    new = say(store, "Изменить бронь", r.session_id)
    assert new.stage == "compare" and new.voucher is None and new.cart.items == []
    assert new.plan.spec == r.plan.spec
    assert "Правки пока нет" in new.reply


def test_new_trip_books_anew_and_the_first_voucher_stays(store):
    r = booked(store)
    say(store, "а если на 2 дня позже", r.session_id)
    new = say(store, "Изменить бронь", r.session_id)
    for text in ("беру", "да", "подтверждаю"):
        new = say(store, text, r.session_id)
    assert new.stage == "booked" and new.voucher.booking_id != r.voucher.booking_id
