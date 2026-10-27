"""Re-plan DAG (T6, #10): a TripSpec diff recomputes only the plan nodes that depend on it.

Nodes, each one feeds the next:
  destination -> offers -> ranking -> timeline -> cart
- destination: spec.destinations[0]; nothing to compute, the root of the chain.
- offers: find_offers(spec, destination); the search depends on dates, guests, origin.
- ranking: rank(spec, offers); selected offers and why; budget, meal, refund, wishes.
- timeline: build_timeline(plan) reads plan.selected and plan.offers, never the spec. It is
  built for the first plan too and rebuilt when the selection changed.
- cart: the cart confirms one TripSpec (the voucher keeps it), so any trip change empties it.
  The next confirmation refills it with fill_cart, which replaces the items, never appends.

A field changes -> its node and every node after it are dirty; clean nodes keep their values.
The timeline also stops early: the same selected offers keep the same timeline.
Plan.changes are "before -> after" lines from spec fields and tool data, not the LLM (TRAP-4).
"""

import re
from calendar import monthrange
from datetime import date, timedelta

from api.contracts import Cart, Change, Explanation, HotelOffer, Offer, Plan, TripSpec
from api.extract import EDIT_VERB, MONTH_NAMES, NUM, NUMS
from api.fixtures import find_offers, rank
from api.optimizer import BEST_LABELS, bundles, order_tariffs, pick_hotel
from api.timeline import build_timeline

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
    "own_transport": "offers",
    "arrive_at": "offers",
    "leave_at": "offers",
    "budget_rub": "ranking",
    "hotel_budget_rub": "ranking",
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
    "tickets": "билеты",
    "budget_rub": "бюджет",
    "hotel_budget_rub": "бюджет отеля",
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


# «на два дня позже», «на пару недель раньше», «на месяц позже», «на полгода раньше», «на год
# вперёд»: a shift of the current dates, not a duration. model.step drops the nights the
# extractor reads from «неделю» when it matches. Months and years move on the calendar (#176).
LONG_UNIT = r"месяц\w*|пол\s?года|год(?:а|у|ик\w*)?(?!\w)"
SHIFT_RE = re.compile(
    rf"\bна\s+(?:(?P<n>{NUM}|пар[уы])\s+)?(?P<unit>дн\w*|день|сут\w*|недел\w*|ноч\w*|{LONG_UNIT})"
    r"\s+(?P<way>позже|раньше|вперед|назад)"
)
# «оплачу на месяц позже» (the user pays later) and «беру, на месяц вперёд спасибо» (a thanks
# after the phrase) are a consent with a remark, not a request to move the plan (#176). Any word
# that turns the sentence («оплачу, только сдвинь на месяц позже») or a stop makes a request.
PAYS_RE = re.compile(r"\b(?:оплач\w*)\b")
THANKS_RE = re.compile(r"\s*(?:большое\s+|вам\s+)?спасибо\b")
TURN_RE = re.compile(
    r"\b(?:но|только|если|а|лучше|хочу|хотим|можно|нет|не|давай\w*"
    r"|сдвин\w*|перенес\w*|перенос\w*|передвин\w*|отлож\w*|подвин\w*)\b|[.!?;]"
)


def _is_long(m: re.Match) -> bool:
    return bool(re.fullmatch(LONG_UNIT, m.group("unit")))


def shift_matches(t: str, planned: bool = True) -> list[re.Match]:
    """The shift phrases of the text t (lower case, «е» for «ё») that ask to move the dates.

    planned: the session has a plan. Without one «на полгода вперёд» is part of the first
    request, not a move of dates the user never saw. «Вперёд» and «назад» are no shift of
    the guessed dates either: «на неделю вперёд» is a length there. «Позже» and «раньше» move
    days and weeks as before.
    """
    found = list(SHIFT_RE.finditer(t))
    if not planned:
        found = [m for m in found if not _is_long(m) and m.group("way") in ("позже", "раньше")]
    return [m for m in found if not (_is_long(m) and _remark(t, m))]


def _remark(t: str, m: re.Match) -> bool:
    """True when the phrase m is a remark of a consent: after «оплачу» or before «спасибо»."""
    if THANKS_RE.match(t[m.end() :]):
        return True
    verbs = list(PAYS_RE.finditer(t[: m.start()]))
    return bool(verbs) and not TURN_RE.search(t[verbs[-1].end() : m.start()])


