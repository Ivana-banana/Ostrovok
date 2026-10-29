"""Optimizer (T7, #13): whole-trip bundles, hard filters, user weights, "what was sacrificed".

- Bundle: one hotel tariff x a leg there x a leg back x the transfers of those legs to that
  hotel (transfer_id: the key of the pair, offers_for_place sets it from the fields
  TransferOffer.hotel_offer_id and leg_offer_id, #307). Every tariff of a hotel is its own HotelOffer, so several tariffs of
  one hotel compete as separate bundles. A car goes both ways or not at all; a car has no
  transfers. total_rub is the sum of price_rub of the bundle offers: all guests, all nights.
- Hard filters from TripSpec: meal (the tariff meal includes the asked one: AI covers BB),
  refundable=True, modes, max_travel_hours (each leg), pets (hotel and every leg), children
  (family or kids_club), «тяжело ходить» (wheelchair or elevator), car (parking), budget.
- Fallback when nothing passes: the bundles with the fewest broken requirements other than
  the budget, then the smallest budget overrun. The budget gives way first. Every broken rule
  is a risk. Pets have no fallback (#131): a trip with a hotel or a leg that refuses the pet is
  never selected. If every trip refuses it, optimize selects the legs that take the pet and no
  hotel, or nothing if no leg takes it; dead_end_text says why.
- Score: weights from TripSpec.priorities (order -> weights, DEFAULT_WEIGHTS when empty,
  «недорого» means price first) over min-max scores of the candidates. Ties: lower total,
  shorter trip, then offer ids, so the result does not depend on the offer order.
- Bundles: the best one and up to 3 alternatives of another kind (cheapest, cheapest hotel,
  fastest, most comfortable, within the budget). The selection is the best bundle; a hotel
  the user picks replaces the hotel and its transfers (pick_hotel).

Texts use only offer fields, spec values and sums or differences of offer prices (TRAP-4).
"""

import hashlib
from dataclasses import dataclass, field
from typing import NamedTuple

from api.contracts import (
    Bundle,
    Explanation,
    ExtraOffer,
    HotelOffer,
    Offer,
    Priority,
    TransferOffer,
    TransportOffer,
    TripSpec,
)
from api.offers import OWN_BACK, OWN_THERE, transfer_id

CRITERIA: tuple[Priority, ...] = (
    "price",
    "cancellation",
    "meal",
    "location",
    "travel_time",
    "rating",
)
# Team estimate without a source: the user said nothing, so the cheapest whole trip goes first
# (price dominates, the rest only break near-ties). A priority the user names beats price (S9).
DEFAULT_WEIGHTS: dict[Priority, float] = {
    "price": 0.85,
    "rating": 0.04,
    "travel_time": 0.03,
    "cancellation": 0.03,
    "meal": 0.03,
    "location": 0.02,
}
# Team estimate without a source (D-045): with no named priority, a tariff with free cancellation
# whose hotel costs at most this share more than the cheapest hotel of the same legs is the default.
REFUNDABLE_NEAR = 0.15
MEAL_LEVEL = {"RO": 0, "BB": 1, "HB": 2, "FB": 3, "AI": 4}
MEAL_TEXT = {
    "RO": "без питания",
    "BB": "завтраки",
    "HB": "полупансион",
    "FB": "полный пансион",
    "AI": "всё включено",
}
MODE_TEXT = {"flight": "самолёт", "train": "поезд", "car": "машина"}
PRIORITY_TEXT = {
    "price": "цена",
    "cancellation": "отмена",
    "meal": "питание",
    "location": "расположение",
    "travel_time": "время в пути",
    "rating": "рейтинг",
}
MAX_ALTERNATIVES = 3
# Labels of the best bundle: every requirement met / the nearest trip. dag.py finds the ranking by them.
BEST_LABELS = ("лучший по вашим приоритетам", "ближе всего к условиям")
# Team estimate without a source (D-009): a leg longer than one night on the road does not go
# first while a trip without such a leg exists.
LONG_LEG_H = 13.0
# #343: «есть быстрее» only for a real saving: at least FASTER_H and FASTER_SHARE of the road
# (team estimate, D-028). 2.6 h against 2.5 h is no reason to pay more.
FASTER_H, FASTER_SHARE = 1.0, 0.15


class Ranked(NamedTuple):
    selected: list[str]
    explanations: list[Explanation]
    bundles: list[Bundle]


