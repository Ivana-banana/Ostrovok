"""Offer providers (T5, #9): hotels with 2-4 tariffs, transport, transfers and add-ons.

- Places: the catalogue snapshot (api/catalog.py: centre, IANA zone, price level, distance
  to airport and station). offers_for_place works for any coordinates (TRAP-2); a name
  outside the snapshot goes to the online geocoder, [] when it finds nothing.
- Hotels: osm_hotels.json (OpenStreetMap snapshot) when it has the city, else synthetic
  hotels around the centre. Every tariff (price, meal, cancellation) is synthetic.
- Tariffs: every hotel sells 2-4 tariffs (meal RO/BB or BB/HB/AI x free cancellation or not),
  each its own HotelOffer with its own id, transfers and add-ons. They do not read
  spec.meal or spec.refundable; the optimizer picks the tariff.
- Transport: flight, train or car from the great-circle distance, fixed local time slots;
  a car drives at most MAX_DRIVE a day and stops for the night on a longer road (#276).
- Transfers: hub -> hotel after each arrival, hotel -> hub before each return, per hotel.
- Add-ons: early check-in, late check-out and the night before, per hotel.

Deterministic: the same place, dates and guests give the same offers (sha256 seed, no
random module). Network only for a name outside the catalogue, with a fallback (TRAP-6).
Every offer is synthetic=True (TRAP-7); prices and times come only from here (TRAP-4).
All coefficients below are team estimates without a source; level and free_cancel_share of a
place with 101hotels facts come from scripts/calibrate_prices.py (catalog.json _source, #119).
"""

import hashlib
import json
import math
import re
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from importlib.resources import files
from pathlib import Path
from zoneinfo import ZoneInfo

from api import catalog
from api.contracts import (
    Cancellation,
    ExtraOffer,
    HotelOffer,
    HotelProvider,
    Meal,
    Mode,
    Offer,
    Place,
    TransferOffer,
    TransferProvider,
    TransportOffer,
    TransportProvider,
    TripSpec,
)

HERE = Path(__file__).parent
ZONE_TAB = files("tzdata").joinpath("zoneinfo", "zone.tab")
DEFAULT_LEVEL = 5500  # night in a 3-star room for two, roubles, when a place has no level
SEASON = {1: 0.95, 2: 0.9, 3: 0.9, 4: 0.95, 5: 1.1, 6: 1.2, 7: 1.3, 8: 1.3, 9: 1.1, 10: 0.95,
          11: 0.9, 12: 1.05}  # fmt: skip
STAR_FACTOR = {1: 0.55, 2: 0.7, 3: 0.95, 4: 1.45, 5: 2.5}
MEAL_RUB = {"RO": 0, "BB": 700, "HB": 1800, "FB": 2800, "AI": 3800}  # person-night, level 5000
SYNTH_STARS = [2, 3, 4, 4, 5]
SYNTH_KM = {2: (2.5, 6.0), 3: (1.0, 3.0), 4: (0.4, 2.5), 5: (0.2, 1.2)}
CITY_NAMES = ["Центральный", "Панорама", "Старый город", "Парк", "Гранд", "Бульвар", "Сити",
              "Акварель", "Европа", "Северная звезда"]  # fmt: skip
SEA_NAMES = ["Морской бриз", "Лазурный берег", "Ривьера", "Променад", "Бухта"]
# No direct flights from Russia: one change and 3 more hours.
NO_DIRECT = {"Италия", "Франция", "Испания", "Чехия", "Австрия", "Венгрия", "Германия",
             "Греция", "Кипр"}  # fmt: skip
OUT_FLIGHTS = [(time(10, 30), 1.0), (time(17, 40), 0.9)]  # arrival, local to the place
# A car (#276, #321): at most MAX_DRIVE at the wheel a day, then a night on the road. Every
# driving day starts at the same clock time: there at CAR_OUT, so that the car comes on the
# check-in day; back at CAR_BACK, the hotel check-out (HotelOffer.check_out). MAX_DRIVE and
# ROAD_NIGHT_RUB (a room, one room per car) are team estimates without a source (D-026). For
# reference: Regulation (EC) 561/2006 lets a professional driver drive 9-10 h a day; a private
# driver has no limit (research/domain.md).
MAX_DRIVE = timedelta(hours=12)
CAR_OUT, CAR_BACK = time(8, 0), time(12, 0)
ROAD_NIGHT_RUB = 3500
BACK_FLIGHTS = [(time(15, 20), 1.0), (time(21, 5), 0.9)]  # departure, local to the place
SYNTH_NOTE = (
    "Синтетический отель для демо: такого объекта может не быть. "
    "Цена, питание, отмена, рейтинг и удобства — синтетика."
)
OSM_NOTE = (
    "Объект из OpenStreetMap (© участники OpenStreetMap, ODbL); удобства — только из тегов OSM. "
    "Цена, питание, отмена и рейтинг — синтетика."
)


def _rand(*parts: object) -> float:
    """Deterministic number in [0, 1): the same parts give the same number in every process."""
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _between(lo: float, hi: float, *parts: object) -> float:
    return lo + (hi - lo) * _rand(*parts)


