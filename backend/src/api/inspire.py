"""Inspiration (T12, #20): 2–4 destinations from catalog.json for a spec without a destination.

The wishes pick the places by catalogue fields only: «море» — sea, «лыжи» — tag ski, «горы» —
tag mountains, «санаторий» — tag spa, «лес», «природа», «озеро» — tag nature (no big city);
with no theme the pool is the big cities (tag city and CITY_POP). «Европа» and «юг» narrow the
pool by country and by latitude. The climate of the month marks the places that break the
request («тепло», warm sea). Only CANDIDATES places get the price and travel time of
api/offers.py, never the whole catalogue. A card that breaks the request goes below the others;
places closer than SPREAD_KM to a shown one wait for a free slot.
"""

from collections import Counter
from datetime import timedelta

from api import catalog
from api import offers as ports  # TRANSPORT is read at call time: a test may swap it
from api.contracts import Destination, TransportOffer, TripSpec
from api.fixtures import COLD_MONTHS, WARM_WATER_C, future_date, trip_cost
from api.offers import FAR_AIRPORT_KM, drive_time, find_offers, road_nights
from api.optimizer import pets_possible

# Places priced per call: find_offers runs for each, so the cost grows with this number.
CANDIDATES = 16
SPREAD_KM = 150  # Анталия and Кемер are one choice for the user, not two
SHOWN_PER_COUNTRY = 2
CITY_POP = 1_000_000  # with no theme: the big cities (GeoNames population), nearest first
PER_COUNTRY = 3  # candidates of one country: the 21 places of Турция leave room for Египет
# #114: estimate without a source: in the cold half of the year a place is warm from this air.
WARM_AIR_C = 15.0  # «на юг» in the cold half of the year
WARM_ASKED_C = (
    22.0  # #342: «тепло», «погреться»: team estimate (D-027), no source for the beach air
)
# Estimates without a source, for the prefilter and the minus lines only.
ROAD_KM = 1745  # a drive up to 24 h: api/offers.py drives road / 80 h × 1.1, road = 1.3 × the line
RAIL_KM = 3000  # api/offers.py builds a train up to 3000 km of route, route = 1.2 × the line
KM_PER_HOUR = 800  # a flight with the airport time; the exact hours come from api/offers.py
CAR_KM = 1900  # api/offers.py drives up to 2500 km of road, road = 1.3 × the straight line
SOUTH_DEG = 5.0  # «на юг»: this many degrees of latitude south of the departure hub
RAINY_MM = 150
HOT_C = 33.0
LONG_ROAD_H = 4.0
HIGH_M = 1000  # the height is named in the reason from here (as the mountains tag of catalog.json)
# «в Европу» from Russia: the European countries of catalog.json; Russia, Belarus and Turkey
# are not counted (the user means a trip abroad to Europe).
EUROPE = {
    "Австрия", "Албания", "Бельгия", "Болгария", "Босния и Герцеговина",
    "Великобритания", "Венгрия", "Германия", "Греция", "Дания", "Ирландия", "Исландия",
    "Испания", "Италия", "Кипр", "Латвия", "Литва", "Мальта", "Молдавия", "Нидерланды",
    "Норвегия", "Польша", "Португалия", "Румыния", "Северная Македония", "Сербия", "Словакия",
    "Словения", "Финляндия", "Франция", "Хорватия", "Черногория", "Чехия", "Швейцария",
    "Швеция", "Эстония",
}  # fmt: skip
# wish -> (catalogue tag, why, violation when the place has no such tag)
TAGS = {
    "лыжи": ("ski", "горнолыжный курорт", "не горнолыжный курорт"),
    "горы": ("mountains", "горы", "не в горах"),
    "санаторий": ("spa", "лечебный курорт", "не лечебный курорт"),
    "природа": ("nature", "природа, тишина", "не природа"),
}
MODES = {"flight": "самолётом", "train": "поездом", "car": "на машине"}
# No trip at all, not a worse one: such a card goes below the cards that only break a wish.
NO_WAY = "сюда не добраться"
NO_PETS = "с питомцем поездку не собрать: транспорт или отель не берут животных"
NO_SEA = "моря нет"
NO_SWIM = "море не для купания"  # #224: the sea is near, but its water is warm for 1–2 months only
NOT_EUROPE = "не в Европе"
NOT_SOUTH = "не южнее, чем"
NOT_WARM = "не тепло"
# Not the asked kind of place: below a place of the theme with cold water or a long road.
OFF_THEME = {NO_SEA, NO_SWIM, NOT_EUROPE, *(miss for _, _, miss in TAGS.values())}
WHY = {tag: why for tag, why, _ in TAGS.values()}
THEMES = (*TAGS, "море", "Европа", "юг")  # wishes that pick places by catalogue facts


def _month(spec: TripSpec) -> int | None:
    return spec.month or (spec.date_from.month if spec.date_from else None)


def _themed(spec: TripSpec) -> bool:
    """«тепло» is a theme in the cold months only (#114): in July every place is warm."""
    warm = "тепло" in spec.wishes and _month(spec) in COLD_MONTHS
    return warm or any(w in spec.wishes for w in THEMES)


