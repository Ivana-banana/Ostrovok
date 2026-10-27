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
    return {**defaults, **p, "slug": slug}


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


def _room_factor(spec: TripSpec) -> float:
    """Room price for these guests relative to two adults."""
    adults = 0.85 if spec.adults == 1 else 1 + 0.45 * (spec.adults - 2)
    return adults + sum(0 if a < 2 else 0.25 if a < 12 else 0.45 for a in spec.children_ages)


def _fares(spec: TripSpec, mode: Mode) -> float:
    """Full fares for these guests: children pay less, a car is paid per car elsewhere."""
    if mode == "train":
        kids = sum(0 if a < 5 else 0.5 if a < 10 else 1 for a in spec.children_ages)
    else:
        kids = sum(0.1 if a < 2 else 0.75 if a < 12 else 1 for a in spec.children_ages)
    return spec.adults + kids


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
                title=base["title"],
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
    if a["airport_km"] is not None and b["airport_km"] is not None and km >= 300:
        pair = {a["country"], b["country"]}
        changes = int("Россия" in pair and bool(pair & NO_DIRECT))
        hours = _flight_hours(km) + 3 * changes
        base = 2200 + 3.6 * km + 3000 * changes
        for slot, k in OUT_FLIGHTS:
            depart = _shift(datetime.combine(there, slot, tb), -_minutes(hours))
            price = fare("flight", base * k, there, slot)
            legs.append(_leg(spec, "flight", a, b, depart, hours, price, changes))
        for slot, k in BACK_FLIGHTS:
            depart = datetime.combine(back, slot, tb)
            price = fare("flight", base * k, back, slot)
            legs.append(_leg(spec, "flight", b, a, depart, hours, price, changes))
    route = km * 1.2
    if a["station_km"] is not None and b["station_km"] is not None and route <= 3000:
        trains = []
        if route <= 800:  # day train
            hours, base = max(0.5, route / 170), 700 + 3.4 * route
            trains.append((hours, base, datetime.combine(there, time(7, 40), ta), time(16, 20)))
        if route >= 600:  # overnight train: arrives in the morning
            hours, base = route / 70, 1100 + 3.0 * route
            out = _shift(datetime.combine(there, time(7, 10), tb), -_minutes(hours))
            trains.append((hours, base, out, time(21, 40)))
        for hours, base, out, back_at in trains:
            ret = datetime.combine(back, back_at, tb)
            legs.append(
                _leg(spec, "train", a, b, out, hours, fare("train", base, there, out.time()))
            )
            legs.append(_leg(spec, "train", b, a, ret, hours, fare("train", base, back, back_at)))
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
        for h in hotels:
            road = round(hub_km + 0.6 * (h.distance_center_km or 0), 1)
            ride = _minutes((10 + road / 35 * 60) / 60)
            if leg.destination == place["name"]:
                start = _shift(
                    leg.arrive_at.astimezone(tz), timedelta(minutes=40 if flight else 15)
                )
                title = f"Трансфер: {hub} → {h.title}, ≈{road:g} км"
            else:
                end = _shift(
                    leg.depart_at.astimezone(tz), -timedelta(minutes=120 if flight else 40)
                )
                start = _shift(end, -ride)
                title = f"Трансфер: {h.title} → {hub}, ≈{road:g} км"
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
    legs = TRANSPORT.legs(spec, _complete(origin), place) if origin else []
    transfers = TRANSFERS.transfers(spec, place, hotels, legs)
    return [*hotels, *legs, *transfers, *(x for _, extras in pairs for x in extras)]


def find_offers(spec: TripSpec, name: str, now: datetime | None = None) -> list[Offer]:
    """Offers for a destination name from the spec; [] when the name is unknown."""
    place = resolve_place(name)
    if place is None:
        return []
    origin = resolve_place(spec.origin) if spec.origin else None
    return offers_for_place(spec, place, origin, now)