def _rub(x: float) -> int:
    return max(100, int(round(x, -2)))


def _minutes(hours: float) -> timedelta:
    return timedelta(minutes=5 * round(hours * 12))


def _shift(at: datetime, delta: timedelta) -> datetime:
    """at + delta in real time, in the zone of at: ZoneInfo arithmetic is wall-clock (DST)."""
    return (at.astimezone(UTC) + delta).astimezone(at.tzinfo)


@cache
def osm_hotels() -> dict[str, list[dict]]:
    """Overpass elements (tourism=hotel) by place name."""
    return json.loads((HERE / "osm_hotels.json").read_text(encoding="utf-8"))["places"]


places = catalog.places  # extract.py reads the city names from here
_norm = catalog.norm  # checks.py compares city names with it


def resolve_place(name: str) -> dict | None:
    """Catalogue place for a name as the user wrote it (TRAP-3), else the online geocoder, or None."""
    return catalog.resolve(name) or catalog.lookup_online(name)


def _complete(p: dict) -> dict:
    """Defaults for a place from a geocoder: name, lat, lon and tz are enough."""
    russia = p.get("country") == "Россия"
    defaults = {"country": "", "sea": False, "level": DEFAULT_LEVEL, "airport_km": 25,
                "station_km": 3 if russia else None, "aliases": []}  # fmt: skip
    slug = p.get("slug") or hashlib.sha1(p["name"].encode()).hexdigest()[:6]
    return _open_airport({**defaults, **p, "slug": slug})


def _open_airport(p: dict) -> dict:
    """C8 (#363): a place behind a closed airport gets the nearest open airport of its country
    (no border on the way), km by the straight line; the transfer prices the road by these km.
    A substitute farther than catalog.AIRPORT_KM flies only without a train (transport_offers)."""
    if not closed_airport(p):
        return p
    port = min(_open_ports(p.get("cc", "")), key=lambda a: km_between(p, a), default=None)
    if port is None:
        return {**p, "airport": None, "airport_km": None}
    km = round(km_between(p, port))
    return {**p, "airport": {**port, "km": km}, "airport_km": km}


@cache
def _open_ports(cc: str) -> list[dict]:
    """Open airports of the snapshot in a country: the country of an airport is the one of its
    zone in zone.tab of the tzdata package."""
    rows = (x.split("\t") for x in ZONE_TAB.read_text(encoding="utf-8").splitlines())
    zones = {r[2] for r in rows if len(r) > 2 and r[0] == cc}
    return [
        {"iata": a[0], "lat": a[1], "lon": a[2], "tz": a[3]}
        for a in catalog.snapshot()["airports"]
        if a[3] in zones and a[0] not in CLOSED_AIRPORTS
    ]


def km_between(a: dict, b: dict) -> float:
    """Great-circle distance, km."""
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(h))


def _guests_tag(spec: TripSpec) -> str:
    return f"{spec.adults}a" + "".join(f"c{a}" for a in spec.children_ages)


ROOM_GUESTS = 4  # a standard room sleeps 2-4 (critic card C7, team estimate)


def rooms(spec: TripSpec) -> int:
    """Standard rooms for the party (C7, #363); a child under 2 sleeps in a cot."""
    guests = spec.adults + sum(a >= 2 for a in spec.children_ages)
    return max(1, math.ceil(guests / ROOM_GUESTS))


def rooms_label(spec: TripSpec) -> str:
    """«2 номера» for a party in more than one room (C7: the cart line explains the price)."""
    n = rooms(spec)
    return "" if n == 1 else _plural(n, "номер", "номера", "номеров")


def _room_factor(spec: TripSpec) -> float:
    """Room price for these guests relative to two adults; each extra room adds a single room."""
    adults = 0.85 if spec.adults == 1 else 1 + 0.45 * (spec.adults - 2)
    kids = sum(0 if a < 2 else 0.25 if a < 12 else 0.45 for a in spec.children_ages)
    return adults + kids + 0.85 * (rooms(spec) - 1)


# Child fares by mode: (age below, share of a full fare, takes a seat). An age from the last
# bound up pays the full fare. A car is paid per car elsewhere.
CHILD_FARES: dict[Mode, list[tuple[int, float, bool]]] = {
    "train": [(5, 0.0, False), (10, 0.5, True)],
    "flight": [(2, 0.1, False), (12, 0.75, True)],
}


def _child_fare(mode: Mode, age: int) -> tuple[float, bool, bool]:
    """Share of a full fare, a seat, a child fare (#323): one rule for the price and the label."""
    for below, share, seat in CHILD_FARES.get(mode, []):
        if age < below:
            return share, seat, seat
    return 1.0, True, False


def _fares(spec: TripSpec, mode: Mode) -> float:
    """Full fares for these guests: children pay less (CHILD_FARES)."""
    return spec.adults + sum(_child_fare(mode, a)[0] for a in spec.children_ages)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} {few}"
    return f"{n} {many}"