@dataclass
class Trip:
    hotel: HotelOffer
    there: TransportOffer | None
    back: TransportOffer | None
    transfers: list[TransferOffer]
    rules: list[str] = field(default_factory=list)  # broken requirements except the budget
    over: int = 0  # overrun of the trip budget and of the hotel budget together, roubles
    hotel_over: int = 0  # the part of `over` that is the hotel above the hotel budget
    score: float = 0.0
    near_refund: "Trip | None" = (
        None  # the cheaper non-refundable trip that D-045 moved this one past
    )

    @property
    def legs(self) -> list[TransportOffer]:
        return [x for x in (self.there, self.back) if x]

    @property
    def ids(self) -> list[str]:
        """Hotel, legs, transfers: the order of the offers in find_offers."""
        return [self.hotel.offer_id, *(x.offer_id for x in (*self.legs, *self.transfers))]

    @property
    def offers(self) -> list[Offer]:
        return [self.hotel, *self.legs, *self.transfers]

    @property
    def total(self) -> int:
        return self.hotel.price_rub + self.road + self.transfer_rub

    @property
    def road(self) -> int:
        return sum(x.price_rub for x in self.legs)

    @property
    def transfer_rub(self) -> int:
        return sum(t.price_rub for t in self.transfers)

    @property
    def slow_leg(self) -> TransportOffer | None:
        """The longest leg when it is longer than LONG_LEG_H; it is the reason for `slow`."""
        longest = max(self.legs, key=_leg_hours, default=None)
        return longest if longest and _leg_hours(longest) > LONG_LEG_H else None

    @property
    def slow(self) -> bool:
        return self.slow_leg is not None

    @property
    def hours(self) -> float:
        """Door to door one way: the leg there and the transfer after it."""
        if not self.there:
            return 0.0
        end = self.there.arrive_at
        for t in self.transfers:
            if t.offer_id == _tid(self.hotel, self.there):
                end = t.arrive_at
        return round((end - self.there.depart_at).total_seconds() / 3600, 1)

    @property
    def comfort(self) -> tuple[float, int, int, bool]:
        h = self.hotel
        return (h.rating or 0, h.stars or 0, MEAL_LEVEL[h.meal], h.cancellation.refundable)

    @property
    def comfort_key(self) -> tuple:
        """min() of it gives the most comfortable trip, then the cheaper one."""
        return (*(-x for x in self.comfort), self.total, self.key)

    @property
    def key(self) -> tuple:
        return (-round(self.score, 9), self.total, self.hours, tuple(self.ids))


def _tid(hotel: HotelOffer, leg: TransportOffer) -> str:
    return transfer_id(hotel.offer_id, leg.offer_id)


# TRAP-7 (#567): the source of a real price by kind and mode (web/src/Board.tsx PRICE_SOURCES).
PRICE_SOURCES = {"hotel": "101hotels", "flight": "Авиасейлс", "train": "tutu"}


def price_mark(offers) -> str:
    """The label of a sum of these offers: «синтетика» when all are synthetic or there are none,
    the real sources when none is, «часть цен — синтетика» for a mix (TRAP-7)."""
    if all(o.synthetic for o in offers):
        return "синтетика"
    if any(o.synthetic for o in offers):
        return "часть цен — синтетика"
    names = (PRICE_SOURCES.get(getattr(o, "mode", o.kind), "реальные данные") for o in offers)
    return " + ".join(dict.fromkeys(names))


def _rub(n: int) -> str:
    return f"{n:,} ₽".replace(",", " ")


def _m(n: int, offers) -> str:
    """A sum of the offers with its own label: a real hotel price is not «синтетика» (#567)."""
    return f"{_rub(n)} ({price_mark(offers)})"


def _h(x: float) -> str:
    return f"{round(x, 1):g} ч"


def _leg_hours(leg: TransportOffer) -> float:
    return round((leg.arrive_at - leg.depart_at).total_seconds() / 3600, 1)


def _diff(d: int, offers) -> str:
    return (
        f"дороже на {_m(d, offers)}"
        if d > 0
        else f"дешевле на {_m(-d, offers)}"
        if d
        else "по той же цене"
    )


def weights(spec: TripSpec) -> dict[Priority, float]:
    """Order -> weights: the i-th priority weighs 1/(i+1), the rest keep half the default.

    «недорого» in wishes without priorities gives the price weights; it is not a named priority.
    """
    order = list(dict.fromkeys(spec.priorities))
    if not order and "недорого" in spec.wishes:
        order = ["price"]
    if not order:
        return dict(DEFAULT_WEIGHTS)
    w: dict[Priority, float] = {c: DEFAULT_WEIGHTS[c] / 2 for c in CRITERIA}
    for i, p in enumerate(order):
        w[p] = 1 / (i + 1)
    s = sum(w.values())
    return {c: v / s for c, v in w.items()}


