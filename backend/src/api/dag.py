"""Re-plan DAG (T6, #10): a TripSpec diff recomputes only the plan nodes that depend on it.

Nodes, each one feeds the next:
  destination -> offers -> ranking -> timeline -> cart
- destination: spec.destinations[0]; nothing to compute, the root of the chain.
- offers: find_offers(spec, destination); the search depends on dates, guests, origin.
- ranking: rank(spec, offers); selected offers and why; budget, meal, refund, wishes.
- timeline: build_timeline(plan) reads only the selected offers. It is rebuilt when the
  selection changed and the old plan had a timeline (the stage machine builds it on "беру").
- cart: the cart confirms one TripSpec (the voucher keeps it), so any trip change empties it.
  The next confirmation refills it with fill_cart, which replaces the items, never appends.

A field changes -> its node and every node after it are dirty; clean nodes keep their values.
The timeline also stops early: the same selected offers keep the same timeline.
Plan.changes are "before -> after" lines from spec fields and tool data, not the LLM (TRAP-4).
"""

import re
from datetime import timedelta

from api.contracts import Cart, Change, Explanation, HotelOffer, Offer, Plan, TripSpec
from api.extract import MONTH_NAMES, NUM, NUMS
from api.fixtures import build_timeline, find_offers, rank
from api.optimizer import bundles

NODES = ("destination", "offers", "ranking", "timeline", "cart")
# The first node that reads the field. "assumptions" is text for the user, no node reads it.
FIELD_NODE = {
    "destinations": "destination",
    "origin": "offers",
    "date_from": "offers",
    "date_to": "offers",
    "month": "offers",
    "nights": "offers",
    "adults": "offers",
    "children_ages": "offers",
    "pets": "offers",
    "modes": "offers",
    "budget_rub": "ranking",
    "meal": "ranking",
    "refundable": "ranking",
    "max_travel_hours": "ranking",
    "wishes": "ranking",
    "priorities": "ranking",
}
# Key of a change group -> Change.what, the label the user sees.
LABELS = {
    "destinations": "направление",
    "origin": "откуда",
    "dates": "даты",
    "month": "месяц",
    "guests": "состав",
    "pets": "питомец",
    "modes": "транспорт",
    "budget_rub": "бюджет",
    "meal": "питание",
    "refundable": "отмена",
    "max_travel_hours": "время в пути",
    "wishes": "пожелания",
    "priorities": "что важнее",
    "hotel": "отель",
    "choice": "ваш выбор",
    "transport": "дорога",
    "total_rub": "итог",
    "issues": "стыковки",
    "cart": "корзина",
}
MEALS = {
    "RO": "без питания",
    "BB": "завтраки",
    "HB": "полупансион",
    "FB": "полный пансион",
    "AI": "всё включено",
}
MODES = {"flight": "самолёт", "train": "поезд", "car": "машина"}
PRIORITIES = {
    "price": "цена",
    "cancellation": "отмена",
    "meal": "питание",
    "location": "расположение",
    "travel_time": "время в пути",
    "rating": "рейтинг",
}


# «на два дня позже», «на неделю раньше»: a shift of the current dates, not a duration.
# model.step drops the nights the extractor reads from «неделю» when this matches.
SHIFT_RE = re.compile(rf"\bна\s+(?:{NUM}\s*)?(дн\w*|день|недел\w*)\s+(позже|раньше)")
MANUAL_WHY = "вы выбрали этот отель сами"


def shift_dates(spec: TripSpec, text: str) -> TripSpec:
    """The spec with its dates moved by «на N дней позже/раньше»; unchanged without the phrase.

    The extractor sees one message, not the spec, so the shift is applied here (S2).
    """
    m = SHIFT_RE.search(text.lower().replace("ё", "е"))
    if not m or not (spec.date_from and spec.date_to):
        return spec
    n = int(m.group(1)) if m.group(1) and m.group(1).isdigit() else NUMS.get(m.group(1), 1)
    days = n * (7 if m.group(2).startswith("нед") else 1) * (1 if m.group(3) == "позже" else -1)
    start, end = spec.date_from + timedelta(days=days), spec.date_to + timedelta(days=days)
    kept = [a for a in spec.assumptions if not a.startswith("даты:")]  # dates are stated now
    return spec.model_copy(
        update={"date_from": start, "date_to": end, "month": start.month, "assumptions": kept}
    )