def _rule_breaks(spec: TripSpec, place: dict, hub: dict, month: int | None) -> list[str]:
    """What breaks the request without the price and the travel time: theme, region, climate."""
    out = [miss for wish, (tag, _, miss) in TAGS.items()
           if wish in spec.wishes and tag not in place["tags"]]  # fmt: skip
    if "Европа" in spec.wishes and place["country"] not in EUROPE:
        out.append(NOT_EUROPE)
    if "юг" in spec.wishes and place["lat"] > hub["lat"] - SOUTH_DEG:
        out.append(f"{NOT_SOUTH} {hub['name']}")
    air, water = catalog.month_climate(place, month) if month else (None, None)
    if "море" in spec.wishes:
        if not place["sea"]:
            out.append(NO_SEA)
        elif water is not None and water < WARM_WATER_C:
            out.append(f"море холодное для купания: вода {water:g} °C")
        elif "swim" not in place["tags"]:
            out.append(NO_SWIM)
    warm_wish = "тепло" in spec.wishes
    limit = WARM_ASKED_C if warm_wish else WARM_AIR_C  # #235: «на юг» in winter = warm
    asked = warm_wish or "юг" in spec.wishes
    if asked and month in COLD_MONTHS and air is not None and air < limit:
        out.append(f"{NOT_WARM}: днём {air:g} °C")
    return out


def _why(spec: TripSpec, place: dict, water: float | None) -> str:
    """The reason from catalogue facts: the asked theme first, then the other tags."""
    asked = [tag for wish, (tag, _, _) in TAGS.items() if wish in spec.wishes]
    tags = [t for t, _, _ in TAGS.values() if t in place["tags"]]
    tags.sort(key=lambda t: t not in asked)
    high = (place["elev"] or 0) >= HIGH_M
    parts = [f"горы, {place['elev']} м" if t == "mountains" and high else WHY[t] for t in tags]
    if place["sea"]:
        swim = "swim" in place["tags"] and (water or 0) >= WARM_WATER_C
        sea = "море для купания" if swim else "у моря"
        parts.insert(0 if "море" in spec.wishes else len(parts), sea)
    return ", ".join(parts) or ("природа" if "nature" in place["tags"] else "город")


def _road(spec: TripSpec, hub: dict, place: dict) -> tuple[float, float, int]:
    """The way there by the transport port (offers.TRANSPORT), hours: door to door (the
    fastest leg, as Destination.travel_hours), at the wheel (#321: a car without its nights on
    the road, any other leg door to door), and the nights on the road of that leg. 0 at the
    origin."""
    probe = (
        spec
        if spec.date_from
        else spec.model_copy(update={"date_from": future_date(spec.month or 1, 15)})
    )
    legs = [x for x in ports.TRANSPORT.legs(probe, hub, place) if x.origin == hub["name"]]
    if not legs:
        return 0, 0, 0
    door = min(_hours(x.arrive_at - x.depart_at) for x in legs)
    best: TransportOffer = min(legs, key=drive_time)
    return door, _hours(drive_time(best)), road_nights(best)


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 1)


def _nights_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} ночёвка"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} ночёвки"
    return f"{n} ночёвок"


# A tradeoff that starts with ROAD_LINE is the road of a car with nights on the road (#321):
# model._text_inspire and web Board.tsx show it in place of «в пути N ч».
ROAD_LINE = "за рулём "


def _road_text(door: float, wheel: float, nights: int) -> str:
    if not nights:
        return f"дорога {door:g} ч"
    return f"{ROAD_LINE}{wheel:g} ч, {_nights_word(nights)} в пути"


def _minuses(
    place: dict, month: int | None, road: tuple[float, float, int], note: str
) -> list[str]:
    air, water = catalog.month_climate(place, month) if month else (None, None)
    rain = place["climate"]["rain"][month - 1] if month else None
    door, wheel, nights = road
    out = []
    if door >= LONG_ROAD_H:
        out.append(_road_text(door, wheel, nights))
    if water is not None and place["sea"] and WARM_WATER_C <= water < WARM_WATER_C + 2:
        out.append(f"вода {water:g} °C — на границе комфорта")
    if air is not None and air >= HOT_C:
        out.append(f"жарко: днём {air:g} °C")
    if rain is not None and rain >= RAINY_MM:
        out.append(f"дождливо: {rain:g} мм осадков за месяц")
    if (place["airport_km"] or 0) > FAR_AIRPORT_KM:
        out.append(f"от аэропорта {place['airport_km']} км")
    out += [note] * bool(note)
    return out or [_road_text(door, wheel, nights)]