def enumerate_trips(offers: list[Offer]) -> list[Trip]:
    """Every hotel tariff x leg there x leg back, with the transfers of these legs."""
    hotels = sorted((o for o in offers if isinstance(o, HotelOffer)), key=lambda o: o.offer_id)
    legs = sorted((o for o in offers if isinstance(o, TransportOffer)), key=lambda o: o.offer_id)
    transfers = {o.offer_id: o for o in offers if isinstance(o, TransferOffer)}
    trips = []
    for h in hotels:
        there = [x for x in legs if x.destination == h.city] or [None]
        back = [x for x in legs if x.origin == h.city] or [None]
        for a in there:
            for b in back:
                if a and b and (a.mode == "car") != (b.mode == "car"):
                    continue  # the car goes both ways
                ts = [
                    transfers[_tid(h, x)]
                    for x in (a, b)
                    if x and x.mode != "car" and _tid(h, x) in transfers
                ]
                # Own tickets (C5, #408): the transfers of the own legs, offers.own_legs.
                own = [OWN_THERE] * (a is None) + [OWN_BACK] * (b is None)
                ts += [transfers[i] for x in own if (i := transfer_id(h.offer_id, x)) in transfers]
                trips.append(Trip(h, a, b, ts))
    return trips


def broken_rules(spec: TripSpec, t: Trip) -> list[str]:
    """Requirements of the spec this trip breaks, except the budget; the user sees the text."""
    h, out = t.hotel, []
    if spec.meal and MEAL_LEVEL[h.meal] < MEAL_LEVEL[spec.meal]:
        out.append(f"питание: {MEAL_TEXT[h.meal]}, а нужно {MEAL_TEXT[spec.meal]}")
    if spec.refundable and not h.cancellation.refundable:
        out.append("невозвратный тариф, а нужна бесплатная отмена")
    for leg in t.legs:
        if spec.modes and leg.mode not in spec.modes:
            asked = ", ".join(MODE_TEXT[m] for m in spec.modes)
            out.append(f"{leg.title}: {MODE_TEXT[leg.mode]}, а нужно: {asked}")
        if spec.max_travel_hours and _leg_hours(leg) > spec.max_travel_hours:
            out.append(
                f"{leg.title}: в пути {_h(_leg_hours(leg))} — дольше {_h(spec.max_travel_hours)}"
            )
        if spec.pets and not leg.pets_allowed:
            out.append(f"{leg.title}: животных не берут")
    if spec.pets and not h.pets_allowed:
        out.append(f"{h.title}: нет отметки о приёме животных в данных отеля")
    if spec.children_ages and not {"family", "kids_club"} & set(h.amenities):
        out.append(f"{h.title}: нет отметки «для семей с детьми» в данных отеля")
    if "тяжело ходить" in spec.wishes and not {"wheelchair", "elevator"} & set(h.amenities):
        out.append(f"{h.title}: нет отметки о лифте или доступной среде в данных отеля")
    if any(leg.mode == "car" for leg in t.legs) and "parking" not in h.amenities:
        out.append(f"{h.title}: нет отметки о парковке в данных отеля")
    return list(dict.fromkeys(out))


def _minmax(values: list[float | None], higher_better: bool) -> list[float]:
    known = [v for v in values if v is not None]
    if not known:
        return [0.0] * len(values)
    lo, hi = min(known), max(known)
    out = []
    for v in values:
        if v is None:
            out.append(0.0)
        elif hi == lo:
            out.append(1.0)
        else:
            s = (v - lo) / (hi - lo)
            out.append(s if higher_better else 1 - s)
    return out


def _score(trips: list[Trip], w: dict[Priority, float]) -> None:
    columns = {
        "price": _minmax([t.total for t in trips], False),
        "cancellation": _minmax([float(t.hotel.cancellation.refundable) for t in trips], True),
        "meal": _minmax([MEAL_LEVEL[t.hotel.meal] for t in trips], True),
        "location": _minmax([t.hotel.distance_center_km for t in trips], False),
        "travel_time": _minmax([t.hours for t in trips], False),
        "rating": _minmax([t.hotel.rating for t in trips], True),
    }
    for i, t in enumerate(trips):
        t.score = sum(w[c] * columns[c][i] for c in CRITERIA)


def pets_ok(t: Trip) -> bool:
    """The hotel and every leg of the trip take pets."""
    return t.hotel.pets_allowed and all(leg.pets_allowed for leg in t.legs)


