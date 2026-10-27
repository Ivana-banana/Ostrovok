"""Destination catalogue (T4, #8): places, climate by month, airports, departure hubs.

catalog.json is built by scripts/build_catalog.py; sources, licences and the date are in its
_source. Free text resolves to a place: case, ё/е, a leading preposition, Russian case endings,
ru/en aliases (TRAP-3), a country name (its main place), then a close spelling.
A name outside the snapshot goes to lookup_online: the Open-Meteo geocoder and one year of
climate with a short timeout. Any error there gives None, never an exception (TRAP-2, TRAP-6).
"""

import difflib
import json
import math
import re
import statistics
import time
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from functools import cache
from pathlib import Path

from api.guards import name_candidates, normalize_toponym

HERE = Path(__file__).parent
ONLINE_CACHE = HERE.parents[2] / "data" / "online_places.json"  # /app/data in the container
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
MARINE = "https://marine-api.open-meteo.com/v1/marine"
YEAR = "2025"  # one year of climate for a place outside the snapshot
TIMEOUT_S = 4  # one request
GEOCODE_BUDGET_S = 8  # all requests for one name together (#244): no new one starts after it
WIDE_COUNT = 5  # records asked when the first one was another place
FAILED_S = 60  # after a network error the geocoder is not asked again for this long
SEA_KM = 30  # as in scripts/build_catalog.py
# #224: tag "swim" = "sea for swimming", apart from "sea" = "the sea is near". As in
# scripts/build_catalog.py. SWIM_WATER_C equals api.fixtures.WARM_WATER_C (test_catalog.py checks).
SWIM_WATER_C = 21.5
SWIM_MONTHS = 3  # months of the year with the sea this warm: a season, not two weeks
AIRPORT_KM = 200
OSRM = "https://router.project-osrm.org/table/v1/driving/"
# Road choice of the airport as in scripts/build_catalog.py (ROAD_SPAN, ROAD_PLUS_KM, ROAD_GAIN,
# ROAD_GAIN_KM; D-020, test_catalog.py checks they are equal), at most ROAD_CANDIDATES of them
ROAD_SPAN, ROAD_PLUS_KM, ROAD_GAIN, ROAD_GAIN_KM = 1.5, 30, 0.15, 20
ROAD_CANDIDATES = 3
FUZZY = 0.85  # «Антлия» -> «Анталия», but «Малага» is not «Мале»
ENDINGS = ("ий", "ый", "ой", "ая", "ое", "ие", "ые", "ь", "й", "а", "я", "о", "е", "и", "у",
           "ю", "ы")  # fmt: skip
# Case endings after a stem: «Гори» is not «Гоа» + «ри» (TRAP-3).
CASES = {"", "а", "я", "у", "ю", "е", "и", "ы", "ой", "ей", "ом", "ем", "ою", "ею", "ью", "ам", "ям",
         "ах", "ях", "ов", "ев", "ий", "ый", "ая", "ое", "ие", "ые", "ую", "ого", "его", "ому",
         "ему", "ых", "их"}  # fmt: skip
PREPOSITION = re.compile(r"^(?:в|во|на|к|ко|до|из|по)\s+")

_deadline: ContextVar[float | None] = ContextVar("geocode_deadline", default=None)
_online: dict[str, dict | None] = {}
_failed: dict[str, float] = {}  # name -> time.monotonic() of the last network error


def swims(sea: list | None) -> bool:
    """Sea for swimming: SWIM_MONTHS or more months with the water at SWIM_WATER_C or warmer."""
    return sum(t is not None and t >= SWIM_WATER_C for t in sea or []) >= SWIM_MONTHS


@cache
def snapshot() -> dict:
    return json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))


def places() -> list[dict]:
    return snapshot()["places"]


def hubs() -> list[dict]:
    """Departure cities in Russia."""
    return [p for p in places() if p["hub"]]


def norm(text: str) -> str:
    s = unicodedata.normalize("NFKC", text).strip().lower().replace("ё", "е")
    return re.sub(r"[\s\-‐–]+", " ", s)


def _stem(word: str) -> str:
    end = next((e for e in ENDINGS if word.endswith(e) and len(word) - len(e) >= 2), "")
    return word[: len(word) - len(end)]


