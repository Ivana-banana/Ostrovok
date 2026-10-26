"""Stand-ins for agent modules that are not in main yet (accepted on fixtures).

Each function is one seam: the owning task replaces it and keeps the signature.
All prices are synthetic (TRAP-7). Climate of the month and travel hours come from the
catalogue (api/catalog.py, #8) and the transport formula of api/offers.py.
"""

from datetime import date

from api import catalog
from api.contracts import Destination, Offer, TripSpec
from api.offers import find_offers, transport_offers
from api.optimizer import rank  # noqa: F401  T7 (#13): the seam is the optimizer now
from api.timeline import build_timeline  # noqa: F401  # T8 (#14) lives in api/timeline.py

# TODO(T12, #20): inspiration ranking over the whole catalogue instead of these seven places.
# name -> stems for api/extract.py, why, tradeoffs (team text).
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


def future_date(month: int, day: int, today: date | None = None) -> date:
    """The next date with this month and day, not in the past."""
    today = today or date.today()
    d = date(today.year, month, day)
    return d if d >= today else date(today.year + 1, month, day)


def _min_price(offers: list[Offer], pred) -> int:
    return min((o.price_rub for o in offers if pred(o)), default=0)


def trip_cost(spec: TripSpec, name: str) -> int:
    """Cheapest hotel plus the cheapest flight each way."""
    offers = find_offers(spec, name)
    return (
        _min_price(offers, lambda o: o.kind == "hotel")
        + _min_price(offers, lambda o: o.kind == "transport" and o.origin == spec.origin)
        + _min_price(offers, lambda o: o.kind == "transport" and o.destination == spec.origin)
    )


def travel_hours(spec: TripSpec, origin: dict, place: dict) -> float:
    """The fastest way there by api/offers.py, hours; 0 when the place is the origin."""
    probe = (
        spec
        if spec.date_from
        else spec.model_copy(update={"date_from": future_date(spec.month or 1, 15)})
    )
    legs = [x for x in transport_offers(probe, origin, place) if x.origin == origin["name"]]
    return min(
        (round((x.arrive_at - x.depart_at).total_seconds() / 3600, 1) for x in legs), default=0
    )


def suggest_destinations(spec: TripSpec) -> list[Destination]:
    """2–4 destinations for the spec, violations marked.

    TODO(T12, #20): replace with the inspiration ranking over the catalogue.
    """
    sea = "море" in spec.wishes
    hub, note = catalog.origin_hub(spec.origin or "Москва")
    out = []
    for name, c in CATALOG.items():
        place = catalog.resolve(name)
        if sea and not place["sea"]:
            continue
        air, water = catalog.month_climate(place, spec.month) if spec.month else (None, None)
        hours = travel_hours(spec, hub, place)
        violates = []
        if spec.max_travel_hours and hours > spec.max_travel_hours:
            violates.append(f"в пути {hours:g} ч — дольше {spec.max_travel_hours:g} ч")
        if sea and water is not None and water < WARM_WATER_C:
            violates.append(f"море холодное для купания: вода {water:g} °C")
        out.append(
            Destination(
                name=name,
                country=place["country"],
                lat=place["lat"],
                lon=place["lon"],
                why=c["why"],
                est_total_rub=trip_cost(spec, name),
                air_c=air,
                water_c=water,
                travel_hours=hours,
                tradeoffs=c["tradeoffs"] + [note] * bool(note),
                violates=violates,
            )
        )
    out.sort(key=lambda d: (bool(d.violates), d.est_total_rub))
    return out[:4]