def _seats_label(spec: TripSpec, mode: Mode) -> str:
    """«6 мест (2 детских тарифа)» for a train or a plane: the seats and the child fares the
    price counts (_fares, TRAP-4). Empty for a car: it is paid per car."""
    if mode not in CHILD_FARES:
        return ""
    rules = [_child_fare(mode, a) for a in spec.children_ages]
    seats = spec.adults + sum(seat for _, seat, _ in rules)
    child = sum(kid for _, _, kid in rules)
    free = sum(not seat for _, seat, _ in rules)
    notes = []
    if child:
        notes.append(_plural(child, "детский тариф", "детских тарифа", "детских тарифов"))
    below, share, _ = CHILD_FARES[mode][0]  # the youngest go without a seat
    if free and mode == "train":
        notes.append(_plural(free, "ребёнок", "ребёнка", "детей") + f" до {below} лет без места")
    elif free:
        notes.append(_plural(free, "младенец", "младенца", "младенцев") + " без места")
    if free and share:  # #334: not free, the label names the share of the price
        notes[-1] += f", {round(share * 100, 1):g} % тарифа"
    label = _plural(seats, "место", "места", "мест")
    return f"{label} ({', '.join(notes)})" if notes else label


def _dates(spec: TripSpec) -> tuple[date, date]:
    """Check-in and check-out; callers return [] earlier when the spec has no start date."""
    if spec.date_from is None:
        raise ValueError("spec.date_from is empty: offers need a start date")
    return spec.date_from, spec.date_to or spec.date_from + timedelta(days=spec.nights or 1)


def _night_factor(d: date) -> float:
    holiday = (d.month == 12 and d.day >= 28) or (d.month == 1 and d.day <= 8)
    return (1.5 if holiday else SEASON[d.month]) * (1.15 if d.weekday() in (4, 5) else 1.0)


def _hotel_base(place: dict, key: str, name: str, stars: int, lat: float, lon: float,
                tags: dict, synthetic: bool) -> dict:  # fmt: skip
    """Hotel features. A real OSM hotel gets a feature only from its tag (no tag: no flag);
    a synthetic hotel gets synthetic features."""

    def r(what: str) -> float:
        return _rand(place["name"], key, what)

    km = round(km_between(place, {"lat": lat, "lon": lon}), 1)
    pet_tag = tags.get("pets", tags.get("dog"))
    flags = {"wheelchair": tags.get("wheelchair") == "yes"}
    pets = pet_tag in ("yes", "leashed")
    if synthetic:
        flags = {
            "family": r("family") < (0.8 if stars >= 4 or place["sea"] else 0.3),
            "kids_club": place["sea"] and stars == 5,
            "wheelchair": r("wheelchair") < (0.7 if stars >= 4 else 0.2),
            "elevator": stars >= 4 or r("elevator") < 0.6,
            "beach": place["sea"] and (km <= 1.5 or stars >= 4),
            "parking": stars <= 2 or r("parking") < 0.6,
        }
        flags["elevator"] = flags["elevator"] or flags["wheelchair"]
        pets = pets or r("pets") < 0.3
    return {
        "key": key,
        "name": name,
        "stars": stars,
        "lat": lat,
        "lon": lon,
        "km": km,
        "pets": pets,
        "amenities": [a for a, on in flags.items() if on],
        "rating": min(9.8, round(6.0 + 0.6 * stars + _between(-0.4, 0.5, place["name"], key), 1)),
    }


def _osm_bases(place: dict) -> list[dict]:
    out = []
    for e in osm_hotels().get(place["name"], [])[:5]:
        tags = e.get("tags", {})
        key = f"o{e['id']}"
        stars = tags.get("stars", "")
        stars = (
            int(stars)
            if stars in ("1", "2", "3", "4", "5")
            else 3 + (_rand(place["name"], key) < 0.5)
        )
        name = tags.get("name:ru") or tags["name"]
        base = _hotel_base(place, key, name, stars, e["lat"], e["lon"], tags, synthetic=False)
        out.append({**base, "title": f"{name}, {place['name']}", "note": OSM_NOTE})
    return out


def _synthetic_bases(place: dict) -> list[dict]:
    names = SEA_NAMES + CITY_NAMES if place["sea"] else CITY_NAMES
    shift = int(_rand(place["name"], "names") * len(names))
    out = []
    for i, stars in enumerate(SYNTH_STARS):
        key = f"s{i}"
        km = _between(*SYNTH_KM[stars], place["name"], key, "km")
        bearing = _between(0, 2 * math.pi, place["name"], key, "bearing")
        lat = place["lat"] + km / 111.2 * math.cos(bearing)
        lon = place["lon"] + km / (111.2 * math.cos(math.radians(place["lat"]))) * math.sin(bearing)
        name = names[(shift + i) % len(names)]
        # The second hotel always takes pets: a dog owner has at least one option.
        tags = {"pets": "yes"} if i == 1 else {}
        base = _hotel_base(place, key, name, stars, round(lat, 6), round(lon, 6), tags, True)
        out.append({**base, "title": f"Отель «{name}», {place['name']}", "note": SYNTH_NOTE})
    return out