def _prefer_refundable(spec: TripSpec, pool: list[Trip]) -> None:
    """Near-equal hotel price: the default is a free-cancellation tariff, not the cheapest (D-045).

    Moves in place the first refundable trip of the same legs to the front and notes the trip it
    passed in `near_refund`. A user who named priorities has chosen.
    """
    best = pool[0]
    if spec.priorities or best.hotel.cancellation.refundable:
        return
    for i, t in enumerate(pool):
        if (
            t.hotel.cancellation.refundable
            and (t.over, t.slow) == (best.over, best.slow)
            and (t.there, t.back) == (best.there, best.back)
            and t.hotel.price_rub <= best.hotel.price_rub * (1 + REFUNDABLE_NEAR)
        ):
            t.near_refund = best
            pool.insert(0, pool.pop(i))
            return


def _pool(spec: TripSpec, trips: list[Trip]) -> tuple[list[Trip], bool]:
    """Trips to choose from and whether they break nothing; sorted best first.

    With a pet only the trips that take it are candidates (#131). When none does, the pool is
    the old fewest-rules one, for the callers that must rank something; optimize() selects
    nothing then.
    """
    for t in trips:
        t.rules = broken_rules(spec, t)
        trip_over = max(0, t.total - spec.budget_rub) if spec.budget_rub is not None else 0
        hotel = spec.hotel_budget_rub
        t.hotel_over = max(0, t.hotel.price_rub - hotel) if hotel is not None else 0
        t.over = trip_over + t.hotel_over
    candidates = [t for t in trips if pets_ok(t)] if spec.pets else []
    candidates = candidates or trips
    fewest = min(len(t.rules) for t in candidates)
    pool = [t for t in candidates if len(t.rules) == fewest]
    if any(t.over == 0 for t in pool):
        pool = [t for t in pool if t.over == 0]
    _score(pool, weights(spec))
    # The price weight must not send the user on a long train when a faster trip exists; only a
    # user who named the price first has chosen. «недорого» in wishes does not lift it (D-009).
    demote = spec.priorities[:1] != ["price"] and any(not t.slow for t in pool)
    if any(t.over for t in pool):
        # nothing fits the budget: smallest overrun
        pool.sort(key=lambda t: (t.over, demote and t.slow, *t.key))
    else:
        pool.sort(key=lambda t: (demote and t.slow, *t.key))
    _prefer_refundable(spec, pool)
    return pool, fewest == 0 and not pool[0].over


def _alternatives(
    spec: TripSpec, best: Trip, pool: list[Trip], trips: list[Trip]
) -> list[tuple[str, Trip]]:
    """(label, trip) of another kind than the best one; each trip once."""
    by_price = sorted(pool, key=lambda t: (t.total, t.key))
    kinds = [
        ("самый дешёвый", by_price[0], by_price[0].total < best.total),
        (
            "самый дешёвый отель",
            min(pool, key=lambda t: (t.hotel.price_rub, t.total, t.key)),
            None,
        ),
        ("самый быстрый", min(pool, key=lambda t: (t.hours, t.total, t.key)), None),
        ("самый комфортный", min(pool, key=lambda t: t.comfort_key), None),
    ]
    in_budget = sorted((t for t in trips if t.over == 0), key=lambda t: (len(t.rules), t.key))
    in_budget = [t for t in in_budget if pets_ok(t)] if spec.pets else in_budget
    if best.over and in_budget:  # first: MAX_ALTERNATIVES must not push it out
        kinds.insert(0, ("в бюджете", in_budget[0], True))
    out: list[tuple[str, Trip]] = []
    seen = {tuple(best.ids)}
    for label, t, ok in kinds:
        if ok is None:
            ok = {
                "самый дешёвый отель": t.hotel.price_rub < best.hotel.price_rub,
                "самый быстрый": t.hours < best.hours,
                "самый комфортный": t.comfort > best.comfort,
            }[label]
        if ok and tuple(t.ids) not in seen and len(out) < MAX_ALTERNATIVES:
            out.append((label, t))
            seen.add(tuple(t.ids))
    return out


def _versus(t: Trip, best: Trip) -> list[str]:
    """What this alternative gives up against the best trip, from offer fields."""
    h, b = t.hotel, best.hotel
    out = []
    if t.total > best.total:
        out.append(_diff(t.total - best.total, t.offers + best.offers) + " за всю поездку")
    if h.rating is not None and b.rating is not None and h.rating < b.rating:
        out.append(f"рейтинг {h.rating} вместо {b.rating}")
    if b.cancellation.refundable and not h.cancellation.refundable:
        out.append("невозвратный тариф")
    if MEAL_LEVEL[h.meal] < MEAL_LEVEL[b.meal]:
        out.append(f"питание: {MEAL_TEXT[h.meal]} вместо {MEAL_TEXT[b.meal]}")
    if t.hours > best.hours:
        out.append(f"дорога {_h(t.hours)} вместо {_h(best.hours)}")
    hd, bd = h.distance_center_km, b.distance_center_km
    if hd is not None and bd is not None and hd > bd:
        out.append(f"до центра {hd:g} км вместо {bd:g} км")
    if t.transfer_rub > best.transfer_rub:
        tr = t.transfers + best.transfers
        line = f"трансферы {_m(t.transfer_rub, tr)} вместо {_m(best.transfer_rub, tr)}"
        if h.price_rub < b.price_rub and t.total >= best.total:
            line += f": съедают экономию {_m(b.price_rub - h.price_rub, [h, b])} на отеле"
        out.append(line)
    return out + [f"нарушает: {r}" for r in t.rules]