def _same_words(query: list[str], key: list[str]) -> bool:
    """Same words up to a Russian case ending: «нижнем новгороде» = «нижний новгород»."""
    return len(query) == len(key) and all(
        q.startswith(_stem(k)) and q[len(_stem(k)) :] in CASES
        for q, k in zip(query, key, strict=True)
    )


def _stems(text: str) -> str:
    return " ".join(_stem(w) for w in text.split())


@cache
def _keys() -> list[tuple[str, dict]]:
    return [(norm(k), p) for p in places() for k in (p["name"], *p["aliases"])]


def _query(text: str) -> str:
    return PREPOSITION.sub("", norm(normalize_toponym(text)))


def country_of(text: str) -> str | None:
    """Country name from free text: «в Турцию» -> «Турция»."""
    q = _query(text).split()
    return next(
        (c for c, keys in snapshot()["countries"].items()
         for k in keys if q == k.split() or _same_words(q, k.split())),
        None,
    )  # fmt: skip


def by_country(text: str) -> list[dict]:
    """Places of the country in the text, the main place first, then by population."""
    c = country_of(text)
    return sorted(
        (p for p in places() if p["country"] == c), key=lambda p: (not p["main"], -p["pop"])
    )


def resolve(text: str) -> dict | None:
    """Snapshot place for a name as the user wrote it (TRAP-3), or None."""
    q = _query(text)
    if not q:
        return None
    if exact := [p for k, p in _keys() if k == q]:
        return exact[0]
    near = sorted(((len(k), p["name"], p) for k, p in _keys() if _same_words(q.split(), k.split())))
    if near:
        return near[-1][2]
    if (found := by_country(text)) and found[0]["main"]:
        return found[0]  # «в Турцию» -> Анталия; Россия has no main place
    if len(q) < 4:
        return None
    stems = {_stems(k): p for k, p in _keys() if len(k.split()) == len(q.split())}
    close = difflib.get_close_matches(_stems(q), stems, n=1, cutoff=FUZZY)
    return stems[close[0]] if close else None


def month_climate(place: dict, month: int) -> tuple[float | None, float | None]:
    """Mean daily maximum of the air and the sea surface, °C, for the month; None: no data."""
    c = place.get("climate")
    if not c:
        return None, None
    return c["air"][month - 1], (c["sea"] or [None] * 12)[month - 1]


def find(
    month: int | None = None,
    country: str | None = None,
    sea: bool | None = None,
    tags: Iterable[str] = (),
    air_min: float | None = None,
    air_max: float | None = None,
    sea_min: float | None = None,
    rain_max: float | None = None,
) -> list[dict]:
    """Places that pass every given filter; the climate filters need the month."""
    c = country_of(country) if country else None
    out = []
    for p in places():
        if (country and p["country"] != c) or (sea is not None and p["sea"] != sea):
            continue
        if not set(tags) <= set(p["tags"]):
            continue
        if month:
            air, water = month_climate(p, month)
            rain = p["climate"]["rain"][month - 1]
            if (air_min is not None and (air is None or air < air_min)) or (
                air_max is not None and (air is None or air > air_max)
            ):
                continue
            if sea_min is not None and (water is None or water < sea_min):
                continue
            if rain_max is not None and (rain is None or rain > rain_max):
                continue
        out.append(p)
    return out


def km_between(a: dict, b: dict) -> float:
    """Great-circle distance, km."""
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(h))


def nearest(point: dict, pool: Iterable[dict] | None = None) -> dict:
    return min(places() if pool is None else pool, key=lambda p: km_between(point, p))


def origin_hub(name: str, fetch: Callable[[str, dict], dict] | None = None) -> tuple[dict, str]:
    """Departure hub for a city the user named, and a note to say aloud when it is another city."""
    place = resolve(name) or lookup_online(name, fetch)
    if place is None:
        return resolve("Москва"), f"Город вылета «{name.strip()}» не нашёл: считаю вылет из Москвы."
    if place["hub"]:
        return place, ""
    hub = nearest(place, hubs())
    return hub, (
        f"Вылет — из города {hub['name']}: {place['name']} не в списке городов вылета, "
        f"{hub['name']} ближе всех ({km_between(place, hub):.0f} км)."
    )


