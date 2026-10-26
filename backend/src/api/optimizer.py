"""Optimizer (T7, #13): whole-trip bundles, hard filters, user weights, "what was sacrificed".

- Bundle: one hotel tariff x a leg there x a leg back x the transfers of those legs to that
  hotel (transfer_id). Every tariff of a hotel is its own HotelOffer, so several tariffs of
  one hotel compete as separate bundles. A car goes both ways or not at all; a car has no
  transfers. total_rub is the sum of price_rub of the bundle offers: all guests, all nights.
- Hard filters from TripSpec: meal (the tariff meal includes the asked one: AI covers BB),
  refundable=True, modes, max_travel_hours (each leg), pets (hotel and every leg), children
  (family or kids_club), «тяжело ходить» (wheelchair or elevator), car (parking), budget.
- Fallback when nothing passes: the bundles with the fewest broken requirements other than
  the budget, then the smallest budget overrun. The budget gives way first: a hotel without
  the dog is no option, an overrun is a number the user sees. Every broken rule is a risk.
- Score: weights from TripSpec.priorities (order -> weights, DEFAULT_WEIGHTS when empty,
  «недорого» means price first) over min-max scores of the candidates. Ties: lower total,
  shorter trip, then offer ids, so the result does not depend on the offer order.
- Bundles: the best one and up to 3 alternatives of another kind (cheapest, cheapest hotel,
  fastest, most comfortable, within the budget). The selection is the best bundle without
  its transfers; the hotel explanation names them as outside the cart.

Texts use only offer fields, spec values and sums or differences of offer prices (TRAP-4).
"""

import hashlib
from dataclasses import dataclass, field
from typing import NamedTuple

from api.contracts import (
    Bundle,
    Explanation,
    HotelOffer,
    Offer,
    Priority,
    TransferOffer,
    TransportOffer,
    TripSpec,
)
from api.offers import transfer_id

CRITERIA: tuple[Priority, ...] = (
    "price",
    "cancellation",
    "meal",
    "location",
    "travel_time",
    "rating",
)
# Team estimate without a source: price matters most when the user said nothing.
DEFAULT_WEIGHTS: dict[Priority, float] = {
    "price": 0.35,
    "rating": 0.2,
    "travel_time": 0.15,
    "cancellation": 0.1,
    "meal": 0.1,
    "location": 0.1,
}
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
    over: int = 0  # budget overrun, roubles
    score: float = 0.0

    @property
    def legs(self) -> list[TransportOffer]:
        return [x for x in (self.there, self.back) if x]

    @property
    def ids(self) -> list[str]:
        """Hotel, legs, transfers: the order of the offers in find_offers."""
        return [self.hotel.offer_id, *(x.offer_id for x in (*self.legs, *self.transfers))]

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


def _rub(n: int) -> str:
    return f"{n:,} ₽".replace(",", " ")


def _h(x: float) -> str:
    return f"{round(x, 1):g} ч"


def _leg_hours(leg: TransportOffer) -> float:
    return round((leg.arrive_at - leg.depart_at).total_seconds() / 3600, 1)


def _diff(d: int) -> str:
    return f"дороже на {_rub(d)}" if d > 0 else f"дешевле на {_rub(-d)}" if d else "по той же цене"


def weights(spec: TripSpec) -> dict[Priority, float]:
    """Order -> weights: the i-th priority weighs 1/(i+1), the rest keep half the default."""
    order = list(dict.fromkeys(spec.priorities))
    if not order and "недорого" in spec.wishes:
        order = ["price"]
    if not order:
        return dict(DEFAULT_WEIGHTS)
    w = {c: DEFAULT_WEIGHTS[c] / 2 for c in CRITERIA}
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
        out.append(f"{h.title}: животных не принимают")
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


