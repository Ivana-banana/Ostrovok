"""Stand-ins for agent modules that are not in main yet (accepted on fixtures).

Each function is one seam: the owning task replaces it and keeps the signature.
All prices are synthetic (TRAP-7). November climate and flight hours are team estimates
without a source; the reply labels them as an estimate.
"""

from datetime import date

from api.contracts import Destination, Offer, TripSpec
from api.offers import find_offers
from api.optimizer import rank  # noqa: F401  T7 (#13): the seam is the optimizer now
from api.timeline import build_timeline  # noqa: F401  # T8 (#14) lives in api/timeline.py

# TODO(T12, #20): climate from Open-Meteo and travel hours from the catalogue (T4, #8).
# name -> slug, country, lat, lon, IANA tz, UTC offset, stems, sea, hours from Moscow,
# November (air, water) °C, room-night price for 2 adults, why, tradeoffs.
# fmt: off
_KEYS = ("slug", "country", "lat", "lon", "tz", "utc", "stems", "sea", "hours", "nov", "night",
         "why", "tradeoffs")
_ROWS = [
    ("Анталия", "ayt", "Турция", 36.8969, 30.7133, "Europe/Istanbul", 3, ("антал",), True, 4.0,
     (21.0, 22.0), 6500, "тёплое море, семейные отели, прямой перелёт",
     ["в ноябре вода на границе комфорта"]),
    ("Хургада", "hrg", "Египет", 27.2579, 33.8116, "Africa/Cairo", 2, ("хургад", "египет"), True,
     4.8, (27.0, 26.0), 6000, "самое тёплое море из коротких перелётов",
     ["перелёт почти 5 ч", "пляжи с кораллами — нужна обувь"]),
    ("Сочи", "aer", "Россия", 43.5855, 39.7231, "Europe/Moscow", 3, ("сочи",), True, 2.5,
     (12.0, 16.0), 5000, "короткий перелёт без визы и обмена валюты",
     ["море холодное для купания"]),
    ("Дубай", "dxb", "ОАЭ", 25.2048, 55.2708, "Asia/Dubai", 4, ("дуба",), True, 5.5,
     (28.0, 27.0), 9000, "жарко и тёплое море гарантированно", ["дорогие отели"]),
    ("Калининград", "kgd", "Россия", 54.7104, 20.4522, "Europe/Kaliningrad", 2, ("калининград",),
     True, 2.3, (4.0, 9.0), 4000, "Балтика и старый город", ["холодно, море не для купания"]),
    ("Казань", "kzn", "Россия", 55.7961, 49.1064, "Europe/Moscow", 3, ("казан",), False, 1.6,
     (0.0, None), 4500, "город на выходные, близко", ["моря нет"]),
    ("Санкт-Петербург", "led", "Россия", 59.9343, 30.3351, "Europe/Moscow", 3,
     ("петербург", "питер", "спб"), False, 1.5, (2.0, None), 5000, "музеи и прогулки",
     ["моря для купания нет"]),
]
# fmt: on
CATALOG: dict[str, dict] = {row[0]: dict(zip(_KEYS, row[1:], strict=True)) for row in _ROWS}

# Estimate, not from a source: below this the sea counts as cold for swimming.
# It sits between catalogue values on purpose (Antalya 22, Sochi 16).
WARM_WATER_C = 21.5
# #114, #157: the months where the November climate stands in for the real one (warmer places stay warmer).
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


def trip_cost(spec: TripSpec, name: str) -> int:
    """Cheapest hotel plus the cheapest flight each way."""
    offers = find_offers(spec, name)
    return (
        _min_price(offers, lambda o: o.kind == "hotel")
        + _min_price(offers, lambda o: o.kind == "transport" and o.origin == spec.origin)
        + _min_price(offers, lambda o: o.kind == "transport" and o.destination == spec.origin)
    )


def suggest_destinations(
    spec: TripSpec, limit: int = 4, exclude: list[str] | None = None
) -> list[Destination]:
    """2–4 destinations (up to `limit`) for the spec, violations marked.

    exclude: refused cities and countries (#113); «Турция» drops every city of Turkey.

    TODO(T4, #8; T12, #20): replace with the destination catalogue and inspiration ranking.
    """
    sea = "море" in spec.wishes
    exclude = exclude or []
    out = []
    for name, c in CATALOG.items():
        if (sea and not c["sea"]) or name in exclude or c["country"] in exclude:
            continue
        air, water = c["nov"] if spec.month == 11 else (None, None)
        violates = []
        if spec.max_travel_hours and c["hours"] > spec.max_travel_hours:
            violates.append(f"перелёт {c['hours']:g} ч — дольше {spec.max_travel_hours:g} ч")
        if sea and water is not None and water < WARM_WATER_C:
            violates.append(f"море холодное для купания: вода {water:g} °C")
        if sea and spec.month != 11:  # #157: no data is not «passes»; model.py says so once
            nov_water = c["nov"][1]
            if spec.month in COLD_MONTHS and nov_water is not None and nov_water < WARM_WATER_C:
                note = f"море холодное для купания: вода {nov_water:g} °C в ноябре"
                if spec.month != 10:  # October is usually warmer than November: no claim
                    note += f", {IN_MONTH[spec.month - 1]} не теплее"
                violates.append(note)
        out.append(
            Destination(
                name=name,
                country=c["country"],
                lat=c["lat"],
                lon=c["lon"],
                why=c["why"],
                est_total_rub=trip_cost(spec, name),
                air_c=air,
                water_c=water,
                travel_hours=c["hours"],
                tradeoffs=c["tradeoffs"],
                violates=violates,
            )
        )
    out.sort(key=lambda d: (bool(d.violates), d.est_total_rub))
    return out[:limit]
