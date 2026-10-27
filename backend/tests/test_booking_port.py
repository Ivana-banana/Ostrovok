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

    def __init__(self, refuse=None, extra_rub=0, drop=False, voucher_extra_rub=0):
        self.refuse, self.extra_rub, self.drop = refuse, extra_rub, drop
        self.voucher_extra_rub = voucher_extra_rub
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
        total = v.total_rub + self.voucher_extra_rub
        update = {"booking_id": f"F-{len(self.booked)}", "simulated": False, "total_rub": total}
        return v.model_copy(update=update)

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
        (TimeoutError("timeout"), "не ответила"),
        (ConnectionResetError("reset"), "не ответила"),
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
    assert second["status"] == "confirmed"
    # The attempt key changes after a cancel: a real provider refuses a reused key.
    assert foreign.booked == [r.session_id, f"{r.session_id}:2"]


def test_voucher_with_another_total_than_the_quote_is_409_and_cancelled(
    client, store, at_checkout, monkeypatch
):
    """#376: a foreign adapter books the cart at another total; the core does not keep it."""
    r = at_checkout
    foreign = Foreign(voucher_extra_rub=1000)
    monkeypatch.setattr(cart, "BOOKING", foreign)
    res = pay(client, r, r.cart.total_rub)
    assert res.status_code == 409 and "вместо" in res.json()["detail"]
    assert foreign.cancelled == ["F-1"]
    assert store.get(r.session_id).voucher is None


def test_programming_error_of_an_adapter_is_not_masked_as_a_provider_failure(
    client, at_checkout, monkeypatch
):
    """#376: only BookingRefused and network errors are a 409; a bug goes up with its traceback."""
    monkeypatch.setattr(cart, "BOOKING", Foreign(refuse=KeyError("bug")))
    with pytest.raises(KeyError):
        pay(client, at_checkout, at_checkout.cart.total_rub)


def test_repeat_after_a_failed_session_write_books_nothing_new(
    client, store, at_checkout, monkeypatch
):
    """#376: the provider call is outside the session lock; the attempt and its voucher are saved
    first, so a repeat after a failed write stores that voucher and calls `book` no more."""
    r = at_checkout
    foreign = Foreign()
    monkeypatch.setattr(cart, "BOOKING", foreign)
    real = store.update
    calls = []

    def fail_once(sid, change):
        calls.append(sid)
        if len(calls) == 1:
            raise OSError("disk full")
        return real(sid, change)

    monkeypatch.setattr(store, "update", fail_once)
    with pytest.raises(OSError):
        pay(client, r, r.cart.total_rub)
    assert store.get(r.session_id).voucher is None and foreign.booked == [r.session_id]
    v = pay(client, r, r.cart.total_rub).json()
    assert v["booking_id"] == "F-1" and foreign.booked == [r.session_id]
    assert store.get(r.session_id).voucher.booking_id == "F-1"


def test_book_runs_outside_the_session_write_lock(client, store, at_checkout, monkeypatch):
    """#376: while the provider books, another session can be written."""
    r = at_checkout
    other = r.model_copy(update={"session_id": "other"})
    store.put(other)

    class Probe(Foreign):
        def book(self, spec, c, session_id):
            store.update("other", lambda prev: prev.model_copy(update={"reply": "written"}))
            return super().book(spec, c, session_id)

    monkeypatch.setattr(cart, "BOOKING", Probe())
    monkeypatch.setattr(
        store, "update", _no_nested(store.update)
    )  # fails if book() runs inside an update
    assert pay(client, r, r.cart.total_rub).status_code == 200
    assert store.get("other").reply == "written"


def _no_nested(update):
    depth = []

    def wrapped(sid, change):
        assert not depth, "store.update is called inside another store.update"
        depth.append(sid)
        try:
            return update(sid, change)
        finally:
            depth.pop()

    return wrapped
