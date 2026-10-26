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
    """Cart after "add" or "remove". Raises HTTPException for an offer outside the plan."""
    offer = next((o for o in plan.offers if o.offer_id == offer_id), None)
    if offer is None:
        raise HTTPException(status_code=422, detail="Такого предложения нет в плане поездки.")
    if action == "remove":
        gone = {offer_id} | {
            o.offer_id for o in cart.items if o.kind == "extra" and o.for_offer_id == offer_id
        }
        return make_cart([o for o in cart.items if o.offer_id not in gone])
    if offer.kind == "extra" and all(o.offer_id != offer.for_offer_id for o in cart.items):
        raise HTTPException(
            status_code=409, detail="Услуга относится к позиции, которой нет в корзине."
        )
    slot = _slot(offer, plan.spec)
    items = list(cart.items)
    for i, o in enumerate(items):
        if _slot(o, plan.spec) == slot:
            items[i] = offer  # replace in place, no duplicate
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


def _known(prev: ChatReply | None) -> ChatReply:
    if prev is None:
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    return prev


def cart_route(req: CartAction, store: SessionStore) -> Cart:
    def change(prev: ChatReply | None) -> ChatReply:
        reply = _known(prev)
        if reply.voucher:
            raise HTTPException(
                status_code=409, detail=f"Бронь {reply.voucher.booking_id} уже оформлена."
            )
        cart = edit_cart(reply.plan, reply.cart, req.action, req.offer_id)
        return reply.model_copy(update={"cart": cart})

    return store.update(req.session_id, change).cart


def checkout_route(req: CheckoutRequest, store: SessionStore) -> Voucher:
    def change(prev: ChatReply | None) -> ChatReply:
        reply = _known(prev)
        if reply.voucher:  # a repeated click returns the same booking, never a second one
            if reply.voucher.total_rub != req.confirm_total_rub:
                raise HTTPException(
                    status_code=409,
                    detail=f"Бронь уже оформлена на {_rub(reply.voucher.total_rub)}.",
                )
            return reply
        if not reply.cart.items:
            raise HTTPException(status_code=409, detail="Корзина пуста: оформлять нечего.")
        # Prices come from the plan, never from the stored or the sent cart (TRAP-4).
        cart = make_cart([o for o in reply.plan.offers if o.offer_id in _ids(reply.cart)])
        if cart.total_rub != req.confirm_total_rub:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Итог корзины {_rub(cart.total_rub)}, а вы подтверждаете "
                    f"{_rub(req.confirm_total_rub)}. Проверьте сумму и подтвердите ещё раз."
                ),
            )
        voucher = book(reply.plan.spec, cart)
        text = f"Бронь {voucher.booking_id} оформлена на {_rub(voucher.total_rub)}. Оплата имитирована."
        budget = reply.plan.spec.budget_rub
        if budget is not None and voucher.total_rub > budget:
            text += f" Итог больше бюджета {_rub(budget)}."
        return reply.model_copy(
            update={
                "stage": "booked",
                "reply": text,
                "questions": [],
                "cart": cart,
                "voucher": voucher,
            }
        )

    return store.update(req.session_id, change).voucher  # type: ignore[return-value]


def _ids(cart: Cart) -> set[str]:
    return {o.offer_id for o in cart.items}