def dirty_nodes(old: TripSpec, new: TripSpec) -> set[str]:
    """Nodes to recompute after the spec edit: the first node of each changed field onward."""
    changed = [node for f, node in FIELD_NODE.items() if getattr(old, f) != getattr(new, f)]
    if not changed:
        return set()
    return set(NODES[min(NODES.index(n) for n in changed) :])


def _chosen(plan: Plan) -> list[Offer]:
    return [o for o in plan.offers if o.offer_id in plan.selected]


def _hotel_of(plan: Plan) -> HotelOffer | None:
    return next((o for o in _chosen(plan) if isinstance(o, HotelOffer)), None)


def _same_hotel(h: HotelOffer, offers: list[Offer]) -> HotelOffer | None:
    """The same hotel among new offers: the same offer, else the same title for other dates."""
    hotels = [o for o in offers if isinstance(o, HotelOffer)]
    return next((o for o in hotels if o.offer_id == h.offer_id), None) or next(
        (o for o in hotels if (o.title, o.city) == (h.title, h.city)), None
    )


def _keep_pick(old: Plan, plan: Plan) -> Change | None:
    """Keeps the hotel the user picked over the ranking («беру первый»).

    The pick is manual when the old ranking chose another hotel. It is dropped only when the
    hotel is gone, the edit changed a hotel requirement it fails, or a lowered budget fits the
    ranked set but not the pick; the Change says why.
    """
    picked, ranked = _hotel_of(old), _hotel_of(plan)
    if not picked or not ranked or ranked.title == picked.title:
        return None
    if picked.offer_id in rank(old.spec, old.offers)[0]:
        return None  # the ranking chose it, not the user
    same, spec = _same_hotel(picked, plan.offers), plan.spec
    budget = spec.budget_rub if spec.budget_rub != old.spec.budget_rub else None
    if not same:
        why = "его нет в новых предложениях"
    elif spec.meal != old.spec.meal and spec.meal and same.meal != spec.meal:
        why = f"в нём {MEALS[same.meal]}, а нужно {MEALS[spec.meal]}"
    elif (
        spec.refundable != old.spec.refundable
        and spec.refundable is not None
        and same.cancellation.refundable != spec.refundable
    ):
        why = f"в нём {_refundable(same.cancellation.refundable)}"
    elif budget is not None and (
        _total(plan) <= budget < _total(plan) - ranked.price_rub + same.price_rub
    ):  # the ranked set fits the new budget, the set with the pick does not
        why = "дороже бюджета"
    else:
        ids = {o.offer_id for o in plan.offers if isinstance(o, HotelOffer)}
        plan.selected = [same.offer_id] + [i for i in plan.selected if i not in ids]
        plan.explanations = [Explanation(offer_id=same.offer_id, why=MANUAL_WHY)] + [
            e for e in plan.explanations if e.offer_id not in ids
        ]
        return None
    return Change(what=LABELS["choice"], before=picked.title, after=f"{ranked.title}: {why}")


def replan(old: Plan, spec: TripSpec, cart: Cart) -> tuple[Plan, Cart, list[str]]:
    """New plan and cart for the edited spec, and the nodes recomputed, in order.

    A plan without offers has nothing to keep: all nodes run, no changes are listed.
    """
    dirty = dirty_nodes(old.spec, spec) if old.offers else set(NODES)
    plan = old.model_copy(update={"spec": spec, "changes": []})
    done: list[str] = []
    lost: Change | None = None
    if "destination" in dirty:
        done.append("destination")
    if "offers" in dirty:
        plan.offers = find_offers(spec, spec.destinations[0])
        done.append("offers")
    if "ranking" in dirty:
        plan.selected, plan.explanations = rank(spec, plan.offers)
        plan.bundles = bundles(spec, plan.offers)  # whole-trip options (T7, #13)
        lost = _keep_pick(old, plan) if old.offers else None
        done.append("ranking")
    # Invariant: build_timeline reads only the selected offers, not the spec; T8 (#14) must
    # revisit this shortcut if the checker starts reading spec fields.
    if _chosen(plan) != _chosen(old):
        if old.timeline:
            plan.timeline, plan.issues = build_timeline(plan)
            done.append("timeline")
        else:
            plan.timeline, plan.issues = [], []
    new_cart = cart
    if "cart" in dirty and cart.items:
        new_cart = Cart()
        done.append("cart")
    if old.offers:
        plan.changes = changes(old, plan, cart, new_cart)
        if lost:
            plan.changes.append(lost)
    return plan, new_cart, done


