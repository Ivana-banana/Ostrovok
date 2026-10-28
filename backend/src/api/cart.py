"""Cart and simulated purchase (T9, #11): POST /api/cart and POST /api/checkout.

- One item per slot: one hotel, one outbound and one return transport. Adding an offer of
  an occupied slot replaces the old item; it never duplicates. Other offers are keyed by id.
- Only offers of the plan can enter the cart, and the price is taken from the plan, not from
  the client (TRAP-4). Every price is synthetic (TRAP-7).
- Checkout needs the total the user saw. A different total means the cart changed: 409.
- The purchase goes through the port `BookingProvider` (ADR-0002): `BOOKING.quote` re-checks the
  prices, then `BOOKING.book` books. `BOOKING` is the simulation now; a real provider replaces
  it with one assignment. A refusal of the provider is a 409 with its reason; the cart stays.
- A repeated payment returns the same booking: the core rule, not the adapter's.
- The voucher is stored in the session reply, so a reload shows it.
- The session is changed in one store transaction, so parallel requests lose no update.
"""

import hashlib
import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from api.contracts import (
    BookingProvider,
    BookingRefused,
    Cart,
    CartAction,
    ChatReply,
    CheckoutRequest,
    Guest,
    Offer,
    Plan,
    TripSpec,
    Voucher,
)
from api.offers import transfer_id
from api.store import SessionStore
from api.timeline import build_timeline
from api.webhook import send_booking

log = logging.getLogger(__name__)


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
    """Ids of the transfers whose hotel and leg are both in `items`. The id of a transfer is
    the key of its pair (`transfer_id`): offers.offers_for_place sets it from the fields
    hotel_offer_id and leg_offer_id (ADR-0002, #307)."""
    hotels = [o for o in items if o.kind == "hotel"]
    legs = [o for o in items if o.kind == "transport"]
    return {transfer_id(h.offer_id, x.offer_id) for h in hotels for x in legs}


def _drop_orphan_transfers(items: list[Offer]) -> list[Offer]:
    """Keep a transfer only while its hotel and its leg are both in the cart."""
    valid = _pair_ids(items)
    return [o for o in items if o.kind != "transfer" or o.offer_id in valid]


TRANSFER_NOTES = {
    "out": "трансфер туда: добираетесь сами",
    "back": "трансфер обратно: добираетесь сами",
}


def remember_refusal(plan: Plan, slots: set[str] | None = None) -> Plan:
    """Keep the preference when a car or a new offer set has no transfer pair (#211)."""
    slots = refused(plan) if slots is None else slots
    notes = [a for a in plan.spec.assumptions if a not in TRANSFER_NOTES.values()]
    notes += [note for slot, note in TRANSFER_NOTES.items() if slot in slots]
    return plan.model_copy(update={"spec": plan.spec.model_copy(update={"assumptions": notes})})


def refused(plan: Plan) -> set[str]:
    """Slots ("out", "back") whose transfer the user refused (#200): the selected hotel and leg
    have a transfer offer, and it is not selected. The plan selects the transfer of every pair
    (pick_hotel); only the user takes it out («без трансфера», the cart). Read that selection
    and the saved preference, which survives a car with no transfer offer (#211)."""
    by_id = {o.offer_id: o for o in plan.offers}
    chosen = [by_id[i] for i in plan.selected if i in by_id]
    hotels = [o for o in chosen if o.kind == "hotel"]
    legs = [o for o in chosen if o.kind == "transport"]
    return {slot for slot, note in TRANSFER_NOTES.items() if note in plan.spec.assumptions} | {
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
    no = refused(plan)
    hotels = [o for o in plan.offers if o.kind == "hotel"]
    legs = [o for o in plan.offers if o.kind == "transport"]
    for h in hotels:
        for x in legs:
            pair = transfer_id(h.offer_id, x.offer_id)
            if pair in gone:
                no.add(_slot(x, plan.spec))
            if pair in after - before:
                no.discard(_slot(x, plan.spec))
    plan = remember_refusal(plan, no)
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
        status="confirmed",
        simulated=True,
    )