def _default_meal(place: dict, base: dict) -> Meal:
    stars, r = base["stars"], _rand(place["name"], base["key"], "meal")
    if stars <= 2:
        return "RO"
    if stars == 3:
        return "BB" if r < 0.5 else "RO"
    if stars == 4:
        return "HB" if place["sea"] and r < 0.5 else "BB"
    return "AI" if place["sea"] else "BB"


def _meals(place: dict, base: dict) -> list[Meal]:
    """Two meal plans of a hotel: the default one and the next to it.

    A cheap hotel sells RO and BB. A hotel with HB or AI by default sells BB and that plan.
    """
    default = _default_meal(place, base)
    return ["RO", "BB"] if default in ("RO", "BB") else ["BB", default]


def _hotel(
    spec: TripSpec, place: dict, base: dict, now: datetime
) -> list[tuple[HotelOffer, list[ExtraOffer]]]:
    """2-4 tariffs of one hotel (meal x cancellation), each with its add-ons.

    A hotel with a free-cancellation rate that is still open sells both rates for each meal
    plan; any other hotel sells only the non-refundable one. The tariffs do not read spec.meal or
    spec.refundable: the re-plan DAG (api/dag.py) answers those edits by re-ranking the
    same offers, without a new search. The optimizer treats every tariff as its own hotel.
    """
    tz = ZoneInfo(place["tz"])
    date_in, date_out = _dates(spec)
    nights = max(1, (date_out - date_in).days)
    key = base["key"]
    # A tariff (price and offer_id) does not depend on the clock. The free-cancellation rate
    # exists only until its deadline: after it the hotel sells the non-refundable rate alone,
    # so no two tariffs of a hotel share (meal, refundable).
    # Free cancellation only from 3 stars (team estimate), in the share of 3+ star hotels with it
    # on the 101hotels city page (#119), else in 80% (team estimate).
    share = place.get("free_cancel_share", 0.8)
    refundable_rate = base["stars"] >= 3 and _rand(place["name"], key, "refund") >= 1 - share
    free_until = None
    if refundable_rate:
        days = (7, 3, 1)[int(_rand(place["name"], key, "deadline") * 3)]
        free_until = datetime.combine(date_in - timedelta(days=days), time(12, 0), tz)
    has_refundable = free_until is not None and free_until > now
    room = place["level"] * STAR_FACTOR[base["stars"]] * _room_factor(spec)
    jitter = _between(0.9, 1.12, place["name"], key, date_in, nights, _guests_tag(spec))
    people = spec.adults + 0.5 * sum(a >= 2 for a in spec.children_ages)
    rooms = [room * _night_factor(date_in + timedelta(days=k)) * jitter for k in range(nights)]
    night = sum(rooms) / nights
    before = date_in - timedelta(days=1)
    out = []
    for meal in _meals(place, base):
        meal_night = MEAL_RUB[meal] * place["level"] / 5000 * people
        for refundable in (True, False) if has_refundable else (False,):
            cancellation = Cancellation(
                refundable=refundable, free_until=free_until if refundable else None
            )
            tariff = 1.0 if refundable else 0.9
            offer_id = (
                f"h-{place['slug']}-{key}-{meal}{'r' if refundable else 'n'}-"
                f"{date_in:%Y%m%d}-{nights}n-{_guests_tag(spec)}"
            )
            hotel = HotelOffer(
                offer_id=offer_id,
                title=base["title"] + (f" ({label})" if (label := rooms_label(spec)) else ""),
                city=place["name"],
                tz=place["tz"],
                date_in=date_in,
                date_out=date_out,
                adults=spec.adults,
                children_ages=spec.children_ages,
                price_rub=_rub((sum(rooms) + meal_night * nights) * tariff),
                stars=base["stars"],
                rating=base["rating"],
                meal=meal,
                cancellation=cancellation,
                distance_center_km=base["km"],
                lat=base["lat"],
                lon=base["lon"],
                pets_allowed=base["pets"],
                amenities=base["amenities"],
                description=base["note"],
            )
            extras = [
                (f"{offer_id}-early", f"Ранний заезд с 06:00 — {base['title']}", 0.5 * night),
                (f"{offer_id}-late", f"Поздний выезд до 18:00 — {base['title']}", 0.4 * night),
                (
                    f"{offer_id}-prev",
                    f"Ночь накануне, {before:%d.%m} — {base['title']}",
                    (room * _night_factor(before) * jitter + meal_night) * tariff,
                ),
            ]
            out.append(
                (
                    hotel,
                    [
                        ExtraOffer(offer_id=i, title=t, for_offer_id=offer_id, price_rub=_rub(p))
                        for i, t, p in extras
                    ],
                )
            )
    return out


def _flight_hours(km: float) -> float:
    return 0.6 + min(km, 3000) / 720 + max(km - 3000, 0) / 870