def _rub(n: int) -> str:
    return f"{n:,} ₽".replace(",", " ")


def _dates(s: TripSpec) -> str:
    if not (s.date_from and s.date_to):
        return "не заданы"
    return (
        f"{s.date_from:%d.%m}–{s.date_to:%d.%m}, {s.nights or (s.date_to - s.date_from).days} ноч."
    )


def _guests(s: TripSpec) -> str:
    kids = f" + дети {', '.join(map(str, s.children_ages))} лет" if s.children_ages else ""
    return f"{s.adults} взр.{kids}"


def _refundable(v: bool | None) -> str:
    return {True: "бесплатная отмена", False: "невозвратный тариф", None: "не важно"}[v]


SPEC_VIEWS = {  # LABELS key -> TripSpec as text
    "destinations": lambda s: ", ".join(s.destinations) or "не выбрано",
    "origin": lambda s: s.origin or "не задано",
    "dates": _dates,
    "month": lambda s: MONTH_NAMES[s.month - 1] if s.month else "не задан",
    "guests": _guests,
    "pets": lambda s: "с питомцем" if s.pets else "без питомца",
    "modes": lambda s: ", ".join(MODES[m] for m in s.modes) or "любой",
    "budget_rub": lambda s: _rub(s.budget_rub) if s.budget_rub is not None else "не задан",
    "meal": lambda s: MEALS[s.meal] if s.meal else "не важно",
    "refundable": lambda s: _refundable(s.refundable),
    "max_travel_hours": lambda s: (
        f"до {s.max_travel_hours:g} ч" if s.max_travel_hours else "не ограничено"
    ),
    "wishes": lambda s: ", ".join(s.wishes) or "нет",
    "priorities": lambda s: ", ".join(PRIORITIES[p] for p in s.priorities) or "по умолчанию",
}


def _hotel(plan: Plan) -> str:
    h = next((o for o in _chosen(plan) if o.kind == "hotel"), None)
    if not h:
        return "не выбран"
    return (
        f"{h.title}, {MEALS[h.meal]}, {_refundable(h.cancellation.refundable)}, {_rub(h.price_rub)}"
    )


def _legs(plan: Plan) -> list[str]:
    return [f"{o.title}, {_rub(o.price_rub)}" for o in _chosen(plan) if o.kind == "transport"]


def _transport(old: Plan, new: Plan) -> tuple[str, str]:
    """Only the legs that differ, so a kept flight is not repeated."""
    a, b = _legs(old), _legs(new)
    gone = "; ".join(x for x in a if x not in b) or "нет"
    added = "; ".join(x for x in b if x not in a) or "нет"
    return gone, added


def _issues(plan: Plan) -> str:
    return "; ".join(i.text for i in plan.issues) or "замечаний нет"


def _cart(cart: Cart) -> str:
    if not cart.items:
        return "пусто — подтвердите новый план"
    return f"{len(cart.items)} поз. на {_rub(cart.total_rub)}"


def changes(old: Plan, new: Plan, old_cart: Cart, new_cart: Cart) -> list[Change]:
    """Before -> after lines: changed spec groups first, then changed results."""
    views = dict(SPEC_VIEWS)
    if _dates(old.spec) != _dates(new.spec):
        del views["month"]  # the dates already show the new month
    pairs = [(what, view(old.spec), view(new.spec)) for what, view in views.items()]
    pairs += [
        ("hotel", _hotel(old), _hotel(new)),
        ("transport", *_transport(old, new)),
        ("total_rub", _rub(_total(old)), _rub(_total(new))),
    ]
    if old.timeline:
        pairs.append(("issues", _issues(old), _issues(new)))
    if old_cart.items:
        pairs.append(("cart", _cart(old_cart), _cart(new_cart)))
    return [Change(what=LABELS[w], before=a, after=b) for w, a, b in pairs if a != b]


def _total(plan: Plan) -> int:
    return sum(o.price_rub for o in _chosen(plan))


def describe(changes: list[Change]) -> list[str]:
    """Reply lines for the user: "label: before → after"."""
    return [f"{c.what}: {c.before} → {c.after}" for c in changes]