class SimulatedBooking:
    """BookingProvider adapter: the simulated purchase. Prices do not change between the plan
    and the payment; every booking is confirmed at once and marked simulated. The same attempt
    key returns the same booking, as the port requires (kept in memory of the process)."""

    def __init__(self) -> None:
        self._by_key: dict[str, Voucher] = {}

    def quote(self, spec: TripSpec, cart: Cart) -> Cart:
        return cart

    def book(self, spec: TripSpec, cart: Cart, session_id: str) -> Voucher:
        if session_id not in self._by_key:
            self._by_key.setdefault(session_id, book(spec, cart))  # first of two parallel wins
        return self._by_key[session_id]

    def cancel(self, booking_id: str) -> None:
        for key, v in self._by_key.items():
            if v.booking_id == booking_id:
                self._by_key[key] = v.model_copy(update={"status": "cancelled"})


# The active booking adapter (ADR-0002). A real provider replaces it with one assignment.
BOOKING: BookingProvider = SimulatedBooking()


class CartRefusal(Exception):
    """The cart or the checkout cannot go on. `status` is the HTTP code, `text` is for the user."""

    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status = status
        self.text = text


def _active(reply: ChatReply) -> Voucher | None:
    """The booking of the session, unless it is cancelled: after a cancel the session books anew."""
    v = reply.voucher
    return v if v is not None and v.status != "cancelled" else None


def _guard_cart_open(reply: ChatReply) -> None:
    if _active(reply):
        raise CartRefusal(409, f"Бронь {reply.voucher.booking_id} уже оформлена.")
    if reply.stage != "checkout":
        raise CartRefusal(
            409, "Корзина откроется, когда вы согласитесь с планом: напишите «да» после плана."
        )


_PROVIDER_DOWN = "Система бронирования не ответила"
_KEEP = "Корзина сохранена: подтвердите ещё раз позже или измените план."
# Errors of the provider the core answers with a 409. An adapter wraps its own network errors
# into one of them; any other exception is a bug and goes up as a 500 with its traceback.
_PROVIDER_ERRORS = (OSError, TimeoutError)


class _Rejected(CartRefusal):
    """The provider returned a booking the core does not keep. `voucher` is that booking,
    marked cancelled: the attempt records it, so the next confirmation gets a new key."""

    def __init__(self, text: str, voucher: Voucher):
        super().__init__(409, text)
        self.voucher = voucher.model_copy(update={"status": "cancelled"})


def _items(cart_items: list[Offer]) -> list[tuple[str, int]]:
    return sorted((o.offer_id, o.price_rub) for o in cart_items)


def _quote(spec: TripSpec, cart: Cart) -> Cart:
    """Prices of now from the provider. Its total is the sum of its items; the items must be
    the items of the cart, so the provider cannot add or drop one."""
    try:
        quoted = BOOKING.quote(spec, cart)
    except BookingRefused as e:
        raise CartRefusal(409, f"Цены не подтверждены: {e.reason}. {_KEEP}") from e
    except _PROVIDER_ERRORS as e:
        log.warning("booking: quote failed: %r", e)
        raise CartRefusal(409, f"{_PROVIDER_DOWN}: цены не подтверждены. {_KEEP}") from e
    if sorted(o.offer_id for o in quoted.items) != sorted(o.offer_id for o in cart.items):
        raise CartRefusal(409, f"Система бронирования изменила состав корзины. {_KEEP}")
    return make_cart(quoted.items)