def _hotel_why(spec: TripSpec, t: Trip, pool: list[Trip]) -> str:
    h = t.hotel
    facts = [f"{h.stars}★"] if h.stars else []
    if h.rating is not None:
        facts.append(f"рейтинг {h.rating}")
    facts.append(MEAL_TEXT[h.meal])
    if spec.pets and h.pets_allowed:
        facts.append("принимает животных")
    if h.cancellation.refundable and h.cancellation.free_until:
        facts.append(f"бесплатная отмена до {h.cancellation.free_until:%d.%m}")
    else:
        facts.append("бесплатная отмена" if h.cancellation.refundable else "невозвратный тариф")
    if h.distance_center_km is not None:
        facts.append(f"{h.distance_center_km:g} км от центра")
    parts = [f"отель {_m(h.price_rub, [h])}"]
    if t.legs:
        parts.append(f"дорога {_m(t.road, t.legs)}")
    if t.transfers:
        parts.append(f"трансферы {_m(t.transfer_rub, t.transfers)}")
    whole = "поездка целиком" if t.legs else "проживание без дороги"
    why = f"{', '.join(facts)}; {whole} {_m(t.total, t.offers)} ({' + '.join(parts)})"
    if spec.hotel_budget_rub is not None and not t.hotel_over:
        why += f"; отель в бюджете отеля {_rub(spec.hotel_budget_rub)}"
    order = list(dict.fromkeys(spec.priorities))
    if t.near_refund is not None and pool[0] is t:
        cheaper = t.near_refund
        diff = t.hotel.price_rub - cheaper.hotel.price_rub
        pct = diff / cheaper.hotel.price_rub if cheaper.hotel.price_rub else 0
        why += (
            f"; выбран тариф с бесплатной отменой: отель дороже самого дешёвого невозвратного "
            f"на {_m(diff, [*t.offers, *cheaper.offers])} ({pct:.0%}), "
            f"разница в пределах {REFUNDABLE_NEAR:.0%} цены отеля"
        )
    if order:
        why += "; ваш порядок важности: " + " → ".join(PRIORITY_TEXT[p] for p in order)
    elif "недорого" in spec.wishes:
        cheapest = min(pool, key=lambda x: (x.total, x.key))
        if cheapest.total >= t.total:
            why += "; важнее всего цена («недорого»)"
        else:
            why += "; цена важна («недорого»), самый дешёвый вариант ниже"
            if cheapest.slow_leg:  # hours only when the road is the reason
                why += f": дорога {_h(_leg_hours(cheapest.slow_leg))}"
    return why


def _less(a: float | None, b: float | None) -> bool:
    """a < b when both are known; an absent value neither wins nor loses."""
    return a is not None and b is not None and a < b


def _wins_on(p: Priority, t: Trip, other: Trip) -> bool:
    """Whether trip t is better than the other trip on the criterion p, from offer fields."""
    a, b = t.hotel, other.hotel
    return {
        "price": t.total < other.total,
        "cancellation": a.cancellation.refundable and not b.cancellation.refundable,
        "meal": MEAL_LEVEL[a.meal] > MEAL_LEVEL[b.meal],
        "location": _less(a.distance_center_km, b.distance_center_km),
        "travel_time": t.hours < other.hours,
        "rating": _less(b.rating, a.rating),
    }[p]


