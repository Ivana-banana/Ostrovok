"""Stand-ins for agent modules that are not in main yet (accepted on fixtures).

Each function is one seam: the owning task replaces it and keeps the signature.
All prices are synthetic (TRAP-7). Climate of the month and travel hours come from the
catalogue (api/catalog.py, #8) and the transport formula of api/offers.py.
"""

from datetime import date

from api import offers as ports  # TRANSPORT is read at call time: a test may swap it
from api.contracts import Offer, TripSpec
from api.offers import find_offers  # noqa: F401  find_offers: api/dag.py
from api.optimizer import rank  # noqa: F401  T7 (#13): the seam is the optimizer now
from api.timeline import build_timeline  # noqa: F401  # T8 (#14) lives in api/timeline.py

# Seven places the extractor knows by their stems (api/extract.py, api/answers.py).
# Inspiration ranks the whole catalogue in api/inspire.py (T12, #20). name -> stems, why, tradeoffs.
# fmt: off
_KEYS = ("stems", "why", "tradeoffs")
_ROWS = [
    ("Анталия", ("антал",), "тёплое море, семейные отели, прямой перелёт",
     ["в ноябре вода на границе комфорта"]),
    ("Хургада", ("хургад", "египет"), "самое тёплое море из коротких перелётов",
     ["перелёт почти 5 ч", "пляжи с кораллами — нужна обувь"]),
    ("Сочи", ("сочи",), "короткий перелёт без визы и обмена валюты",
     ["море холодное для купания"]),
    ("Дубай", ("дуба",), "жарко и тёплое море гарантированно", ["дорогие отели"]),
    ("Калининград", ("калининград",), "Балтика и старый город",
     ["холодно, море не для купания"]),
    ("Казань", ("казан",), "город на выходные, близко", ["моря нет"]),
    ("Санкт-Петербург", ("петербург", "питер", "спб"), "музеи и прогулки",
     ["моря для купания нет"]),
]
# fmt: on
CATALOG: dict[str, dict] = {row[0]: dict(zip(_KEYS, row[1:], strict=True)) for row in _ROWS}

# Estimate, not from a source: below this the sea counts as cold for swimming.
# It sits between catalogue values on purpose (November sea: Antalya 23.6, Sochi 17.1).
WARM_WATER_C = 21.5
# #114: the cold half of the year, where «тепло» in the wishes filters the places by the air.
COLD_MONTHS = (10, 11, 12, 1, 2, 3, 4)
IN_MONTH = ("в январе", "в феврале", "в марте", "в апреле", "в мае", "в июне", "в июле",
            "в августе", "в сентябре", "в октябре", "в ноябре", "в декабре")  # fmt: skip


def future_date(month: int, day: int, today: date | None = None) -> date:
    """The next date with this month and day, not in the past."""
    today = today or date.today()
    d = date(today.year, month, day)
    return d if d >= today else date(today.year + 1, month, day)


def _min_price(offers: list[Offer], pred) -> int:
    return min((o.price_rub for o in offers if pred(o)), default=0)


def trip_cost(spec: TripSpec, offers: list[Offer]) -> int:
    """Cheapest hotel plus the cheapest flight each way, from the offers of one place."""
    return (
        _min_price(offers, lambda o: o.kind == "hotel")
        + _min_price(offers, lambda o: o.kind == "transport" and o.origin == spec.origin)
        + _min_price(offers, lambda o: o.kind == "transport" and o.destination == spec.origin)
    )


def travel_hours(spec: TripSpec, origin: dict, place: dict) -> float:
    """The fastest way there by the transport port (offers.TRANSPORT), hours; 0 at the origin."""
    probe = (
        spec
        if spec.date_from
        else spec.model_copy(update={"date_from": future_date(spec.month or 1, 15)})
    )
    legs = [x for x in ports.TRANSPORT.legs(probe, origin, place) if x.origin == origin["name"]]
    return min(
        (round((x.arrive_at - x.depart_at).total_seconds() / 3600, 1) for x in legs), default=0
    )