# A word that says which end of the trip moves; no word -> both ends move. Departure verbs
# («улетим», «уедем», «выезд из Москвы», «отъезд») mean leaving home, so they are no cue.
END_CUE = r"\b(?:обратн|верну|верне[мт]|верни|домой|выезд\s+из\s+отел|выселя|выселен|выписа|возврат|возвращ)"
START_CUE = r"\b(?:засел|заезд|заех|заезж|приезд|приед|приеха|прилет|вылет|начал|старт)"
# A request to move the dates that SHIFT_RE does not read: no amount, no direction or an odd word.
# «поездк…» names the date only with a move verb: «измени бюджет поездки» is no date edit.
_MOVE = rf"(?:{EDIT_VERB}\w*)"  # the verbs of extract.py: its amount is no length either
_WEAK_MOVE = r"(?:поменя\w*|измени\w*)"
_DATE_WORD = (
    r"(?:дат\w*|поездк\w*|вылет\w*|отпуск\w*|заезд\w*|выезд\w*|заселени\w*|возвращени\w*|обратн\w*)"
)
_WEAK_DATE_WORD = r"(?:дат\w*|заезд\w*|заселени\w*|возвращени\w*|возврат\w*)"
_UNIT = r"(?:дн\w*|день|сут\w*|недел\w*|ноч\w*)"
# «на полтора месяца позже»: an amount SHIFT_RE does not read
_LONG_ODD = rf"\bна\s+\w+\s+(?:{LONG_UNIT})\s+(?:позже|раньше|вперед|назад)"
_EDIT_START = START_CUE.replace("|вылет", "")  # a bare «вылет позже» may be a time
DATE_EDIT_RE = re.compile(
    rf"\b{_MOVE}\b[^.!?]*?\b(?:{_DATE_WORD}|на\s+\w+\s+{_UNIT})"
    rf"|\b{_WEAK_MOVE}\b[^.!?]*?\b(?:{_WEAK_DATE_WORD}|на\s+\w+\s+{_UNIT})"
    rf"|\bна\s+\w+\s+{_UNIT}\s+(?:позже|раньше)"
    rf"|{_LONG_ODD}"
    # «вылет позже 10 утра», «хочу вылет позже, утром тяжело» is a time of day, not a date
    rf"|(?:{_EDIT_START}|{END_CUE})\w*[^.!?,;]*\b(?:позже|раньше)\b(?!\s+(?:\d|чем|в\s+\d))"
)
MANUAL_WHY = "вы выбрали этот отель сами"
# What the user sees about a shift; the kinds are the ends that moved.
SHIFT_NOTE = {"both": "сдвинуты", "start": "заезд", "end": "возврат"}
SHIFT_NOTE_RE = re.compile(
    r"^даты: (сдвинуты|заезд|возврат) на (\d+) (дн\.|мес\.|год|года|лет) (позже|раньше)"
    r" по вашей просьбе$"
)


def _amount_text(unit: str, n: int) -> str:
    """«3 дн.», «1 мес.», «1 год», «2 года», «5 лет»: months in whole years read as years."""
    if unit == "дн.":
        return f"{n} дн."
    if n % 12:
        return f"{n} мес."
    y = n // 12
    return f"{y} " + (
        "год"
        if y % 10 == 1 and y % 100 != 11
        else "года"
        if y % 10 in (2, 3, 4) and y % 100 not in (12, 13, 14)
        else "лет"
    )


def is_shift_note(assumption: str) -> bool:
    return bool(SHIFT_NOTE_RE.match(assumption))


def _which_end(text: str, m: re.Match, lo: int, hi: int, places: list[str]) -> str:
    """ "start", "end" or "both" for the shift phrase m; lo..hi bounds the clause around it.

    places: the destinations of the trip; «вылет из Сочи» from there is the flight back.
    """
    before = re.split(r"[.,;!?]|\sа\s|\sи\s", text[lo : m.start()])[-1]
    after = re.split(r"[.,;!?]|\sа\s|\sи\s", text[m.end() : hi])[0]
    for part in (before, after):
        if re.search(END_CUE, part):  # «вылет обратно» is the return
            return "end"
        if f := re.search(r"вылет\w*\s+из\s+(\w+)", part):
            stems = [p.lower().replace("ё", "е")[:4] for p in places]
            if f.group(1)[:4] in stems:
                return "end"
        if re.search(START_CUE, part):
            return "start"
    return "both"


def _amount(word: str | None) -> int:
    """«два», «3», «пару» -> 2; no number -> 1 («на день», «на сутки»)."""
    if not word:
        return 1
    return int(word) if word.isdigit() else 2 if word.startswith("пар") else NUMS.get(word, 1)