def _sacrificed(spec: TripSpec, t: Trip, pool: list[Trip]) -> list[str]:
    """What the best trip gives up against the other candidates, from offer fields."""
    h, out = t.hotel, []
    cheapest = min(pool, key=lambda x: (x.total, x.key))
    if cheapest.total < t.total:
        line = (
            f"дороже самого дешёвого варианта на {_m(t.total - cheapest.total, t.offers + cheapest.offers)} "
            f"({cheapest.hotel.title}, {_m(cheapest.total, cheapest.offers)})"
        )
        first = spec.priorities[0] if spec.priorities else None
        if first and first != "price" and _wins_on(first, t, cheapest):
            line += f" — плата за приоритет «{PRIORITY_TEXT[first]}»"  # the user put it above price
        if cheapest.slow_leg and not t.slow:
            line += (
                f"; у самого дешёвого дорога в одну сторону {_h(_leg_hours(cheapest.slow_leg))} "
                f"({cheapest.slow_leg.title})"
            )
        out.append(line)
    if not h.cancellation.refundable:
        refund = [x for x in pool if x.hotel.cancellation.refundable]
        if refund:
            r = min(refund, key=lambda x: (x.total, x.key))
            out.append(
                f"невозвратный тариф; с бесплатной отменой — {r.hotel.title}, "
                f"{_diff(r.total - t.total, r.offers + t.offers)}"
            )
    top = min(pool, key=lambda x: (-(x.hotel.rating or 0), x.total, x.key))
    if _less(h.rating, top.hotel.rating):
        out.append(f"рейтинг ниже, чем у варианта {top.hotel.title} ({top.hotel.rating})")
    fast = min(pool, key=lambda x: (x.hours, x.total, x.key))
    saved = t.hours - fast.hours
    if saved >= FASTER_H and saved >= FASTER_SHARE * t.hours:
        out.append(
            f"дорога {_h(t.hours)}, есть быстрее: {_h(fast.hours)}, {_diff(fast.total - t.total, fast.offers + t.offers)}"
        )
    return out


def _pets_skipped(spec: TripSpec, trips: list[Trip]) -> list[str]:
    """Hotels and legs left out because they do not take the pet, from offer fields."""
    if not spec.pets:
        return []
    hotels = sorted({t.hotel.title for t in trips if not t.hotel.pets_allowed})
    legs = {x.title: x for t in trips for x in t.legs if not x.pets_allowed}
    out = []
    if hotels:
        out.append(
            f"с животным не выбраны отели без отметки о приёме животных: {'; '.join(hotels)}"
        )
    if legs:
        modes = sorted({MODE_TEXT[x.mode] for x in legs.values()})
        out.append(f"с животным не выбраны рейсы, где животных не берут ({', '.join(modes)})")
    return out


def pets_possible(spec: TripSpec, offers: list[Offer]) -> bool:
    """Whether some trip takes the pet; true without a pet or without trips."""
    trips = enumerate_trips(offers)
    return not spec.pets or not trips or any(pets_ok(t) for t in trips)


def dead_end_text(spec: TripSpec, offers: list[Offer]) -> str:
    """Why no trip takes the pet and what the user can do; empty when a trip takes it."""
    if pets_possible(spec, offers):
        return ""
    trips = enumerate_trips(offers)
    place = spec.destinations[0]
    if any(all(x.pets_allowed for x in t.legs) for t in trips):
        n = len({t.hotel.title for t in trips})
        return (
            f"Направление «{place}»: с питомцем не нашёл отель, который точно принимает животных. "
            f"Ни у одного из {n} отелей в данных нет отметки о приёме животных. Это не отказ, а условие, "
            "которое не подтверждено. Варианты: выбрать отель из списка по номеру, предварительно "
            "уточнив у отеля, что он принимает животных; или другой город, "
            "где поездка с питомцем возможна."
        )
    _pool(spec, trips)
    closest = min(trips, key=lambda t: (len(t.rules), t.key))
    why = "; ".join(r for r in closest.rules if "животн" in r)
    return (
        f"Направление «{place}»: с питомцем не получается собрать поездку: {why}. "
        "Выберите другое направление."
    )


def _risks(spec: TripSpec, t: Trip, fits: bool, trips: list[Trip]) -> list[str]:
    out = []
    if not fits:
        out.append("ни один вариант не подходит под все условия — выбран ближайший")
    out += t.rules
    if not t.legs and not spec.own_transport:  # own tickets (#408): the user knows the way
        out.append("как добраться — нет данных")
    if t.hotel_over and spec.hotel_budget_rub is not None:
        out.append(
            f"отель не укладывается в бюджет отеля {_rub(spec.hotel_budget_rub)}: "
            f"{_m(t.hotel.price_rub, [t.hotel])}, на {_m(t.hotel_over, [t.hotel])} больше"
        )
    trip_over = t.over - t.hotel_over
    if trip_over and spec.budget_rub is not None:
        line = (
            f"не укладывается в бюджет {_rub(spec.budget_rub)}: "
            f"{'поездка целиком' if t.legs else 'проживание без дороги'} {_m(t.total, t.offers)}, на {_m(trip_over, t.offers)} больше"
        )
        if t.hotel.price_rub <= spec.budget_rub < t.total:
            line += f"; отель {_m(t.hotel.price_rub, [t.hotel])} укладывается, сверху дорога и трансферы"
        out.append(line)
    if t.over:
        cheap = [x for x in trips if x.over == 0]
        if cheap:
            c = min(cheap, key=lambda x: (len(x.rules), x.key))
            broken = "; ".join(c.rules) or "—"
            out.append(
                f"в бюджет укладывается {c.hotel.title} за {_m(c.total, c.offers)}, но: {broken}"
            )
        else:
            least = min(trips, key=lambda x: (x.total, x.key))
            out.append(f"самая дешёвая поездка из найденных — {_m(least.total, least.offers)}")
    return out


