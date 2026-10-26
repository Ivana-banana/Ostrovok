"""Stand-ins for agent modules that are not in main yet (accepted on fixtures).

Each function is one seam: the owning task replaces it and keeps the signature.
All prices are synthetic (TRAP-7). November climate and flight hours are team estimates
without a source; the reply labels them as an estimate.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from api.contracts import (
    Cart,
    Destination,
    Offer,
    Plan,
    TimelineEvent,
    TimelineIssue,
    TripSpec,
    Voucher,
)
from api.guards import check_arrival_vs_checkin
from api.offers import find_offers
from api.optimizer import rank  # noqa: F401  T7 (#13): the seam is the optimizer now

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


def suggest_destinations(spec: TripSpec) -> list[Destination]:
    """2–4 destinations for the spec, violations marked.

    TODO(T4, #8; T12, #20): replace with the destination catalogue and inspiration ranking.
    """
    sea = "море" in spec.wishes
    out = []
    for name, c in CATALOG.items():
        if sea and not c["sea"]:
            continue
        air, water = c["nov"] if spec.month == 11 else (None, None)
        violates = []
        if spec.max_travel_hours and c["hours"] > spec.max_travel_hours:
            violates.append(f"перелёт {c['hours']:g} ч — дольше {spec.max_travel_hours:g} ч")
        if sea and water is not None and water < WARM_WATER_C:
            violates.append(f"море холодное для купания: вода {water:g} °C")
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
    return out[:4]


def build_timeline(plan: Plan) -> tuple[list[TimelineEvent], list[TimelineIssue]]:
    """Events of the selected items and docking issues.

    TODO(T8, #14): replace with the timeline checker (transfers, extras as fixes).
    """
    chosen = [o for o in plan.offers if o.offer_id in plan.selected]
    events: list[TimelineEvent] = []
    issues: list[TimelineIssue] = []
    hotel = next((o for o in chosen if o.kind == "hotel"), None)
    for o in chosen:
        if o.kind == "transport":
            events.append(
                TimelineEvent(at=o.depart_at, title=f"Вылет: {o.title}", offer_id=o.offer_id)
            )
            events.append(
                TimelineEvent(at=o.arrive_at, title=f"Прилёт: {o.destination}", offer_id=o.offer_id)
            )
    if hotel:
        tz = ZoneInfo(hotel.tz)  # the hotel may be outside CATALOG (T5, #9)
        check_in = datetime.combine(hotel.date_in, hotel.check_in, tz)
        events.append(
            TimelineEvent(at=check_in, title=f"Заселение: {hotel.title}", offer_id=hotel.offer_id)
        )
        events.append(
            TimelineEvent(
                at=datetime.combine(hotel.date_out, hotel.check_out, tz),
                title=f"Выезд: {hotel.title}",
                offer_id=hotel.offer_id,
            )
        )
        for o in chosen:
            same_day = o.kind == "transport" and o.arrive_at.date() == hotel.date_in
            if same_day and o.destination == hotel.city:
                for text in check_arrival_vs_checkin(o.arrive_at, hotel.check_in)[:1]:
                    issues.append(TimelineIssue(severity="warn", text=text))
    events.sort(key=lambda e: e.at)
    return events, issues


def fill_cart(plan: Plan) -> Cart:
    """Cart with the selected items; it replaces the old cart.

    TODO(T9, #11): replace with the cart module (POST /api/cart).
    """
    items = [o for o in plan.offers if o.offer_id in plan.selected]
    return Cart(items=items, total_rub=sum(o.price_rub for o in items))


def book(spec: TripSpec, cart: Cart) -> Voucher:
    """Simulated purchase of the cart.

    TODO(T9, #11): replace with the checkout of POST /api/checkout.
    """
    now = datetime.now(UTC)
    return Voucher(
        booking_id=f"B-{now:%Y%m%d%H%M%S}",
        items=cart.items,
        spec=spec,
        total_rub=cart.total_rub,
        created_at=now,
    )