def add_months(d: date, n: int) -> date:
    """The same day of the month n months on; the last day of the month if it has no such day."""
    year, month = divmod(d.month - 1 + n, 12)
    return date(d.year + year, month + 1, min(d.day, monthrange(d.year + year, month + 1)[1]))


def _months(m: re.Match) -> int:
    """The calendar months of a long phrase: 1 for «месяц», 6 for «полгода», 12 for «год»."""
    unit, n = m.group("unit"), _amount(m.group("n"))
    return 6 if unit.startswith("пол") else n * 12 if unit.startswith("год") else n


def shift_dates(spec: TripSpec, text: str, planned: bool = True) -> TripSpec:
    """The spec with its dates moved by «на N дней/месяцев позже/раньше»; unchanged without it.

    The extractor sees one message, not the spec, so the shift is applied here (S2). A cue word
    («обратно», «заселиться») moves one end only and changes the nights. The old «даты:»
    assumptions go; a note «сдвинуты на N дн./мес./год … по вашей просьбе» (totals per end) replaces them.
    Raises ValueError when less than one night would be left.
    """
    t = text.lower().replace("ё", "е")
    found = shift_matches(t, planned)
    if not found or not (spec.date_from and spec.date_to):
        return spec
    start, end = spec.date_from, spec.date_to
    total = {k: {"дн.": 0, "мес.": 0} for k in ("both", "start", "end")}  # days, months
    for a in spec.assumptions:  # the shifts of earlier turns count too
        if old := SHIFT_NOTE_RE.match(a):
            kind = next(k for k, w in SHIFT_NOTE.items() if w == old.group(1))
            unit = "дн." if old.group(3) == "дн." else "мес."
            n = int(old.group(2)) * (12 if old.group(3) in ("год", "года", "лет") else 1)
            total[kind][unit] += n * (1 if old.group(4) == "позже" else -1)
    for i, m in enumerate(found):
        sign = 1 if m.group("way") in ("позже", "вперед") else -1
        lo = found[i - 1].end() if i else 0
        hi = found[i + 1].start() if i + 1 < len(found) else len(t)
        kind = _which_end(t, m, lo, hi, spec.destinations)
        old_start, old_end = start, end
        if _is_long(m):
            total[kind]["мес."] += sign * _months(m)
            if kind == "end":
                end = add_months(end, sign * _months(m))
            else:  # both ends: the nights stay, the end follows the start
                start = add_months(start, sign * _months(m))
                end = start + (old_end - old_start) if kind == "both" else end
        else:
            days = _amount(m.group("n")) * (7 if m.group("unit").startswith("нед") else 1)
            total[kind]["дн."] += sign * days
            start += timedelta(days=sign * days) if kind != "end" else timedelta(0)
            end += timedelta(days=sign * days) if kind != "start" else timedelta(0)
    if (end - start).days < 1:
        raise ValueError("В поездке останется меньше одной ночи.")
    if start < date.today() and start < spec.date_from:
        # The year of a shift is known: a check-in in the past is refused, the plan stays (#176)
        raise ValueError(f"Заезд пришёлся бы на {start:%d.%m.%Y}: эта дата уже прошла.")
    kept = [a for a in spec.assumptions if not a.startswith("даты:")]  # dates are stated now
    for kind, units in total.items():
        for unit, n in units.items():
            if n:
                way = "позже" if n > 0 else "раньше"
                amount = _amount_text(unit, abs(n))
                kept.append(f"даты: {SHIFT_NOTE[kind]} на {amount} {way} по вашей просьбе")
    return spec.model_copy(
        update={
            "date_from": start,
            "date_to": end,
            "nights": (end - start).days,
            "month": start.month,
            "assumptions": kept,
        }
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
    """The same hotel among the new offers, with a tariff for the new spec.

    The offers are kept (the spec edit is about meal, refund or budget): the best tariff of
    the hotel for the new spec, the first one in the order of order_tariffs. The offers are
    new (other dates): the tariff with the same meal and cancellation, else the best one.
    """
    group = [
        o for o in offers if isinstance(o, HotelOffer) and (o.title, o.city) == (h.title, h.city)
    ]
    if not group or any(o.offer_id == h.offer_id for o in group):
        return group[0] if group else None
    same = (h.meal, h.cancellation.refundable)
    return next((o for o in group if (o.meal, o.cancellation.refundable) == same), group[0])


def _ranked_hotel(plan: Plan) -> HotelOffer | None:
    """The hotel of the best set the ranking showed, read from the stored bundles.

    The best bundle is found by its label, not by its place: a bundle of the user's own trip
    may stand before it. Without such a label the first bundle stands for the ranking.
    """
    best = next((b for b in plan.bundles if b.label.startswith(BEST_LABELS)), None)
    best = best or (plan.bundles[0] if plan.bundles else None)
    ids = set(best.offer_ids) if best else set()
    return next((o for o in plan.offers if isinstance(o, HotelOffer) and o.offer_id in ids), None)


def _keep_pick(old: Plan, plan: Plan) -> Change | None:
    """Keeps the hotel the user picked over the ranking («беру первый»).

    The pick is manual when the old plan selects another hotel than its best bundle, the
    ranking the user saw. The bundles are stored, so no second rank() of the old spec runs
    here. It is dropped only when the hotel is gone, the edit changed a hotel requirement it
    fails, or a lowered budget fits the ranked set but not the pick; the Change says why.
    """
    picked, ranked = _hotel_of(old), _hotel_of(plan)
    if not picked or not ranked or (ranked.title, ranked.city) == (picked.title, picked.city):
        return None
    shown = _ranked_hotel(old)
    if not shown or (shown.title, shown.city) == (picked.title, picked.city):
        return None  # the ranking chose it, not the user
    same, spec = _same_hotel(picked, plan.offers), plan.spec
    budget = spec.budget_rub if spec.budget_rub != old.spec.budget_rub else None
    hotel_budget = (
        spec.hotel_budget_rub if spec.hotel_budget_rub != old.spec.hotel_budget_rub else None
    )
    # The pick brings its own transfers for the selected legs (T7, #13).
    swapped = pick_hotel(plan.selected, plan.offers, same.offer_id) if same else []
    swapped_rub = sum(o.price_rub for o in plan.offers if o.offer_id in swapped)
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
    elif budget is not None and _total(plan) <= budget < swapped_rub:
        # the ranked set fits the new budget, the set with the pick does not
        why = "дороже бюджета"
    elif hotel_budget is not None and ranked.price_rub <= hotel_budget < same.price_rub:
        why = "дороже бюджета отеля"
    else:
        ids = {o.offer_id for o in plan.offers if isinstance(o, HotelOffer)}
        plan.selected = swapped
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
        plan.offers = order_tariffs(spec, plan.offers)  # the best tariff of a hotel goes first
        plan.selected, plan.explanations = rank(spec, plan.offers)
        plan.bundles = bundles(spec, plan.offers)  # whole-trip options (T7, #13)
        lost = _keep_pick(old, plan) if old.offers else None
        done.append("ranking")
    # Invariant: build_timeline reads plan.selected and plan.offers, not the spec (T8, #14).
    # The offer_id of a hotel or a leg carries dates and guests, so the same selected offers
    # mean the same timeline. If the checker starts reading spec fields, rebuild it here.
    # The timeline is built whenever the selection changed, also for the first plan (#66): the
    # compare stage shows it before the user says «беру».
    if _chosen(plan) != _chosen(old) or not old.offers:
        plan.timeline, plan.issues = build_timeline(plan)
        done.append("timeline")
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


def period(s: TripSpec) -> str:
    """«20.10–25.10»; with the year when a date is not in the current year (#159)."""
    fmt = "%d.%m" if {s.date_from.year, s.date_to.year} == {date.today().year} else "%d.%m.%Y"
    return f"{s.date_from.strftime(fmt)}–{s.date_to.strftime(fmt)}"


def _dates(s: TripSpec) -> str:
    if not (s.date_from and s.date_to):
        return "не заданы"
    return f"{period(s)}, {s.nights or (s.date_to - s.date_from).days} ноч."


def _guests(s: TripSpec) -> str:
    kids = f" + дети {', '.join(map(str, s.children_ages))} лет" if s.children_ages else ""
    return f"{s.adults} взр.{kids}"


def own_tickets(s: TripSpec) -> str:
    """«свои билеты, прилёт 14.11 01:30, вылет 18.11 15:00»; "" without own tickets (#408)."""
    if not s.own_transport:
        return ""
    times = [
        f"{w} {at:%d.%m %H:%M}" for w, at in (("прилёт", s.arrive_at), ("вылет", s.leave_at)) if at
    ]
    return ", ".join(["свои билеты", *times])


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
    "tickets": lambda s: own_tickets(s) or "подбираем",
    "budget_rub": lambda s: _rub(s.budget_rub) if s.budget_rub is not None else "не задан",
    "hotel_budget_rub": lambda s: (
        _rub(s.hotel_budget_rub) if s.hotel_budget_rub is not None else "не задан"
    ),
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
