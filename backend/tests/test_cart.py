"""Cart and simulated purchase (T9, #11): replace instead of duplicate, total check, voucher."""

import threading

import pytest
from fastapi.testclient import TestClient

from api.cart import edit_cart
from api.contracts import Cart, ChatReply, ChatRequest
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


def cart_post(client, r, action, offer_id):
    return client.post(
        "/api/cart", json={"session_id": r.session_id, "action": action, "offer_id": offer_id}
    )


def test_add_hotel_replaces_the_hotel_not_duplicates(client, at_checkout):
    r = at_checkout
    hotels = [o for o in r.plan.offers if o.kind == "hotel"]
    other = next(h for h in hotels if h.offer_id not in {o.offer_id for o in r.cart.items})
    body = cart_post(client, r, "add", other.offer_id).json()
    assert [o["kind"] for o in body["items"]].count("hotel") == 1
    assert other.offer_id in {o["offer_id"] for o in body["items"]}
    assert len(body["items"]) == len(r.cart.items)
    assert body["total_rub"] == sum(o["price_rub"] for o in body["items"])


def test_add_same_item_twice_keeps_one(client, at_checkout):
    r = at_checkout
    one = r.cart.items[0].offer_id
    cart_post(client, r, "add", one)
    body = cart_post(client, r, "add", one).json()
    assert [o["offer_id"] for o in body["items"]].count(one) == 1
    assert body["total_rub"] == r.cart.total_rub


def test_transport_replaces_per_direction(at_checkout):
    plan = at_checkout.plan
    out = [o for o in plan.offers if o.kind == "transport" and o.origin == plan.spec.origin]
    assert len(out) >= 2
    cart = edit_cart(plan, Cart(), "add", out[0].offer_id)
    cart = edit_cart(plan, cart, "add", out[1].offer_id)
    assert [o.offer_id for o in cart.items] == [out[1].offer_id]
    back = next(o for o in plan.offers if o.kind == "transport" and o.destination == "Москва")
    cart = edit_cart(plan, cart, "add", back.offer_id)
    assert len(cart.items) == 2  # the return flight is another slot


def test_remove_and_add_back_restores_total(client, at_checkout):
    r = at_checkout
    gone = r.cart.items[0]
    less = cart_post(client, r, "remove", gone.offer_id).json()
    assert less["total_rub"] == r.cart.total_rub - gone.price_rub
    back = cart_post(client, r, "add", gone.offer_id).json()
    assert back["total_rub"] == sum(o["price_rub"] for o in back["items"])
    assert {o["offer_id"] for o in back["items"]} == {o.offer_id for o in r.cart.items}
    assert len(back["items"]) == len(r.cart.items)


def test_trap_4_offer_outside_the_plan_is_rejected(client, at_checkout):
    """TRAP-4: the cart takes prices only from the offers of the plan."""
    r = at_checkout
    bad = cart_post(client, r, "add", "h-fake-0")
    assert bad.status_code == 422
    assert "нет в плане" in bad.json()["detail"]
    assert (
        client.get(f"/api/session/{r.session_id}").json()["cart"]["total_rub"] == r.cart.total_rub
    )


def test_unknown_session_is_404(client):
    body = {"session_id": "nope", "action": "add", "offer_id": "x"}
    assert client.post("/api/cart", json=body).status_code == 404
    assert (
        client.post(
            "/api/checkout", json={"session_id": "nope", "confirm_total_rub": 1}
        ).status_code
        == 404
    )


def test_checkout_with_another_total_is_409_and_books_nothing(client, at_checkout):
    r = at_checkout
    res = client.post(
        "/api/checkout",
        json={"session_id": r.session_id, "confirm_total_rub": r.cart.total_rub - 1},
    )
    assert res.status_code == 409
    assert "Итог корзины" in res.json()["detail"]
    assert client.get(f"/api/session/{r.session_id}").json()["voucher"] is None


def test_checkout_after_cart_edit_needs_the_new_total(client, at_checkout):
    r = at_checkout
    cart = cart_post(client, r, "remove", r.cart.items[-1].offer_id).json()
    stale = {"session_id": r.session_id, "confirm_total_rub": r.cart.total_rub}
    assert client.post("/api/checkout", json=stale).status_code == 409
    fresh = {"session_id": r.session_id, "confirm_total_rub": cart["total_rub"]}
    assert client.post("/api/checkout", json=fresh).status_code == 200


def test_checkout_issues_voucher_and_session_keeps_it(client, at_checkout):
    r = at_checkout
    body = {"session_id": r.session_id, "confirm_total_rub": r.cart.total_rub}
    v = client.post("/api/checkout", json=body).json()
    assert v["simulated"] is True and v["total_rub"] == r.cart.total_rub
    assert [i["offer_id"] for i in v["items"]] == [o.offer_id for o in r.cart.items]
    assert v["spec"]["date_from"] and v["spec"]["adults"] == 2
    reloaded = client.get(f"/api/session/{r.session_id}").json()  # page reload
    assert reloaded["stage"] == "booked" and reloaded["voucher"] == v
    assert reloaded["cart"]["total_rub"] == r.cart.total_rub
    # A second click returns the same booking, not a new one.
    again = client.post("/api/checkout", json=body)
    assert again.status_code == 200 and again.json()["booking_id"] == v["booking_id"]


def test_trap_7_voucher_prices_are_synthetic(client, at_checkout):
    """TRAP-7: every voucher item stays marked synthetic."""
    r = at_checkout
    body = {"session_id": r.session_id, "confirm_total_rub": r.cart.total_rub}
    v = client.post("/api/checkout", json=body).json()
    assert v["items"] and all(i["synthetic"] for i in v["items"])


def test_cart_is_closed_after_booking(client, at_checkout):
    r = at_checkout
    body = {"session_id": r.session_id, "confirm_total_rub": r.cart.total_rub}
    client.post("/api/checkout", json=body)
    res = cart_post(client, r, "remove", r.cart.items[0].offer_id)
    assert res.status_code == 409 and "уже оформлена" in res.json()["detail"]


def test_empty_cart_cannot_be_booked(client, at_checkout):
    r = at_checkout
    for o in r.cart.items:
        cart_post(client, r, "remove", o.offer_id)
    res = client.post("/api/checkout", json={"session_id": r.session_id, "confirm_total_rub": 0})
    assert res.status_code == 409 and "пуста" in res.json()["detail"]


def test_chat_confirmation_still_books_with_the_same_voucher(store, at_checkout):
    r = say(store, "подтверждаю", at_checkout.session_id)
    assert r.stage == "booked" and r.voucher.total_rub == at_checkout.cart.total_rub


def test_parallel_updates_of_one_session_lose_nothing(store, at_checkout):
    sid = at_checkout.session_id
    n = 12

    def add_wish(i):
        def change(prev):
            spec = prev.plan.spec.model_copy(update={"wishes": [*prev.plan.spec.wishes, f"w{i}"]})
            return prev.model_copy(update={"plan": prev.plan.model_copy(update={"spec": spec})})

        store.update(sid, change)

    threads = [threading.Thread(target=add_wish, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    wishes = store.get(sid).plan.spec.wishes
    assert {f"w{i}" for i in range(n)} <= set(wishes) and len(wishes) == len(set(wishes))


def test_failed_update_rolls_back(store, at_checkout):
    def boom(prev):
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        store.update(at_checkout.session_id, boom)
    assert store.get(at_checkout.session_id) == at_checkout