FAR_AIRPORT_KM = 60  # a place farther from its airport has none of its own (#123)
# C8 (#363): airports closed to flights, research/domain.md «Закрытые аэропорты» (sources,
# 27.10.2026). GDZ and KRR take flights again since 25.10.2026. A place behind a closed airport
# flies through the nearest open airport of the data with the road by distance (_complete), or
# goes by train or car.
CLOSED_AIRPORTS = {"AAQ", "EGO", "BZK", "VOZ", "URS", "LPK", "ROV", "SIP"}
CLOSED_NOTE = "закрыт для рейсов (по данным на 27.10.2026)"
# The last departure of an airport open only by day (research/domain.md, 27.10.2026): GDZ
# 08:30-20:00, KRR 09:00-23:00 MSK. Only the return flight from the place reads it; the arrival
# slots (OUT_FLIGHTS) are inside both windows.
LAST_DEPARTURE = {"GDZ": time(20, 0), "KRR": time(23, 0)}


def closed_airport(place: dict) -> str:
    """The IATA code of the airport of a place when it is closed to flights, else empty."""
    iata = (place.get("airport") or {}).get("iata", "")
    return iata if iata in CLOSED_AIRPORTS else ""


def _via_airport(mode: Mode, a: dict, b: dict) -> str:
    """The IATA code of the airport a flight to or from a place without an airport of its own
    goes through: the nearest one of the data. Empty when the data has no airport for the place."""
    if mode != "flight":
        return ""
    for p in (b, a):
        port = p.get("airport")
        if port and port["km"] > FAR_AIRPORT_KM:
            return port["iata"]
    return ""


def _leg(
    spec: TripSpec,
    mode: Mode,
    a: dict,
    b: dict,
    depart: datetime,
    hours: float,
    price: float,
    changes: int = 0,
) -> TransportOffer:
    """One leg a -> b; times are local to the place of each event."""
    depart = depart.astimezone(ZoneInfo(a["tz"]))
    arrive = _shift(depart, _minutes(hours)).astimezone(ZoneInfo(b["tz"]))
    what = {"flight": "Перелёт", "train": "Поезд", "car": "На своей машине"}[mode]
    port = b["airport"]["iata"] if mode == "flight" and b.get("airport") else ""
    via = _via_airport(mode, a, b)  # a flight only: a train or a car has no airport
    # The airport of arrival (#247); a place far from its airport is reached through it (#123).
    if via and via == port:
        tail = f" через {via}"
    else:
        tail = (f" ({port})" if port else "") + (f" через {via}" if via else "")
    title = f"{what} {a['name']} → {b['name']}{tail}, {depart:%d.%m %H:%M}"
    if changes:
        title += f", пересадок: {changes}"
    if seats := _seats_label(spec, mode):
        title += f", {seats}"
    if mode == "car":
        title += " (бензин и платные дороги, оценка)"
    return TransportOffer(
        offer_id=f"t-{a['slug']}-{b['slug']}-{mode}-{depart:%Y%m%d%H%M}-{_guests_tag(spec)}",
        title=title,
        mode=mode,
        origin=a["name"],
        destination=b["name"],
        depart_at=depart,
        arrive_at=arrive,
        changes=changes,
        price_rub=_rub(price),
        pets_allowed=mode != "flight",
    )


def _car(
    spec: TripSpec, a: dict, b: dict, depart: datetime, hours: float, stops: int, price: float
) -> TransportOffer:
    """A car a -> b in 1 + stops driving days of equal length, each one from the clock time
    of `depart`, with a night on the road between them (#276, TRAP-5)."""
    drive = _minutes(hours)
    if not stops:
        return _leg(spec, "car", a, b, depart, hours, price)
    day = drive / (stops + 1)
    leg = _leg(spec, "car", a, b, depart, hours, price)
    arrive = _shift(depart, timedelta(days=stops) + day).astimezone(ZoneInfo(b["tz"]))
    title = leg.title.replace(
        " (бензин и платные дороги, оценка)",
        f", ночёвок в пути: {stops}, за рулём ≈{_span_h(day)} в день "
        "(бензин, платные дороги и ночёвки в пути, оценка)",
    )
    return leg.model_copy(update={"arrive_at": arrive, "title": title})


ROAD_NIGHTS_RE = re.compile(r"ночёвок в пути: (\d+)")  # written by _car into the title


def road_nights(leg: TransportOffer) -> int:
    """Nights on the road of a car leg, from its title (_car); 0 for any other leg."""
    m = ROAD_NIGHTS_RE.search(leg.title) if leg.mode == "car" else None
    return int(m.group(1)) if m else 0


def drive_time(leg: TransportOffer) -> timedelta:
    """Time at the wheel of a car leg without its nights on the road; elapsed time otherwise.

    _car drives 1 + nights equal days, each from the clock time of the start, so the last day
    is the elapsed time without the full days."""
    elapsed = leg.arrive_at.astimezone(UTC) - leg.depart_at.astimezone(UTC)
    nights = road_nights(leg)
    return (elapsed - timedelta(days=nights)) * (nights + 1)


