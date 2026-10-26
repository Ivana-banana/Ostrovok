"""Stand-ins for agent modules that are not in main yet (accepted on fixtures).

Each function is one seam: the owning task replaces it and keeps the signature.
All prices are synthetic (TRAP-7). November climate and flight hours are team estimates
without a source; the reply labels them as an estimate.
"""

import re
from datetime import UTC, date, datetime, time, timedelta, timezone

from api.contracts import (
    Cancellation,
    Cart,
    Destination,
    Explanation,
    HotelOffer,
    Offer,
    Plan,
    TimelineEvent,
    TimelineIssue,
    TransportOffer,
    TripSpec,
    Voucher,
)
from api.guards import MONTHS, check_arrival_vs_checkin, rule_extract

ORIGIN_TZ = timezone(timedelta(hours=3))  # Moscow; the only origin in the fixture

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
ORIGINS = {"москв": "Москва", "петербург": "Санкт-Петербург", "питер": "Санкт-Петербург"}
WORD_NUMBERS = {"двое": 2, "вдвоем": 2, "вдвоём": 2, "трое": 3, "втроем": 3, "четверо": 4}


def _month_of(word: str) -> int | None:
    if word in ("май", "мая"):
        return 5
    return next((n for stem, n in MONTHS.items() if stem != "ма" and word.startswith(stem)), None)


def future_date(month: int, day: int, today: date | None = None) -> date:
    """The next date with this month and day, not in the past."""
    today = today or date.today()
    d = date(today.year, month, day)
    return d if d >= today else date(today.year + 1, month, day)


def extract(text: str) -> tuple[dict, list[str]]:
    """TripSpec fields stated in one message, plus assumptions made while reading it.

    TODO(T3, #7): replace with the full rule extractor (T11 adds the LLM one).
    """
    raw = rule_extract(text)
    t = text.lower()
    found: dict = {}
    notes: list[str] = []
    m = re.search(rf"\bиз\s+({'|'.join(ORIGINS)})\w*", t)
    if m:
        found["origin"] = ORIGINS[m.group(1)]
        t = t.replace(m.group(0), " ")  # the origin is not a destination
    dests = [n for n, c in CATALOG.items() if any(re.search(rf"\b{s}", t) for s in c["stems"])]
    # TODO(T4, #8): a city outside the catalogue is kept as written; the geocoder resolves it.
    other = re.search(r"\b[вВ]о?\s+([А-ЯЁ][а-яё-]+)", text)
    if dests or other:
        found["destinations"] = dests[:1] or [other.group(1)]
    if raw["month"]:
        found["month"] = raw["month"]
    m = re.search(r"\bс\s+(\d{1,2})\s+([а-я]+)", t)
    if m and (month := _month_of(m.group(2))):
        found["month"] = month
        try:
            found["date_from"] = future_date(month, int(m.group(1)))
        except ValueError:
            notes.append(f"даты: «{m.group(1)} {m.group(2)}» нет в календаре, даты подобраны")
    m = re.search(r"\b(\d{1,2})\s*ноч\w*", t)
    if m and int(m.group(1)) >= 1:
        found["nights"] = int(m.group(1))
    elif m:
        notes.append(f"длительность: «{m.group(0)}» не бывает, взята длительность по умолчанию")
    elif re.search(r"\bнедел", t):
        found["nights"] = 7
    m = re.search(r"\b(\d)\s*взросл\w*", t)
    word = next((n for w, n in WORD_NUMBERS.items() if re.search(rf"\b{w}\b", t)), None)
    if m and int(m.group(1)) < 1:
        notes.append(f"взрослых: «{m.group(0)}» не бывает, состав прежний")
    elif m or word:
        found["adults"] = int(m.group(1)) if m else word
    if raw["budget_rub"]:
        found["budget_rub"] = raw["budget_rub"]
    if raw["kids"]:
        ages = [int(a) for a in re.findall(r"\b(\d{1,2})\s*(?:лет|год)", t)]
        if not ages:
            ages = [7]
            notes.append("возраст ребёнка: 7 лет (возраст не назван)")
        found["children_ages"] = ages
    if raw["pets"]:
        found["pets"] = True
    if re.search(r"завтрак", t):
        found["meal"] = "BB"
    wishes = [w for w, on in (("море", raw["sea"]), ("недорого", raw["cheap"])) if on]
    if re.search(r"без\s+(?:долг|длинн)\w*\s+перел", t):
        found["max_travel_hours"] = 5.0
        wishes.append("без долгих перелётов")
    if wishes:
        found["wishes"] = wishes
    return found, notes


def _guests(spec: TripSpec) -> int:
    return spec.adults + len(spec.children_ages)