def _book(spec: TripSpec, cart: Cart, key: str) -> Voucher:
    """The booking of the quoted cart under the attempt key. A voucher with other items or
    another total than the quote is no booking of this cart: the core cancels it and refuses."""
    try:
        voucher = BOOKING.book(spec, cart, key)
    except BookingRefused as e:
        raise CartRefusal(409, f"Бронь не оформлена: {e.reason}. {_KEEP}") from e
    except _PROVIDER_ERRORS as e:
        log.warning("booking: book failed: %r", e)
        raise CartRefusal(409, f"{_PROVIDER_DOWN}: бронь не оформлена. {_KEEP}") from e
    if voucher.status == "cancelled":
        text = f"Бронь не оформлена: система бронирования её отменила. {_KEEP}"
        raise _Rejected(text, voucher)
    if not _same_booking(voucher, cart):
        log.warning("booking: voucher %s differs from the quote", voucher.booking_id)
        try:
            BOOKING.cancel(voucher.booking_id)
        except (BookingRefused, *_PROVIDER_ERRORS) as e:
            log.warning("booking: cancel of %s failed: %r", voucher.booking_id, e)
        text = (
            f"Бронь не оформлена: система бронирования вернула бронь на "
            f"{_rub(voucher.total_rub)} вместо {_rub(cart.total_rub)}. {_KEEP}"
        )
        raise _Rejected(text, voucher)
    return voucher


def _repeat(reply: ChatReply, confirm_total_rub: int) -> Voucher | None:
    """The booking of the session for a repeated payment: the core rule, not the adapter's."""
    if (v := _active(reply)) and v.total_rub != confirm_total_rub:
        raise CartRefusal(409, f"Бронь уже оформлена на {_rub(v.total_rub)}.")
    return v


def _priced(reply: ChatReply, confirm_total_rub: int) -> tuple[Cart, ChatReply | None]:
    """The cart quoted by the provider, ready to book; or, when the provider changed the
    prices, the reply to store instead: the cart with the new prices and the new total."""
    _guard_cart_open(reply)
    in_cart = {o.offer_id for o in reply.cart.items}
    cart = make_cart([o for o in reply.plan.offers if o.offer_id in in_cart])
    if not cart.items:
        raise CartRefusal(409, "Корзина пуста: оформлять нечего.")
    quoted = _quote(reply.plan.spec, cart)
    if quoted.total_rub == confirm_total_rub:
        return quoted, None
    text = (
        f"Итог корзины {_rub(quoted.total_rub)}, а вы подтверждаете "
        f"{_rub(confirm_total_rub)}. Проверьте сумму и подтвердите ещё раз."
    )
    if quoted.total_rub == cart.total_rub:
        raise CartRefusal(409, text)
    text = f"Система бронирования изменила цены. {text}"
    return quoted, reply.model_copy(update={"reply": text, "questions": [], "cart": quoted})


def _booked(reply: ChatReply, cart: Cart, voucher: Voucher) -> ChatReply:
    paid = "Оплата имитирована." if voucher.simulated else "Оплата принята."
    text = f"Бронь {voucher.booking_id} оформлена на {_rub(voucher.total_rub)}. {paid}"
    update = {"stage": "booked", "reply": text, "questions": [], "cart": cart, "voucher": voucher}
    return reply.model_copy(update=update)


def attempt_key(session_id: str, attempt: int, spec: TripSpec, cart: Cart) -> str:
    """Idempotency key of a booking attempt: `<session_id>:<hash>` for the first attempt, then
    `<session_id>:<hash>:<n>`. The hash covers what is bought: items with prices, the total,
    the dates and the party. So a new trip of the same session («начать заново») is a new key,
    and the same cart repeated is the same key. A new attempt of one cart starts only after a
    cancel or a rejected booking (BookingProvider)."""
    what = repr(
        (_items(cart.items), cart.total_rub, spec.date_from, spec.date_to)
        + (spec.adults, tuple(spec.children_ages), spec.pets)
    )
    h = hashlib.sha256(what.encode()).hexdigest()[:8]
    return f"{session_id}:{h}" if attempt <= 1 else f"{session_id}:{h}:{attempt}"


def _same_booking(v: Voucher, cart: Cart) -> bool:
    return v.total_rub == cart.total_rub and _items(v.items) == _items(cart.items)


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