def request_url(url: str, params: dict) -> str:
    """The full GET address; eval/geocoder_answers.json keys its recorded answers by it."""
    return f"{url}?{urllib.parse.urlencode(params)}"


def _get(url: str, params: dict) -> dict:
    req = urllib.request.Request(request_url(url, params))
    left = _deadline.get()
    timeout = TIMEOUT_S if left is None else max(0.5, min(TIMEOUT_S, left - time.monotonic()))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _monthly(days: list[str], values: list, how=statistics.fmean) -> list[float | None]:
    by_month: dict[int, list[float]] = {}
    for d, v in zip(days, values, strict=True):
        if v is not None:
            by_month.setdefault(int(d[5:7]), []).append(v)
    return [round(how(by_month[m]), 1) if m in by_month else None for m in range(1, 13)]


def _airport(here: dict, fetch: Callable[[str, dict], dict]) -> tuple[dict, float, bool]:
    """Airport of a place outside the snapshot, the straight km to it, and "OSRM failed".

    The straight-line nearest airport is replaced by the nearest by road (one OSRM table request
    to the ROAD_CANDIDATES nearest) only for a gain of ROAD_GAIN or ROAD_GAIN_KM, as in the
    snapshot (build_catalog.airport_fields). "road": True means km are by road. No network or
    no road: the straight-line nearest, "road": False, the place still works (TRAP-6).
    """
    ports = [{"iata": a[0], "lat": a[1], "lon": a[2], "tz": a[3]} for a in snapshot()["airports"]]
    by_km = sorted(((km_between(here, a), a) for a in ports), key=lambda x: x[0])
    near = [x for x in by_km if x[0] <= ROAD_SPAN * by_km[0][0] + ROAD_PLUS_KM][:ROAD_CANDIDATES]
    line, port = near[0]
    km, road, failed = round(line), False, False
    if len(near) > 1:
        try:
            points = ";".join(f"{p['lon']},{p['lat']}" for p in (here, *(a for _, a in near)))
            body = fetch(OSRM + points, {"sources": 0, "annotations": "distance"})
            metres = body["distances"][0][1:]
            if body["code"] != "Ok" or len(metres) != len(near):
                raise ValueError(body["code"])
        except (OSError, KeyError, IndexError, TypeError, ValueError):  # TRAP-6: straight line
            failed = True
        else:
            ok = [
                (round(m / 1000), k, a)
                for (k, a), m in zip(near, metres, strict=True)
                if m is not None
            ]
            if ok:
                km, line, port = min(ok, key=lambda x: x[0])
                first = None if metres[0] is None else round(metres[0] / 1000)
                if first is not None and first - km < min(ROAD_GAIN_KM, ROAD_GAIN * first):
                    km, line, port = first, near[0][0], near[0][1]  # the gain is too small
                road = True
    return {"iata": port["iata"], "km": km, "tz": port["tz"], "road": road}, line, failed


def _geocode(name: str, fetch: Callable[[str, dict], dict]) -> dict | None:
    """The geocoder's answer for a name in any case (TRAP-3), or None: never another place.

    Open-Meteo searches fuzzily and gives one record: «Тарту» -> Тартус, «Сиене» -> Сиенегитас,
    «Кал» -> Кали (#244). So an answer counts only when its name is the name asked:
    - the name as written is asked first, as the user wrote it: «Кале» is Calais, «Малибу» is
      not a case of «Малиба». The answer must be the same words up to a case ending;
    - then the nominative guesses: «Старую Ладогу» asks «Старая Ладога», «Бледе» asks «Блед»,
      «Бледа», «Бледь». The answer to a guess must be the guess itself, letter for letter;
    - last, when the first answer was another place, a longer list for the name as written:
      «Тарту» is the second record after Тартус.
    All of it fits GEOCODE_BUDGET_S: the first network error or the end of the time stops it.
    """
    ask = {"count": 1, "language": "ru", "format": "json"}
    written = name.strip()
    words = norm(written).split()
    deadline = time.monotonic() + GEOCODE_BUDGET_S
    token = _deadline.set(deadline)

    def search(guess: str, count: int = 1) -> list[dict]:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"geocoder budget {GEOCODE_BUDGET_S} s is over: {name}")
        return fetch(GEOCODE, {**ask, "count": count, "name": guess}).get("results") or []

    try:
        for guess in dict.fromkeys([written, *name_candidates(name)]):
            for g in search(guess):
                if guess == written:
                    same = _same_words(words, norm(g["name"]).split())
                else:
                    same = norm(g["name"]) == norm(guess)
                if same:
                    return g
        # Written name: the first record was another place. A longer list may have the right one.
        wide = search(written, WIDE_COUNT)
        return next((g for g in wide if _same_words(words, norm(g["name"]).split())), None)
    finally:
        _deadline.reset(token)