def _leg_why(t: Trip, leg: TransportOffer, offers: list[Offer]) -> Explanation:
    same = [
        o
        for o in offers
        if isinstance(o, TransportOffer)
        and (o.origin, o.destination) == (leg.origin, leg.destination)
    ]
    way = "туда" if leg is t.there else "обратно"
    facts = [f"{way}: {leg.depart_at:%d.%m %H:%M} → {leg.arrive_at:%d.%m %H:%M}"]
    facts.append(f"в пути {_h(_leg_hours(leg))}")
    if leg.changes:
        facts.append(f"пересадок: {leg.changes}")
    cheapest = min(same, key=lambda o: (o.price_rub, o.offer_id))
    fastest = min(same, key=lambda o: (_leg_hours(o), o.price_rub, o.offer_id))
    if leg.price_rub == cheapest.price_rub:
        facts.append(f"самый дешёвый из вариантов {way}")
    if _leg_hours(leg) == _leg_hours(fastest):
        facts.append(f"самый быстрый из вариантов {way}")
    tradeoffs = []
    if leg.price_rub > cheapest.price_rub:
        tradeoffs.append(
            f"дороже самого дешёвого варианта {way} на {_m(leg.price_rub - cheapest.price_rub, [leg, cheapest])} "
            f"({cheapest.title})"
        )
    return Explanation(offer_id=leg.offer_id, why=", ".join(facts), tradeoffs=tradeoffs)


def _bundle(label: str, t: Trip, tradeoffs: list[str]) -> Bundle:
    ids = t.ids
    digest = hashlib.sha1("|".join(ids).encode()).hexdigest()[:10]
    return Bundle(
        bundle_id=f"b-{digest}",
        label=label,
        offer_ids=ids,
        total_rub=t.total,
        travel_hours=t.hours,
        tradeoffs=tradeoffs,
    )


def _explanations(
    spec: TripSpec, t: Trip, offers: list[Offer], trips: list[Trip]
) -> list[Explanation]:
    """Why of trip t against the pool of the spec: the hotel first, then each leg."""
    pool, fits = _pool(spec, trips)
    hotel = Explanation(
        offer_id=t.hotel.offer_id,
        why=_hotel_why(spec, t, pool),
        tradeoffs=_sacrificed(spec, t, pool) + _pets_skipped(spec, trips),
        risks=_risks(spec, t, fits, trips),
    )
    return [hotel, *(_leg_why(t, leg, offers) for leg in t.legs)]


def _selected_trip(trips: list[Trip], selected: list[str]) -> Trip | None:
    chosen = set(selected)
    return next((t for t in trips if set(t.ids) <= chosen), None)


def explain(spec: TripSpec, offers: list[Offer], selected: list[str]) -> list[Explanation]:
    """Why of the selected trip (not of the best one), from the facts of the offers.

    Use it after the selection changed (another leg or hotel): the old text names the totals
    of the old selection. Empty when the selection is not a whole trip of the offers.
    """
    trips = enumerate_trips(offers)
    trip = _selected_trip(trips, selected)
    return _explanations(spec, trip, offers, trips) if trip else []


def optimize(spec: TripSpec, offers: list[Offer]) -> Ranked:
    """Selected offers of the best trip, why for each of them, and the bundles to compare."""
    trips = enumerate_trips(offers)
    if not trips:
        return Ranked([], [], [])
    pool, fits = _pool(spec, trips)
    best = pool[0]
    if spec.pets and not pets_ok(best):
        # Nothing takes the pet (#131). The legs that take it may stay; no hotel is chosen for
        # the user: he picks one himself after he checks it, or goes elsewhere.
        ok = [t for t in trips if all(x.pets_allowed for x in t.legs)]
        if not ok:
            return Ranked([], [], [])
        t = min(ok, key=lambda t: (t.road, t.hours, tuple(x.offer_id for x in t.legs)))
        return Ranked([x.offer_id for x in t.legs], [_leg_why(t, x, offers) for x in t.legs], [])
    explanations = _explanations(spec, best, offers, trips)
    sacrificed = explanations[0].tradeoffs
    label = BEST_LABELS[0] if fits else BEST_LABELS[1]
    bundles = [_bundle(label, best, sacrificed + best.rules)]
    bundles += [
        _bundle(lb, t, _versus(t, best)) for lb, t in _alternatives(spec, best, pool, trips)
    ]
    return Ranked(best.ids, explanations, bundles)