def _pool(spec: TripSpec, trips: list[Trip]) -> tuple[list[Trip], bool]:
    """Trips to choose from and whether they break nothing; sorted best first."""
    for t in trips:
        t.rules = broken_rules(spec, t)
        t.over = max(0, t.total - spec.budget_rub) if spec.budget_rub is not None else 0
    fewest = min(len(t.rules) for t in trips)
    pool = [t for t in trips if len(t.rules) == fewest]
    if any(t.over == 0 for t in pool):
        pool = [t for t in pool if t.over == 0]
    _score(pool, weights(spec))
    if any(t.over for t in pool):
        pool.sort(key=lambda t: (t.over, *t.key))  # nothing fits the budget: smallest overrun
    else:
        pool.sort(key=lambda t: t.key)
    return pool, fewest == 0 and not pool[0].over


def _alternatives(best: Trip, pool: list[Trip], trips: list[Trip]) -> list[tuple[str, Trip]]:
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
    if best.over and in_budget:
        kinds.append(("в бюджете", in_budget[0], True))
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
        out.append(_diff(t.total - best.total) + " за всю поездку")
    if (h.rating or 0) < (b.rating or 0):
        out.append(f"рейтинг {h.rating} вместо {b.rating}")
    if b.cancellation.refundable and not h.cancellation.refundable:
        out.append("невозвратный тариф")
    if MEAL_LEVEL[h.meal] < MEAL_LEVEL[b.meal]:
        out.append(f"питание: {MEAL_TEXT[h.meal]} вместо {MEAL_TEXT[b.meal]}")
    if t.hours > best.hours:
        out.append(f"дорога {_h(t.hours)} вместо {_h(best.hours)}")
    if (h.distance_center_km or 0) > (b.distance_center_km or 0):
        out.append(f"до центра {h.distance_center_km:g} км вместо {b.distance_center_km:g} км")
    if t.transfer_rub > best.transfer_rub:
        line = f"трансферы {_rub(t.transfer_rub)} вместо {_rub(best.transfer_rub)}"
        if h.price_rub < b.price_rub and t.total >= best.total:
            line += f": съедают экономию {_rub(b.price_rub - h.price_rub)} на отеле"
        out.append(line)
    return out + [f"нарушает: {r}" for r in t.rules]


def _hotel_why(spec: TripSpec, t: Trip) -> str:
    h = t.hotel
    facts = [f"{h.stars}★"] if h.stars else []
    if h.rating is not None:
        facts.append(f"рейтинг {h.rating}")
    facts.append(MEAL_TEXT[h.meal])
    if h.cancellation.refundable and h.cancellation.free_until:
        facts.append(f"бесплатная отмена до {h.cancellation.free_until:%d.%m}")
    else:
        facts.append("бесплатная отмена" if h.cancellation.refundable else "невозвратный тариф")
    if h.distance_center_km is not None:
        facts.append(f"{h.distance_center_km:g} км от центра")
    parts = [f"отель {_rub(h.price_rub)}"]
    if t.legs:
        parts.append(f"дорога {_rub(t.road)}")
    if t.transfers:
        parts.append(f"трансферы {_rub(t.transfer_rub)} — отдельно от корзины")
    why = f"{', '.join(facts)}; поездка целиком {_rub(t.total)} ({' + '.join(parts)})"
    order = list(dict.fromkeys(spec.priorities))
    if order:
        why += "; ваш порядок важности: " + " → ".join(PRIORITY_TEXT[p] for p in order)
    elif "недорого" in spec.wishes:
        why += "; важнее всего цена («недорого»)"
    return why


