"""Cart and simulated purchase (T9, #11): POST /api/cart and POST /api/checkout.

- One item per slot: one hotel, one outbound and one return transport. Adding an offer of
  an occupied slot replaces the old item; it never duplicates. Other offers are keyed by id.
- Only offers of the plan can enter the cart, and the price is taken from the plan, not from
  the client (TRAP-4). Every price is synthetic (TRAP-7).
- Checkout needs the total the user saw. A different total means the cart changed: 409.
- The voucher is stored in the session reply, so a reload shows it.
- The session is changed in one store transaction, so parallel requests lose no update.
"""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from api.contracts import (
    Cart,
    CartAction,
    ChatReply,
    CheckoutRequest,
    Offer,
    Plan,
    TripSpec,
    Voucher,
)
from api.store import SessionStore


def _rub(n: int) -> str:
    return f"{n:,} ₽".replace(",", " ")


def _slot(offer: Offer, spec: TripSpec) -> str:
    """Offers of one slot replace each other."""
    if offer.kind == "hotel":
        return "hotel"
    if offer.kind == "transport":
        return "back" if offer.destination == spec.origin else "out"
    return offer.offer_id


def make_cart(items: list[Offer]) -> Cart:
    return Cart(items=items, total_rub=sum(o.price_rub for o in items))


def fill_cart(plan: Plan) -> Cart:
    """Cart with the selected items; it replaces the old cart."""
    return make_cart([o for o in plan.offers if o.offer_id in plan.selected])


def edit_cart(plan: Plan, cart: Cart, action: str, offer_id: str) -> Cart:
    """Cart after "add" or "remove". Raises CartRefusal for an offer outside the plan."""
    offer = next((o for o in plan.offers if o.offer_id == offer_id), None)
    if offer is None:
        raise CartRefusal(422, "Такого предложения нет в плане поездки.")
    if action == "remove":
        gone = {offer_id} | {
            o.offer_id for o in cart.items if o.kind == "extra" and o.for_offer_id == offer_id
        }
        return make_cart([o for o in cart.items if o.offer_id not in gone])
    if offer.kind == "extra" and all(o.offer_id != offer.for_offer_id for o in cart.items):
        raise CartRefusal(409, "Услуга относится к позиции, которой нет в корзине.")
    slot = _slot(offer, plan.spec)
    items = list(cart.items)
    for i, o in enumerate(items):
        if _slot(o, plan.spec) == slot:
            items[i] = offer  # replace in place, no duplicate
            # Extras belong to the old item; they do not follow the new one.
            if o.offer_id != offer.offer_id:
                items = [x for x in items if x.kind != "extra" or x.for_offer_id != o.offer_id]
            return make_cart(items)
    return make_cart(items + [offer])


def book(spec: TripSpec, cart: Cart) -> Voucher:
    """Simulated purchase of the cart. The total is the sum of the item prices."""
    now = datetime.now(UTC)
    return Voucher(
        booking_id=f"B-{now:%Y%m%d%H%M%S}-{uuid4().hex[:4].upper()}",
        items=cart.items,
        spec=spec,
        total_rub=sum(o.price_rub for o in cart.items),
        created_at=now,
    )


class CartRefusal(Exception):
    """The cart or the checkout cannot go on. `status` is the HTTP code, `text` is for the user."""

    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status = status
        self.text = text


def _guard_cart_open(reply: ChatReply) -> None:
    if reply.voucher:
        raise CartRefusal(409, f"Бронь {reply.voucher.booking_id} уже оформлена.")
    if reply.stage != "checkout":
        raise CartRefusal(
            409, "Корзина откроется, когда вы согласитесь с планом: напишите «да» после плана."
        )


def checkout_reply(reply: ChatReply, confirm_total_rub: int) -> ChatReply:
    """The one booking rule for POST /api/checkout and the chat «подтверждаю».

    The cart must be open and not empty. Prices come from the plan, never from the stored or
    the sent cart (TRAP-4). The confirmed total must equal the cart total. A repeated call
    returns the same booking.
    """
    if reply.voucher:
        if reply.voucher.total_rub != confirm_total_rub:
            raise CartRefusal(409, f"Бронь уже оформлена на {_rub(reply.voucher.total_rub)}.")
        return reply
    _guard_cart_open(reply)
    in_cart = {o.offer_id for o in reply.cart.items}
    cart = make_cart([o for o in reply.plan.offers if o.offer_id in in_cart])
    if not cart.items:
        raise CartRefusal(409, "Корзина пуста: оформлять нечего.")
    if cart.total_rub != confirm_total_rub:
        raise CartRefusal(
            409,
            f"Итог корзины {_rub(cart.total_rub)}, а вы подтверждаете "
            f"{_rub(confirm_total_rub)}. Проверьте сумму и подтвердите ещё раз.",
        )
    voucher = book(reply.plan.spec, cart)
    text = f"Бронь {voucher.booking_id} оформлена на {_rub(voucher.total_rub)}. Оплата имитирована."
    return reply.model_copy(
        update={"stage": "booked", "reply": text, "questions": [], "cart": cart, "voucher": voucher}
    )


def _known(prev: ChatReply | None) -> ChatReply:
    if prev is None:
        raise CartRefusal(404, "Сессия не найдена.")
    return prev


def _http(e: CartRefusal) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.text)


def cart_route(req: CartAction, store: SessionStore) -> Cart:
    def change(prev: ChatReply | None) -> ChatReply:
        reply = _known(prev)
        _guard_cart_open(reply)
        cart = edit_cart(reply.plan, reply.cart, req.action, req.offer_id)
        return reply.model_copy(update={"cart": cart})

    try:
        return store.update(req.session_id, change).cart
    except CartRefusal as e:
        raise _http(e) from e


def checkout_route(req: CheckoutRequest, store: SessionStore) -> Voucher:
    def change(prev: ChatReply | None) -> ChatReply:
        return checkout_reply(_known(prev), req.confirm_total_rub)

    try:
        voucher = store.update(req.session_id, change).voucher
    except CartRefusal as e:
        raise _http(e) from e
    assert voucher is not None
    return voucher