def _online_place(name: str, fetch: Callable[[str, dict], dict]) -> dict | None:
    g = _geocode(name, fetch)
    if g is None:
        return None
    lat, lon = round(g["latitude"], 4), round(g["longitude"], 4)
    span = {"latitude": lat, "longitude": lon, "start_date": f"{YEAR}-01-01",
            "end_date": f"{YEAR}-12-31", "timezone": "auto"}  # fmt: skip
    d = fetch(ARCHIVE, {**span, "daily": "temperature_2m_max,precipitation_sum"})["daily"]
    air = _monthly(d["time"], d["temperature_2m_max"])
    rain = _monthly(d["time"], d["precipitation_sum"], sum)  # mm in the month
    s = fetch(MARINE, {**span, "daily": "sea_surface_temperature_mean"})
    here, sea = {"lat": lat, "lon": lon}, None
    if km_between(here, {"lat": s["latitude"], "lon": s["longitude"]}) <= SEA_KM:
        sea = _monthly(s["daily"]["time"], s["daily"]["sea_surface_temperature_mean"])
        sea = sea if any(x is not None for x in sea) else None
    airport, line, flaky = _airport(here, fetch)
    country = g.get("country", "")
    levels = [p["level"] for p in places() if p["country"] == country]
    place = {
        "slug": f"geo-{g['id']}", "name": g["name"], "country": country, "cc": g.get("country_code", ""), "lat": lat,
        "lon": lon, "tz": g["timezone"], "elev": g.get("elevation"), "pop": g.get("population") or 0,
        "geonameid": g.get("id"), "hub": False, "main": False, "sea": sea is not None,
        "tags": ["sea"] * (sea is not None) + ["swim"] * swims(sea), "aliases": [norm(name)] if norm(name) != norm(g["name"]) else [],
        "airport": airport, "airport_km": airport["km"] if line <= AIRPORT_KM else None,
        "climate": {"air": air, "rain": rain, "sea": sea}, "source": "online",
    } | ({"_flaky": True} if flaky else {})  # fmt: skip
    if levels:
        place["level"] = int(statistics.median(levels))
    return place


def lookup_online(name: str, fetch: Callable[[str, dict], dict] | None = None) -> dict | None:
    """A place outside the snapshot from the Open-Meteo geocoder and climate API, or None.

    Cached in the process and, when the path is writable, in data/online_places.json.
    Climate here is one year (YEAR), not the ten-year mean of the snapshot.
    """
    key = norm(name)
    if not key:
        return None
    if key in _online:
        return _online[key]
    try:
        disk = json.loads(ONLINE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        disk = {}
    if key in disk:
        _online[key] = disk[key]
        return disk[key]
    if time.monotonic() - _failed.get(key, -FAILED_S) < FAILED_S:
        return None  # offline demo: one timeout per city a minute, not one per call
    try:
        place = _online_place(name.strip(), fetch or _get)
    except Exception:  # noqa: BLE001  TRAP-6: no network, a timeout or a strange answer
        _failed[key] = time.monotonic()
        return None  # not in _online: after FAILED_S the geocoder is asked again
    flaky = place is not None and place.pop("_flaky", False)  # OSRM failed: not frozen on disk
    _online[key] = place
    if place is not None:
        keys = {key, norm(place["name"])}  # «Бледе» and «Блед» are one place
        _online.update(dict.fromkeys(keys, place))
    if place is not None and not flaky:
        try:
            ONLINE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            ONLINE_CACHE.write_text(
                json.dumps({**disk, **dict.fromkeys(keys, place)}, ensure_ascii=False), "utf-8"
            )
        except OSError:
            pass  # read-only file system: the process cache is enough
    return place