def _sacrificed(t: Trip, pool: list[Trip]) -> list[str]:
    """What the best trip gives up against the other candidates, from offer fields."""
    h, out = t.hotel, []
    cheapest = min(pool, key=lambda x: (x.total, x.key))
    if cheapest.total < t.total:
        out.append(
            f"дороже самого дешёвого варианта на {_rub(t.total - cheapest.total)} "
            f"({cheapest.hotel.title}, {_rub(cheapest.total)})"
        )
    if not h.cancellation.refundable:
        refund = [x for x in pool if x.hotel.cancellation.refundable]
        if refund:
            r = min(refund, key=lambda x: (x.total, x.key))
            out.append(
                f"невозвратный тариф; с бесплатной отменой — {r.hotel.title}, "
                f"{_diff(r.total - t.total)}"
            )
    top = min(pool, key=lambda x: (-(x.hotel.rating or 0), x.total, x.key))
    if (top.hotel.rating or 0) > (h.rating or 0):
        out.append(f"рейтинг ниже, чем у варианта {top.hotel.title} ({top.hotel.rating})")
    fast = min(pool, key=lambda x: (x.hours, x.total, x.key))
    if fast.hours < t.hours:
        out.append(
            f"дорога {_h(t.hours)}, есть быстрее: {_h(fast.hours)}, {_diff(fast.total - t.total)}"
        )
    return out


def _risks(spec: TripSpec, t: Trip, fits: bool, trips: list[Trip]) -> list[str]:
    out = []
    if not fits:
        out.append("ни один вариант не подходит под все условия — выбран ближайший")
    out += t.rules
    if t.over and spec.budget_rub is not None:
        line = (
            f"не укладывается в бюджет {_rub(spec.budget_rub)}: поездка целиком "
            f"{_rub(t.total)}, на {_rub(t.over)} больше"
        )
        if t.hotel.price_rub <= spec.budget_rub < t.total:
            line += f"; отель {_rub(t.hotel.price_rub)} укладывается, сверху дорога и трансферы"
        out.append(line)
        cheap = [x for x in trips if x.over == 0]
        if cheap:
            c = min(cheap, key=lambda x: (len(x.rules), x.key))
            broken = "; ".join(c.rules) or "—"
            out.append(f"в бюджет укладывается {c.hotel.title} за {_rub(c.total)}, но: {broken}")
        else:
            least = min(trips, key=lambda x: (x.total, x.key))
            out.append(f"самая дешёвая поездка из найденных — {_rub(least.total)}")
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
            f"дороже самого дешёвого варианта {way} на {_rub(leg.price_rub - cheapest.price_rub)} "
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


def optimize(spec: TripSpec, offers: list[Offer]) -> Ranked:
    """Selected offers of the best trip, why for each of them, and the bundles to compare."""
    trips = enumerate_trips(offers)
    if not trips:
        return Ranked([], [], [])
    pool, fits = _pool(spec, trips)
    best = pool[0]
    sacrificed = _sacrificed(best, pool)
    explanations = [
        Explanation(
            offer_id=best.hotel.offer_id,
            why=_hotel_why(spec, best),
            tradeoffs=sacrificed,
            risks=_risks(spec, best, fits, trips),
        )
    ]
    explanations += [_leg_why(best, leg, offers) for leg in best.legs]
    label = "лучший по вашим приоритетам" if fits else "ближе всего к условиям"
    bundles = [_bundle(label, best, sacrificed + best.rules)]
    bundles += [_bundle(lb, t, _versus(t, best)) for lb, t in _alternatives(best, pool, trips)]
    # TODO(#13): transfers stay in the bundle, not in the selection: a hotel picked by number
    # (model.py) replaces only the hotel id, and a selected transfer would lead to the old
    # hotel. The selection takes them when the pick swaps the transfers with the hotel.
    selected = [best.hotel.offer_id, *(x.offer_id for x in best.legs)]
    return Ranked(selected, explanations, bundles)


def rank(spec: TripSpec, offers: list[Offer]) -> tuple[list[str], list[Explanation]]:
    """Selected offer ids (hotel, leg there, leg back) of the best trip and why."""
    r = optimize(spec, offers)
    return r.selected, r.explanations


def bundles(spec: TripSpec, offers: list[Offer]) -> list[Bundle]:
    """Whole-trip options for Plan.bundles: the best one first."""
    return optimize(spec, offers).bundles
