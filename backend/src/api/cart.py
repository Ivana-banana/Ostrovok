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
from api.offers import transfer_id
from api.store import SessionStore
from api.timeline import build_timeline


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
        items = [o for o in cart.items if o.offer_id not in gone]
        if offer.kind in ("hotel", "transport"):
            items = _drop_orphan_transfers(items)
        return make_cart(items)
    if offer.kind == "extra" and all(o.offer_id != offer.for_offer_id for o in cart.items):
        raise CartRefusal(409, "Услуга относится к позиции, которой нет в корзине.")
    if offer.kind == "transfer" and offer.offer_id not in _pair_ids(cart.items):
        raise CartRefusal(409, "Трансфер относится к отелю и рейсу, которых нет в корзине.")
    slot = _slot(offer, plan.spec)
    items = list(cart.items)
    for i, o in enumerate(items):
        if _slot(o, plan.spec) == slot:
            items[i] = offer  # replace in place, no duplicate
            # Extras belong to the old item; they do not follow the new one.
            if o.offer_id != offer.offer_id:
                items = [x for x in items if x.kind != "extra" or x.for_offer_id != o.offer_id]
            break
    else:
        items.append(offer)
    return make_cart(_with_transfers(plan, items, offer))


def _pair_ids(items: list[Offer]) -> set[str]:
    """Ids of the transfers whose hotel and leg are both in `items`. A transfer has no field
    for them, so the pair is found by the id hash (`transfer_id`)."""
    hotels = [o for o in items if o.kind == "hotel"]
    legs = [o for o in items if o.kind == "transport"]
    return {transfer_id(h.offer_id, x.offer_id) for h in hotels for x in legs}


def _drop_orphan_transfers(items: list[Offer]) -> list[Offer]:
    """Keep a transfer only while its hotel and its leg are both in the cart."""
    valid = _pair_ids(items)
    return [o for o in items if o.kind != "transfer" or o.offer_id in valid]


def refused(plan: Plan) -> set[str]:
    """Slots ("out", "back") whose transfer the user refused (#200): the selected hotel and leg
    have a transfer offer, and it is not selected. The plan selects the transfer of every pair
    (pick_hotel); only the user takes it out («без трансфера», the cart). The refusal so lives
    in `plan.selected`; the contract has no field for it. Same rule as api/timeline.py."""
    by_id = {o.offer_id: o for o in plan.offers}
    chosen = [by_id[i] for i in plan.selected if i in by_id]
    hotels = [o for o in chosen if o.kind == "hotel"]
    legs = [o for o in chosen if o.kind == "transport"]
    return {
        _slot(x, plan.spec)
        for h in hotels
        for x in legs
        if transfer_id(h.offer_id, x.offer_id) in by_id
        and transfer_id(h.offer_id, x.offer_id) not in plan.selected
    }


def keep_refusal(plan: Plan, selected: list[str]) -> list[str]:
    """`selected` without the transfers of the slots the user refused in `plan` (#200)."""
    slots = refused(plan)
    by_id = {o.offer_id: o for o in plan.offers}
    legs = [by_id[i] for i in selected if i in by_id and by_id[i].kind == "transport"]
    hotels = [i for i in selected if i in by_id and by_id[i].kind == "hotel"]
    drop = {
        transfer_id(h, x.offer_id) for h in hotels for x in legs if _slot(x, plan.spec) in slots
    }
    return [i for i in selected if i not in drop]


def _with_transfers(plan: Plan, items: list[Offer], added: Offer) -> list[Offer]:
    """A transfer links one hotel and one leg: a new hotel or leg replaces the transfers of the
    old one with its own, so the cart and the timeline (TRAP-5) describe the same trip.

    A hotel or leg the user returns (still in `plan.selected`) brings back only the transfers
    that are still selected: the ones dropped with it. A transfer the user removed on its own
    left the selection and stays out; the user can add it back. A new hotel or leg brings no
    transfer of a slot the user refused (#200)."""
    if added.kind not in ("hotel", "transport"):
        return items
    by_id = {o.offer_id: o for o in plan.offers}
    no = refused(plan)
    items = _drop_orphan_transfers(items)
    hotels = [o for o in items if o.kind == "hotel"]
    legs = [o for o in items if o.kind == "transport"]
    new_pairs = [(h, x) for h in hotels for x in legs if added.offer_id in (h.offer_id, x.offer_id)]
    returned = added.offer_id in plan.selected
    have = {o.offer_id for o in items}
    for h, x in new_pairs:
        t = by_id.get(transfer_id(h.offer_id, x.offer_id))
        if (
            t is not None
            and t.offer_id not in have
            and (t.offer_id in plan.selected if returned else _slot(x, plan.spec) not in no)
        ):
            items.append(t)
    order = {o.offer_id: i for i, o in enumerate(plan.offers)}
    return sorted(items, key=lambda o: order.get(o.offer_id, len(order)))


def sync_plan(plan: Plan, old: Cart, new: Cart) -> Plan:
    """The plan after a cart edit: the selection follows the cart, the timeline is rebuilt.

    Without it a replaced hotel or flight stays in `plan.selected`, and the checker (TRAP-5)
    checks the old one. An added item replaces every selected item of its slot, with their
    add-ons, even one the user removed earlier. An add-on or a transfer the user removed leaves
    the selection; a new hotel or leg brings its transfers. A plain removal of a hotel or a leg
    keeps it selected, with its transfers: they leave the cart and the total, and come back with
    it. A transfer of a replaced hotel or leg leaves the selection with it.
    """
    by_id = {o.offer_id: o for o in plan.offers}
    before = {o.offer_id for o in old.items}
    after = {o.offer_id for o in new.items}
    added = [o for o in new.items if o.offer_id not in before]
    slots = {_slot(o, plan.spec) for o in added}
    pairs = _pair_ids(new.items)
    gone = {
        i
        for i in before - after
        if by_id.get(i)
        and (by_id[i].kind == "extra" or (by_id[i].kind == "transfer" and i in pairs))
    }
    for i in plan.selected:
        o = by_id.get(i)
        if o and o.kind != "extra" and _slot(o, plan.spec) in slots:
            gone.add(i)
    gone |= {
        o.offer_id
        for o in by_id.values()
        if o.kind == "extra" and o.for_offer_id in gone and o.offer_id not in after
    }
    kept = [i for i in plan.selected if i not in gone]
    selected = kept + [o.offer_id for o in added if o.offer_id not in kept]
    chosen = [by_id[i] for i in selected if i in by_id]
    live = _pair_ids(chosen)
    selected = [
        i for i in selected if by_id.get(i) is None or by_id[i].kind != "transfer" or i in live
    ]
    if selected == plan.selected:
        return plan
    plan = plan.model_copy(update={"selected": selected})
    plan.timeline, plan.issues = build_timeline(plan)
    return plan


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
        plan = sync_plan(reply.plan, reply.cart, cart)
        return reply.model_copy(update={"cart": cart, "plan": plan})

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