def _span_h(delta: timedelta) -> str:
    hours, minutes = divmod(int(delta.total_seconds() // 60), 60)
    return f"{hours} ч {minutes:02d} мин" if minutes else f"{hours} ч"


# Rail (#343): the route is RAIL_DETOUR x the straight line. A day train goes DAY_TRAIN_KMH on
# average with its stops, an overnight one 70 km/h. A high-speed corridor (FAST_LINES) has its
# own time between its ends; a place within FAST_END_KM of an end adds a local leg at
# DAY_TRAIN_KMH. Corridor times (research/domain.md): «Сапсан» Москва - Санкт-Петербург
# 3:30-4:15; Москва - Нижний Новгород 3:55; Москва - Владимир 1:46. Calibrated on the pairs of
# tests/test_rail_time.py within ±20 %; the rest are team estimates (D-028). Prices do not
# depend on the speed.
# A day train comes by DAY_ARRIVE_BY (no arrival at night, TRAP-5). Back it leaves at DAY_BACK,
# earlier when it would come later, never before _back_from (check-out and the way to the
# station, #370); a longer ride back goes by the overnight train only.
RAIL_DETOUR = 1.2
DAY_TRAIN_KMH = 75
FAST_LINES = {
    frozenset(("Москва", "Санкт-Петербург")): 4.0,
    frozenset(("Москва", "Нижний Новгород")): 3.9,
    frozenset(("Москва", "Владимир")): 1.8,
}
FAST_END_KM = 60
DAY_OUT, DAY_BACK, DAY_ARRIVE_BY = time(7, 40), time(16, 20), time(23, 0)
# #370 (TRAP-5): a leg back leaves no earlier than the check-out, the transfer from the
# farthest hotel to the hub and the margin at the hub, so its transfer never starts before the
# check-out. CHECK_OUT is HotelOffer.check_out; FAR_HOTEL_KM the farthest synthetic hotel
# (SYNTH_KM); HUB_MARGIN as transfer_offers. Times of the transfer: _ride.
CHECK_OUT = time(12, 0)
FAR_HOTEL_KM = max(hi for _, hi in SYNTH_KM.values())
HUB_MARGIN = {"flight": timedelta(minutes=120), "train": timedelta(minutes=40)}


def _hub_road(hub_km: float, hotel_km: float) -> float:
    """Road between a hotel and the airport or the station, km (the hub is out of the centre)."""
    return round(hub_km + 0.6 * hotel_km, 1)


CITY_RIDE_KM = 100  # a transfer drives the first km at 35 km/h, the rest at the car speed (C8)


def _ride(road: float) -> timedelta:
    """A transfer ride: 10 minutes to board, 35 km/h up to CITY_RIDE_KM, then the car speed of
    a road leg (80 km/h, x1.1)."""
    city, far = min(road, CITY_RIDE_KM), max(road - CITY_RIDE_KM, 0)
    return _minutes(10 / 60 + city / 35 + far / 80 * 1.1)


def _back_from(place: dict, mode: Mode, day: date) -> datetime:
    """The earliest departure back from `place` on `day`: check-out, ride, margin; 5 min up."""
    hub_km = place["airport_km"] if mode == "flight" else place["station_km"]
    out = datetime.combine(day, CHECK_OUT, ZoneInfo(place["tz"]))
    at = _shift(out, _ride(_hub_road(hub_km, FAR_HOTEL_KM)) + HUB_MARGIN[mode])
    up = (-at.minute) % 5
    return _shift(at, timedelta(minutes=up)).replace(second=0, microsecond=0)


def _ends(p: dict) -> list[tuple[str, float]]:
    """Corridor ends near a place, with the local leg to each, hours."""
    names = {n for line in FAST_LINES for n in line}
    out = [(p["name"], 0.0)] if p["name"] in names else []
    for n in names - {p["name"]}:
        end = catalog.resolve(n)
        km = km_between(p, end) if end else FAST_END_KM + 1
        if km <= FAST_END_KM:
            out.append((n, km * RAIL_DETOUR / DAY_TRAIN_KMH))
    return out


def _day_train_hours(a: dict, b: dict, route: float) -> float | None:
    """Hours of the day train a -> b: a corridor with its local legs when it is faster, else
    route / DAY_TRAIN_KMH up to 800 km of route; None when no day train goes."""
    slow = max(0.5, route / DAY_TRAIN_KMH) if route <= 800 else None
    fast = [
        line + x + y
        for ea, x in _ends(a)
        for eb, y in _ends(b)
        if (line := FAST_LINES.get(frozenset((ea, eb))))
    ]
    best = min([*fast, *([slow] if slow else [])], default=None)
    return round(best, 2) if best else None


def _port(p: dict) -> dict:
    """Where a flight of the place lands: its substitute airport (_open_airport), else the place."""
    port = p.get("airport") or {}
    return port if "lat" in port else p


def transport_offers(spec: TripSpec, origin: dict, place: dict) -> list[TransportOffer]:
    """There on date_from and back on date_to: flights, trains, and a car if asked for."""
    km = km_between(origin, place)
    if km < 30 or spec.date_from is None:
        return []
    a, b = origin, place
    ta, tb = ZoneInfo(a["tz"]), ZoneInfo(b["tz"])
    there, back = _dates(spec)

    def fare(mode: Mode, base: float, day: date, slot: time) -> float:
        noise = _between(0.92, 1.1, mode, a["name"], b["name"], day, slot)
        return base * SEASON[day.month] * noise * _fares(spec, mode)

    legs: list[TransportOffer] = []
    # A substitute airport (C8) counts: Москва -> Воронеж through DME is no flight.
    hops = km_between(_port(a), _port(b))
    route = km * RAIL_DETOUR
    rail = a["station_km"] is not None and b["station_km"] is not None and route <= 3000
    # The rule of the catalogue (catalog.AIRPORT_KM): a far substitute counts only without a train
    far = any((x.get("airport") or {}).get("km", 0) > catalog.AIRPORT_KM for x in (a, b))
    flies = (not closed_airport(a) and not closed_airport(b) and min(km, hops) >= 300
             and not (far and rail))  # fmt: skip
    if a["airport_km"] is not None and b["airport_km"] is not None and flies:
        pair = {a["country"], b["country"]}
        changes = int("Россия" in pair and bool(pair & NO_DIRECT))
        hours = _flight_hours(hops) + 3 * changes  # airport to airport (C8)
        base = 2200 + 3.6 * hops + 3000 * changes
        for slot, k in OUT_FLIGHTS:
            depart = _shift(datetime.combine(there, slot, tb), -_minutes(hours))
            price = fare("flight", base * k, there, slot)
            legs.append(_leg(spec, "flight", a, b, depart, hours, price, changes))
        earliest, taken = _back_from(b, "flight", back), set()
        last = LAST_DEPARTURE.get((b.get("airport") or {}).get("iata", ""))
        for slot, k in BACK_FLIGHTS:
            # A far airport (#370): the slot moves after the check-out and the transfer.
            depart = max(datetime.combine(back, slot, tb), earliest)
            if last and depart.time() > last:  # the airport closes (C8)
                depart = datetime.combine(back, last, tb)
            if depart < earliest or depart in taken:
                continue
            taken.add(depart)
            price = fare("flight", base * k, back, slot)
            legs.append(_leg(spec, "flight", b, a, depart, hours, price, changes))
    if rail:
        # (hours, fare base, start there, start back or None, fare slot back)
        trains: list[tuple[float, float, datetime, datetime | None, time]] = []
        day = _day_train_hours(a, b, route)
        earliest = _back_from(b, "train", back)
        if day is not None:  # a day train comes by DAY_ARRIVE_BY both ways (#343, TRAP-5)
            out = datetime.combine(there, DAY_OUT, ta)
            by = datetime.combine(back, DAY_ARRIVE_BY, ta)
            latest = _shift(by, -_minutes(day)).astimezone(tb)
            latest = latest.replace(minute=latest.minute - latest.minute % 5)
            ret = min(max(datetime.combine(back, DAY_BACK, tb), earliest), latest)
            if ret < earliest:
                ret = None
            if _shift(out, _minutes(day)).astimezone(tb) <= datetime.combine(
                there, DAY_ARRIVE_BY, tb
            ):
                trains.append((day, 700 + 3.4 * route, out, ret, DAY_BACK))
        if route >= 600:  # overnight train: arrives in the morning
            hours, base = route / 70, 1100 + 3.0 * route
            out = _shift(datetime.combine(there, time(7, 10), tb), -_minutes(hours))
            ret = max(datetime.combine(back, time(21, 40), tb), earliest)
            trains.append((hours, base, out, ret, time(21, 40)))
        for hours, base, out, ret, slot in trains:
            legs.append(
                _leg(spec, "train", a, b, out, hours, fare("train", base, there, out.time()))
            )
            if ret is not None:  # the fare noise keys on the slot: the price does not move
                legs.append(_leg(spec, "train", b, a, ret, hours, fare("train", base, back, slot)))
    road = km * 1.3
    if road <= 2500 and ("car" in spec.modes or not legs):
        hours = road / 80 * 1.1
        cars = math.ceil((spec.adults + len(spec.children_ages)) / 5)
        stops = math.ceil(_minutes(hours) / MAX_DRIVE) - 1  # nights on the road
        price = road * (8 / 100 * 62 + 1.0) * cars  # 8 l/100 km at 62 ₽ + tolls
        price += stops * ROAD_NIGHT_RUB * cars
        # The car leaves `stops` days before the check-in and comes on the check-in day.
        out = datetime.combine(there - timedelta(days=stops), CAR_OUT, ta)
        ret = datetime.combine(back, CAR_BACK, tb)
        legs.append(_car(spec, a, b, out, hours, stops, price))
        legs.append(_car(spec, b, a, ret, hours, stops, price))
    wanted = [leg for leg in legs if leg.mode in spec.modes] or legs
    # There first, then back: the cart lists items in the order of the offers.
    return sorted(wanted, key=lambda leg: leg.origin != a["name"])


def transfer_id(hotel_id: str, leg_id: str) -> str:
    """offer_id of the transfer between a hotel and a transport leg (both directions)."""
    return "x-" + hashlib.sha1(f"{hotel_id}|{leg_id}".encode()).hexdigest()[:12]


def transfer_offers(
    spec: TripSpec, place: dict, hotels: list[HotelOffer], legs: list[TransportOffer]
) -> list[TransferOffer]:
    """Airport or station -> hotel after an arrival; hotel -> hub before a return."""
    tz = ZoneInfo(place["tz"])
    minivan = 1.6 if spec.adults + len(spec.children_ages) > 4 else 1.0
    out = []
    for leg in legs:
        if leg.mode == "car":
            continue
        flight = leg.mode == "flight"
        hub_km = place["airport_km"] if flight else place["station_km"]
        hub = "аэропорт" if flight else "вокзал"
        if flight and place.get("airport"):
            hub += " " + place["airport"]["iata"]
        # C10 (#364): the road to the airport crosses water by a ferry (catalog «ferry»).
        ferry = ", дорога и паром" if flight and (place.get("airport") or {}).get("ferry") else ""
        for h in hotels:
            road = _hub_road(hub_km, h.distance_center_km or 0)
            ride = _ride(road)
            if leg.destination == place["name"]:
                start = _shift(
                    leg.arrive_at.astimezone(tz), timedelta(minutes=40 if flight else 15)
                )
                title = f"Трансфер: {hub} → {h.title}, ≈{road:g} км{ferry}"
            else:
                end = _shift(leg.depart_at.astimezone(tz), -HUB_MARGIN[leg.mode])
                start = _shift(end, -ride)
                title = f"Трансфер: {h.title} → {hub}, ≈{road:g} км{ferry}"
            out.append(
                TransferOffer(
                    offer_id=transfer_id(h.offer_id, leg.offer_id),
                    title=title,
                    depart_at=start,
                    arrive_at=_shift(start, ride),
                    price_rub=_rub((400 + 30 * road) * minivan),
                )
            )
    return out


# Adapters behind the ports (api/contracts.py, «Порты»; ADR-0002). Each search reads the
# module variables below; a business swaps a source with one assignment (README).


class OsmOrSynthetic:
    """Hotels from the OSM snapshot when it has at least 3 for the place, else synthetic."""

    def hotels(
        self, spec: TripSpec, place: Place, now: datetime
    ) -> list[tuple[HotelOffer, list[ExtraOffer]]]:
        osm = _osm_bases(place)
        bases = osm if len(osm) >= 3 else _synthetic_bases(place)
        return [pair for b in bases for pair in _hotel(spec, place, b, now)]


class SyntheticTransport:
    """Flight, train or car from the great-circle distance (transport_offers)."""

    def legs(self, spec: TripSpec, origin: Place, place: Place) -> list[TransportOffer]:
        return transport_offers(spec, origin, place)


class SyntheticTransfers:
    """Hub <-> hotel rides after each arrival and before each return (transfer_offers)."""

    def transfers(
        self,
        spec: TripSpec,
        place: Place,
        hotels: list[HotelOffer],
        legs: list[TransportOffer],
    ) -> list[TransferOffer]:
        return transfer_offers(spec, place, hotels, legs)


HOTELS: HotelProvider = OsmOrSynthetic()
TRANSPORT: TransportProvider = SyntheticTransport()
TRANSFERS: TransferProvider = SyntheticTransfers()


def offers_for_place(
    spec: TripSpec, place: dict, origin: dict | None = None, now: datetime | None = None
) -> list[Offer]:
    """All offers for one place and the spec dates: hotels, transport, transfers, add-ons.

    place and origin need only name, lat, lon and tz (TRAP-2); the rest has defaults.
    """
    if not spec.date_from:
        return []
    place = _complete(place)
    now = now or datetime.now(UTC)  # only the cancellation state depends on it
    pairs = HOTELS.hotels(spec, place, now)
    hotels = [h for h, _ in pairs]
    legs = TRANSPORT.legs(spec, _complete(origin), place) if origin and not spec.own_transport else []
    own = own_legs(spec, place["name"])
    transfers = TRANSFERS.transfers(spec, place, hotels, legs + own)
    return [*hotels, *legs, *transfers, *(x for _, extras in pairs for x in extras)]


OWN_THERE, OWN_BACK = "own-there", "own-back"  # leg_id of the user's own tickets (C5, #408)


def own_legs(spec: TripSpec, city: str) -> list[TransportOffer]:
    """The user's own arrival and departure as legs for the transfers and the timeline only.

    They are never offers: nothing to buy. A leg there is spec.arrive_at, a leg back
    spec.leave_at; a train when the user named only the train, else a flight.
    """
    if not spec.own_transport:
        return []
    mode: Mode = "train" if spec.modes == ["train"] else "flight"
    out = []
    for leg_id, at, there in ((OWN_THERE, spec.arrive_at, True), (OWN_BACK, spec.leave_at, False)):
        if at is None:
            continue
        ends = (spec.origin or "", city) if there else (city, spec.origin or "")
        out.append(
            TransportOffer(
                offer_id=leg_id,
                title="Свои билеты",
                mode=mode,
                origin=ends[0],
                destination=ends[1],
                depart_at=at,
                arrive_at=at,
                price_rub=1,  # never shown or summed: not an offer
            )
        )
    return out


def find_offers(spec: TripSpec, name: str, now: datetime | None = None) -> list[Offer]:
    """Offers for a destination name from the spec; [] when the name is unknown."""
    place = resolve_place(name)
    if place is None:
        return []
    origin = resolve_place(spec.origin) if spec.origin else None
    return offers_for_place(spec, place, origin, now)