def card(spec: TripSpec, hub: dict, place: dict, note: str = "") -> Destination:
    """One destination: price and travel time from api/offers.py, the rest from catalog.json."""
    month = _month(spec)
    air, water = catalog.month_climate(place, month) if month else (None, None)
    road = _road(spec, hub, place)
    hours, wheel, nights = road
    offers = find_offers(spec, place["name"])
    cost = trip_cost(spec, offers)
    violates = _rule_breaks(spec, place, hub, month)
    legs = [o for o in offers if o.kind == "transport"]
    if spec.modes and legs and not any(o.mode in spec.modes for o in legs):
        violates.append(f"{'/'.join(MODES[m] for m in spec.modes)} {NO_WAY}")
    if not pets_possible(spec, offers):
        violates.append(NO_PETS)
    # #321: a car is filtered by the hours at the wheel, its nights on the road do not count.
    if spec.max_travel_hours and wheel > spec.max_travel_hours:
        what = "за рулём" if nights else "в пути"
        violates.append(f"{what} {wheel:g} ч — дольше {spec.max_travel_hours:g} ч")
    if spec.budget_rub is not None and cost > spec.budget_rub:
        violates.append(f"дороже бюджета: {cost:,} ₽ при {spec.budget_rub:,} ₽".replace(",", " "))
    return Destination(
        name=place["name"],
        country=place["country"],
        lat=place["lat"],
        lon=place["lon"],
        why=_why(spec, place, water),
        est_total_rub=cost,
        air_c=air,
        water_c=water,
        travel_hours=hours,
        tradeoffs=_minuses(place, month, road, note),
        violates=violates,
    )


def _reachable(spec: TripSpec, hub: dict, place: dict) -> bool:
    """A way without a plane, as api/offers.py builds it: a train between two stations, and,
    when the car is allowed, a drive of up to 24 h (a banned flight, not a chosen car)."""
    km = catalog.km_between(hub, place)
    train = hub["station_km"] is not None and place["station_km"] is not None
    return ("train" in spec.modes and train and km * 1.2 <= RAIL_KM) or (
        "car" in spec.modes and km * 1.3 <= ROAD_KM
    )


def _pool(spec: TripSpec, hub: dict, exclude: list[str]) -> list[dict]:
    """Places of the theme, without the refused ones and the departure city."""
    origin = catalog.resolve(spec.origin) if spec.origin else None
    out = [
        p
        for p in catalog.places()
        if p is not hub
        and p is not origin
        and p["name"] not in exclude
        and p["country"] not in exclude
    ]
    if spec.max_travel_hours:
        near = [p for p in out if catalog.km_between(hub, p) <= spec.max_travel_hours * KM_PER_HOUR]
        out = near or out
    if spec.modes == ["car"]:
        out = [p for p in out if catalog.km_between(hub, p) <= CAR_KM] or out
    if "природа" in spec.wishes:  # quiet and forest: no big city (a city with the nature tag stays)
        out = [p for p in out if "nature" in p["tags"] or p["pop"] < CITY_POP]
    if not _themed(spec):
        out = [p for p in out if "city" in p["tags"] and p["pop"] >= CITY_POP]
    if spec.modes and "flight" not in spec.modes and spec.modes != ["car"]:
        # #277: a banned flight is no soft wish: the places only a plane reaches go out while
        # others stay; nothing reachable: all stay, each card then says «сюда не добраться»
        out = [p for p in out if _reachable(spec, hub, p)] or out
    return out


def _level(violates: list[str]) -> int:
    """0 fits; 1 breaks a wish (cold water, long road, budget); 2 not the asked kind of place;
    3 no trip at all."""
    if any(v == NO_PETS or v.endswith(NO_WAY) for v in violates):
        return 3
    if any(v in OFF_THEME or v.startswith((NOT_SOUTH, NOT_WARM)) for v in violates):
        return 2
    return int(bool(violates))


def _rank(d: Destination) -> tuple:
    return _level(d.violates), d.est_total_rub


def _spread(cards: list[Destination], limit: int) -> list[Destination]:
    """Cards in order, the fitting ones first. Among them a card close to a shown one or a third
    of one country waits until the others are used up; a fitting card never waits for a breaking one."""
    shown: list[Destination] = []
    groups: dict[int, list[Destination]] = {}
    for d in cards:
        groups.setdefault(_level(d.violates), []).append(d)
    for _, group in sorted(groups.items()):
        for d in group:
            far = all(catalog.km_between(vars(d), vars(s)) >= SPREAD_KM for s in shown)
            if far and sum(s.country == d.country for s in shown) < SHOWN_PER_COUNTRY:
                shown.append(d)
        shown += [d for d in group if d not in shown]
    return sorted(shown[:limit], key=_rank)


def suggest_destinations(
    spec: TripSpec, limit: int = 4, exclude: list[str] | None = None
) -> list[Destination]:
    """Up to `limit` destinations for the spec, the ones that break the request last.

    exclude: refused cities and countries (#113); «Турция» drops every city of Turkey.
    """
    exclude = exclude or []
    hub, note = catalog.origin_hub(spec.origin or "Москва")
    month = _month(spec)

    def order(p: dict) -> tuple:
        return _level(_rule_breaks(spec, p, hub, month)), catalog.km_between(hub, p)

    picked, per_country = [], Counter()
    for p in sorted(_pool(spec, hub, exclude), key=order):
        if per_country[p["country"]] < PER_COUNTRY:
            picked.append(p)
            per_country[p["country"]] += 1
        if len(picked) == max(CANDIDATES, limit):
            break
    cards = sorted((card(spec, hub, p, note) for p in picked), key=_rank)
    return _spread(cards, limit)