def rank(spec: TripSpec, offers: list[Offer]) -> tuple[list[str], list[Explanation]]:
    """Selected offer ids (hotel, leg there, leg back, their transfers) and why."""
    r = optimize(spec, offers)
    return r.selected, r.explanations


def bundles(spec: TripSpec, offers: list[Offer], selected: list[str] | None = None) -> list[Bundle]:
    """Whole-trip options for Plan.bundles. The first one is always the best trip of the spec
    (labelled BEST_LABELS): dag.py finds it by the label, to tell the ranking from a
    manual pick.

    With `selected` that is another trip (the user switched a leg or picked a hotel), the
    bundle of the selected trip goes second as «ваш выбор»: it matches `explain`.
    """
    r = optimize(spec, offers)
    trips = enumerate_trips(offers)
    mine = _selected_trip(trips, selected or [])
    if not r.bundles or mine is None or mine.ids == r.selected:  # no bundles: pets (#131)
        return r.bundles
    pool, _ = _pool(spec, trips)
    old = next((b for b in r.bundles if b.offer_ids == mine.ids), None)
    label = f"ваш выбор ({old.label})" if old else "ваш выбор"
    own = _bundle(label, mine, _versus(mine, pool[0]))
    return [r.bundles[0], own, *(b for b in r.bundles[1:] if b.offer_ids != mine.ids)]


def pick_hotel(selected: list[str], offers: list[Offer], hotel_id: str) -> list[str]:
    """The selection with another hotel: its transfers for the selected legs replace the old
    hotel, its transfers and its add-ons; the legs stay. Order: hotel, legs, transfers."""
    by_id = {o.offer_id: o for o in offers}
    hotel = by_id[hotel_id]
    if not isinstance(hotel, HotelOffer):
        raise TypeError(f"{hotel_id} is not a hotel offer")
    chosen = [by_id[i] for i in selected if i in by_id]
    legs = [o for o in chosen if isinstance(o, TransportOffer)]
    transfers = [_tid(hotel, x) for x in legs if x.mode != "car" and _tid(hotel, x) in by_id]
    transfers += [i for x in (OWN_THERE, OWN_BACK) if (i := transfer_id(hotel_id, x)) in by_id]
    extras = [
        o.offer_id for o in chosen if isinstance(o, ExtraOffer) and o.for_offer_id == hotel_id
    ]
    return [hotel_id, *(x.offer_id for x in legs), *transfers, *extras]


def order_tariffs(spec: TripSpec, offers: list[Offer]) -> list[Offer]:
    """The offers with the tariffs of each hotel ordered best first for this spec.

    The best tariff of a hotel is the one in its best trip: trips of the pool first, in pool
    order, then the rest by broken rules, overrun and price. Hotels keep their order, the
    other offers stay where they are. So the first tariff of a hotel is its row in the
    comparison (hotel_rows), for the chat and the board alike.
    """
    trips = enumerate_trips(offers)
    if not trips:
        return offers
    pool, _ = _pool(spec, trips)
    chosen = {id(t) for t in pool}
    rest = sorted(
        (t for t in trips if id(t) not in chosen),
        key=lambda t: (len(t.rules), t.over, t.total, t.key),
    )
    place: dict[str, int] = {}
    for i, t in enumerate([*pool, *rest]):
        place.setdefault(t.hotel.offer_id, i)
    slots: dict[tuple[str, str], list[int]] = {}
    for i, o in enumerate(offers):
        if isinstance(o, HotelOffer):
            slots.setdefault((o.title, o.city), []).append(i)
    out = list(offers)
    for at in slots.values():
        group = sorted(
            (offers[i] for i in at), key=lambda h: (place.get(h.offer_id, len(place)), h.offer_id)
        )
        for i, h in zip(at, group, strict=True):
            out[i] = h
    return out


def hotel_rows(offers: list[Offer], selected: list[str]) -> list[HotelOffer]:
    """One row per hotel: its selected tariff, else its first (best) tariff (order_tariffs).

    The chat, «беру второй» and the board (web/src/Board.tsx) use the same rule.
    """
    rows: dict[tuple[str, str], HotelOffer] = {}
    for o in offers:
        if isinstance(o, HotelOffer):
            key = (o.title, o.city)
            if key not in rows or (o.offer_id in selected and rows[key].offer_id not in selected):
                rows[key] = o
    return list(rows.values())
