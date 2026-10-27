"""Booking port (ADR-0002, #287): the simulation is one adapter of BookingProvider; the core
keeps the rules (price re-check, refusal -> 409, one booking per session).

Run this file after you assign your own adapter to api.cart.BOOKING.
"""

import inspect

import pytest
from fastapi.testclient import TestClient

from api import cart
from api.contracts import BookingProvider, BookingRefused, Cart, ChatReply, ChatRequest, TripSpec
from api.main import app, get_store
from api.model import chat
from api.store import SessionStore

KAZAN = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё."


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


@pytest.fixture
def client(store):
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


def say(store, text, sid=None) -> ChatReply:
    return chat(ChatRequest(session_id=sid, message=text), store)


@pytest.fixture
def at_checkout(store) -> ChatReply:
    r = say(store, KAZAN)
    r = say(store, "беру", r.session_id)
    r = say(store, "да", r.session_id)
    assert r.stage == "checkout" and r.cart.items
    return r


def pay(client, r, total):
    return client.post(
        "/api/checkout", json={"session_id": r.session_id, "confirm_total_rub": total}
    )


class Foreign(cart.SimulatedBooking):
    """A test adapter of another provider: counts calls, can refuse or change prices."""

    def __init__(self, refuse=None, extra_rub=0, drop=False):
        self.refuse, self.extra_rub, self.drop = refuse, extra_rub, drop
        self.booked: list[str] = []
        self.cancelled: list[str] = []

    def quote(self, spec: TripSpec, c: Cart) -> Cart:
        items = [o.model_copy(update={"price_rub": o.price_rub + self.extra_rub}) for o in c.items]
        return cart.make_cart(items[1:] if self.drop else items)

    def book(self, spec, c, session_id):
        if self.refuse:
            raise self.refuse
        self.booked.append(session_id)
        v = cart.book(spec, c)
        return v.model_copy(update={"booking_id": f"F-{len(self.booked)}", "simulated": False})

    def cancel(self, booking_id):
        self.cancelled.append(booking_id)


def test_simulated_booking_has_the_methods_of_the_port():
    """Conformance: the active adapter has every method of BookingProvider, same parameters."""
    assert isinstance(cart.BOOKING, cart.SimulatedBooking)
    for name in ("quote", "book", "cancel"):
        want = list(inspect.signature(getattr(BookingProvider, name)).parameters)
        got = list(inspect.signature(getattr(cart.BOOKING, name)).parameters)
        assert got == want[1:], name  # the Protocol lists self


def test_simulated_voucher_is_confirmed_and_marked_simulated(client, at_checkout):
    v = pay(client, at_checkout, at_checkout.cart.total_rub).json()
    assert v["status"] == "confirmed" and v["simulated"] is True


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (BookingRefused("нет мест в отеле"), "нет мест в отеле"),
        (RuntimeError("timeout"), "не ответила"),
    ],
)
def test_refusal_of_a_foreign_adapter_is_409_with_a_reason_and_no_dead_end(
    client, store, at_checkout, monkeypatch, error, reason
):
    r = at_checkout
    monkeypatch.setattr(cart, "BOOKING", Foreign(refuse=error))
    res = pay(client, r, r.cart.total_rub)
    assert res.status_code == 409 and reason in res.json()["detail"]
    kept = store.get(r.session_id)
    assert kept.voucher is None and kept.stage == "checkout" and kept.cart == r.cart
    # The chat answers with the reason, too, and the cart stays open.
    out = say(store, "подтверждаю", r.session_id)
    assert out.voucher is None and out.stage == "checkout" and reason in out.reply
    # Not a dead end: the provider is back, the same confirmation books.
    monkeypatch.setattr(cart, "BOOKING", Foreign())
    assert pay(client, r, r.cart.total_rub).status_code == 200


def test_quote_with_another_total_is_409_and_the_next_confirmation_books_it(
    client, store, at_checkout, monkeypatch
):
    r = at_checkout
    foreign = Foreign(extra_rub=100)
    monkeypatch.setattr(cart, "BOOKING", foreign)
    res = pay(client, r, r.cart.total_rub)
    assert res.status_code == 409 and "Итог корзины" in res.json()["detail"]
    assert not foreign.booked
    fresh = store.get(r.session_id)  # the web reloads the session after a 409
    new_total = r.cart.total_rub + 100 * len(r.cart.items)
    assert fresh.voucher is None and fresh.stage == "checkout"
    assert fresh.cart.total_rub == new_total
    v = pay(client, r, fresh.cart.total_rub).json()
    assert v["total_rub"] == new_total and foreign.booked == [r.session_id]


def test_chat_confirmation_after_a_price_change_shows_the_new_total(
    store, at_checkout, monkeypatch
):
    r = at_checkout
    monkeypatch.setattr(cart, "BOOKING", Foreign(extra_rub=100))
    out = say(store, "подтверждаю", r.session_id)
    assert out.voucher is None and "Итог корзины" in out.reply
    out = say(store, "подтверждаю", r.session_id)
    assert out.stage == "booked" and out.voucher.total_rub == out.cart.total_rub


def test_quote_that_drops_an_item_books_nothing(client, store, at_checkout, monkeypatch):
    r = at_checkout
    monkeypatch.setattr(cart, "BOOKING", Foreign(drop=True))
    res = pay(client, r, r.cart.total_rub)
    assert res.status_code == 409 and "состав корзины" in res.json()["detail"]
    assert store.get(r.session_id).voucher is None


def test_repeated_payment_is_one_booking_of_the_core(client, store, at_checkout, monkeypatch):
    r = at_checkout
    foreign = Foreign()
    monkeypatch.setattr(cart, "BOOKING", foreign)
    first = pay(client, r, r.cart.total_rub).json()
    assert first["simulated"] is False and "Оплата принята" in store.get(r.session_id).reply
    again = pay(client, r, r.cart.total_rub).json()
    chat_again = say(store, "подтверждаю", r.session_id)
    assert first["booking_id"] == again["booking_id"] == chat_again.voucher.booking_id == "F-1"
    assert foreign.booked == [r.session_id]


def test_after_cancel_the_session_gets_a_new_booking(client, store, at_checkout, monkeypatch):
    r = at_checkout
    foreign = Foreign()
    monkeypatch.setattr(cart, "BOOKING", foreign)
    first = pay(client, r, r.cart.total_rub).json()
    foreign.cancel(first["booking_id"])

    def cancelled(prev):
        v = prev.voucher.model_copy(update={"status": "cancelled"})
        return prev.model_copy(update={"voucher": v, "stage": "checkout"})

    store.update(r.session_id, cancelled)
    second = pay(client, r, r.cart.total_rub).json()
    assert second["booking_id"] == "F-2" != first["booking_id"]
    assert second["status"] == "confirmed" and foreign.booked == [r.session_id] * 2