def _attempt(store: SessionStore, reply: ChatReply, cart: Cart) -> tuple[int, Voucher | None]:
    """The attempt to book `cart` under, and its voucher if the provider already booked it.

    The last attempt without a voucher is reused: the provider may have booked it before the
    failure, and the same key returns that booking. Its voucher is reused only if it is a
    booking of this very cart (a repeat after a failed session write). An attempt whose
    voucher is cancelled, in the attempt or in the session, or is another trip of the session
    («начать заново»), gives way to a new attempt."""
    n, done = store.last_attempt(reply.session_id)
    if done is not None and not _same_booking(done, cart):
        n, done = n + 1, None
        store.start_attempt(reply.session_id, n)
        return n, done
    cancelled = (
        reply.voucher is not None
        and reply.voucher.status == "cancelled"
        and done is not None
        and reply.voucher.booking_id == done.booking_id
    )
    if n == 0 or (done is not None and (done.status == "cancelled" or cancelled)):
        n, done = n + 1, None
        store.start_attempt(reply.session_id, n)
    return n, done


def checkout_reply(
    store: SessionStore, session_id: str, confirm_total_rub: int, guest: Guest | None = None
) -> ChatReply:
    """The one booking path of a session: POST /api/checkout and the chat «подтверждаю» (#382).
    Returns the stored reply with the voucher; a refusal is a CartRefusal.

    The cart must be open and not empty. Prices come from the plan, never from the stored or
    the sent cart (TRAP-4); then the provider re-checks them (`BOOKING.quote`). The confirmed
    total must equal the quoted total. A repeated call returns the same booking; after a cancel
    the session books anew. A quote with other prices books nothing: the session keeps the
    stage and gets the cart with the new prices, so the next confirmation can book it.

    The provider calls (`quote`, `book`) run outside the session write lock: a slow provider
    does not block the other sessions. The attempt is saved before `book` and its voucher
    right after it, so a repeat after a failed session write returns that voucher and books
    nothing new.

    `guest` (#367) goes into the voucher of a new booking; the provider does not get it. A
    repeat returns the first booking with its first guest.

    A booking new to the session goes to BOOKING_WEBHOOK_URL (api/webhook.py, #435) after it
    is stored; a failed webhook is logged and the booking stays."""
    sid = session_id
    seen = _known(store.get(sid))
    if _repeat(seen, confirm_total_rub):
        return seen
    quoted, changed = _priced(seen, confirm_total_rub)

    if changed:

        def new_prices(prev: ChatReply | None) -> ChatReply:
            cur = _known(prev)
            if (cur.cart, cur.voucher, cur.stage) != (seen.cart, seen.voucher, seen.stage):
                raise CartRefusal(409, "Корзина изменилась. Проверьте её и подтвердите ещё раз.")
            return changed

        store.update(sid, new_prices)
        raise CartRefusal(409, changed.reply)
    n, done = _attempt(store, seen, quoted)
    try:
        key = attempt_key(sid, n, seen.plan.spec, quoted)
        voucher = done or _book(seen.plan.spec, quoted, key).model_copy(update={"guest": guest})
    except _Rejected as e:
        store.save_voucher(sid, n, e.voucher)  # closes attempt n: the next one has a new key
        raise
    voucher = store.save_voucher(sid, n, voucher)
    stored = []  # set by keep(): this call put the booking into the session

    def keep(prev: ChatReply | None) -> ChatReply:
        cur = _known(prev)
        if (v := _active(cur)) and v.booking_id == voucher.booking_id:
            return cur  # a parallel request stored this booking already
        out = _booked(cur, quoted, voucher)
        if cur.cart != seen.cart:  # edited during the payment: the booking is the confirmed cart
            note = " Корзину изменили во время оплаты: бронь оформлена на подтверждённую корзину."
            out = out.model_copy(update={"reply": out.reply + note})
        stored.append(True)
        return out

    out = store.update(sid, keep)
    if stored and out.voucher is not None:  # one webhook per booking of the session (#435)
        send_booking(out.voucher)
    return out


def checkout_route(req: CheckoutRequest, store: SessionStore) -> Voucher:
    """POST /api/checkout: the voucher of checkout_reply; a refusal is its HTTP code."""
    try:
        out = checkout_reply(store, req.session_id, req.confirm_total_rub, req.guest)
    except CartRefusal as e:
        raise _http(e) from e
    assert out.voucher is not None  # checkout_reply returns a booked session
    return out.voucher