def _hotels(spec: TripSpec, name: str) -> list[HotelOffer]:
    c = CATALOG[name]
    tz = timezone(timedelta(hours=c["utc"]))
    night = c["night"] * (spec.adults + 0.5 * len(spec.children_ages)) / 2
    tiers = [  # suffix, title, price factor, stars, rating, meal, refundable, km to centre
        ("eco", "Эконом", 0.75, 3, 7.9, "RO", False, 3.5),
        ("cmf", "Комфорт", 1.0, 4, 8.6, "BB", True, 1.2),
        ("fam", "Семейный", 1.6, 5, 9.1, "AI" if c["sea"] else "HB", True, 0.4),
    ]
    out = []
    for i, (suf, title, k, stars, rating, meal, refundable, km) in enumerate(tiers):
        free_until = datetime.combine(spec.date_from - timedelta(days=3), time(12, 0), tz)
        out.append(
            HotelOffer(
                offer_id=f"h-{c['slug']}-{suf}-{spec.date_from:%Y%m%d}-{spec.nights}",
                title=f"Отель «{title}», {name}",
                city=name,
                tz=c["tz"],
                date_in=spec.date_from,
                date_out=spec.date_to,
                adults=spec.adults,
                children_ages=spec.children_ages,
                price_rub=int(round(night * k * spec.nights, -2)),
                stars=stars,
                rating=rating,
                meal=meal,
                cancellation=Cancellation(
                    refundable=refundable, free_until=free_until if refundable else None
                ),
                distance_center_km=km,
                lat=c["lat"] + 0.01 * i,
                lon=c["lon"] + 0.01 * i,
                pets_allowed=i == 1,
            )
        )
    return out


def _flight(spec: TripSpec, name: str, back: bool, day: date, hh: int, k: float) -> TransportOffer:
    c = CATALOG[name]
    dest_tz = timezone(timedelta(hours=c["utc"]))
    from_tz, to_tz = (dest_tz, ORIGIN_TZ) if back else (ORIGIN_TZ, dest_tz)
    depart = datetime.combine(day, time(hh, 0), from_tz)
    origin, dest = (name, spec.origin) if back else (spec.origin, name)
    return TransportOffer(
        offer_id=f"t-{c['slug']}-{'back' if back else 'out'}-{day:%Y%m%d}-{hh:02d}",
        title=f"Перелёт {origin} → {dest}, {depart:%d.%m %H:%M}",
        mode="flight",
        origin=origin,
        destination=dest,
        depart_at=depart,
        arrive_at=(depart + timedelta(hours=c["hours"])).astimezone(to_tz),
        price_rub=int(round(c["hours"] * 4200 * k, -2)) * _guests(spec),
    )


def find_offers(spec: TripSpec, name: str) -> list[Offer]:
    """Hotels and flights for one destination and the spec dates.

    TODO(T5, #9): replace with providers (OSM hotels + synthetic tariffs, transport, transfers).
    """
    if name not in CATALOG:  # TODO(T4, #8): geocoder for cities outside the snapshot
        return []
    offers: list[Offer] = list(_hotels(spec, name))
    if spec.origin and spec.origin != name:
        offers += [
            _flight(spec, name, False, spec.date_from, 6, 0.85),
            _flight(spec, name, False, spec.date_from, 13, 1.0),
            _flight(spec, name, True, spec.date_to, 15, 1.0),
        ]
    return offers


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


def rank(spec: TripSpec, offers: list[Offer]) -> tuple[list[str], list[Explanation]]:
    """Selected offer ids (hotel, flight there, flight back) and why.

    TODO(T7, #13): replace with the optimizer (user weights, "what was sacrificed").
    """
    hotels = sorted(
        (o for o in offers if o.kind == "hotel" and (not spec.meal or o.meal == spec.meal)),
        key=lambda o: o.price_rub,
    ) or sorted((o for o in offers if o.kind == "hotel"), key=lambda o: o.price_rub)
    out = sorted(
        (o for o in offers if o.kind == "transport" and o.origin == spec.origin),
        key=lambda o: o.price_rub,
    )
    back = [o for o in offers if o.kind == "transport" and o.destination == spec.origin]
    legs = out[:1] + back[:1]
    road = sum(o.price_rub for o in legs)
    fits = [h for h in hotels if not spec.budget_rub or h.price_rub + road <= spec.budget_rub]
    if not hotels:
        return [], []
    if "недорого" in spec.wishes or not fits:
        hotel = (fits or hotels)[0]
    else:
        hotel = max(fits, key=lambda h: h.rating or 0)
    risks = []
    if spec.budget_rub and hotel.price_rub + road > spec.budget_rub:
        risks.append("не укладывается в бюджет даже в самом дешёвом варианте")
    best = max(hotels, key=lambda h: h.rating or 0)
    tradeoffs = (
        [f"рейтинг ниже, чем у варианта {best.title} ({best.rating})"] if hotel is not best else []
    )
    why = (
        f"{hotel.stars}★, рейтинг {hotel.rating}, питание {hotel.meal}, "
        f"{'бесплатная отмена' if hotel.cancellation.refundable else 'невозвратный тариф'}"
    )
    expl = [Explanation(offer_id=hotel.offer_id, why=why, tradeoffs=tradeoffs, risks=risks)]
    expl += [Explanation(offer_id=o.offer_id, why="самый дешёвый рейс") for o in legs]
    return [hotel.offer_id] + [o.offer_id for o in legs], expl


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
        tz = timezone(timedelta(hours=CATALOG[hotel.city]["utc"]))
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
