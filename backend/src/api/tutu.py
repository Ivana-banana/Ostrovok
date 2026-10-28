"""Real trains from tutu.ru (#569): an adapter of TransportProvider over another one.

main.py wraps offers.REAL_TRANSPORT (Travelpayouts or the synthetic adapter) in TutuTrains unless
LIVE_TRAINS=0; a chat request uses it only with real_data (offers.transport(), #490).

- Source: the route page www.tutu.ru/poezda/<From>/<To>/ (robots.txt allows it; a page with a
  date, «?date=», is disallowed). Plain HTML: the Next.js JSON in <script id="__NEXT_DATA__">
  lists the direct trains with one sample run (UTC), the travel time, the local dates of
  departure (calendar, the sales horizon) and the cheapest seat «от» per car class. No browser.
- Timetable: a train runs on `day` when the calendar has it; it leaves at the local time of the
  sample run in the zone of its station and arrives after the travel time in the zone of the
  arrival station (TRAP-5). A leg there arrives on the check-in day; a leg back leaves on the
  check-out day not before check-out, the ride and the margin (offers._back_from). Neither at
  night (timeline NIGHT_FROM-NIGHT_TO): the reception and the transfer may not work.
- Price: the cheapest «от» of the train on the page, not the price of the trip date (the dated
  page is closed by robots.txt); x full fares of the party (offers._fares). The child share is
  the team rule of the synthetic adapter; the title says so (TRAP-4, TRAP-7).
- Only the trains of the base adapter are replaced, the chosen place only (spec.destinations),
  both ends in Russia: the rules of the core and the estimate path do not move (ADR-0002).
- Real legs have synthetic=False and an offer_id ending «-tutu» (the web chip «tutu»).
- Fallback to the trains of the base adapter (TRAP-6): no network or timeout (then tutu is not
  asked for FAILED_S), a refusal (404 on an unknown slug, captcha), a bad page, no train that day.
- Disk cache: data/live/tutu/<From>-<To>.json, the parsed trains (the page has no date), fresh
  for CACHE_S; a stale file serves when the network fails. Standard library only.
"""

import gzip
import json
import logging
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from api import catalog
from api.contracts import Place, TransportOffer, TripSpec
from api.live_hotels import UA, allowed, refused
from api.offers import (
    _back_from,
    _child_fare,
    _dates,
    _fares,
    _guests_tag,
    _norm,
    _seats_label,
    _shift,
)
from api.timeline import NIGHT_FROM, NIGHT_TO

log = logging.getLogger(__name__)

HOST = "www.tutu.ru"
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "live" / "tutu"  # outside git
TIMEOUT_S = 3  # one page; both directions load in parallel
CACHE_S = 6 * 3600  # the trains of a page are fresh this long
FAILED_S = 60  # after a network error tutu is not asked again for this long
MAX_LEGS = 5  # the cheapest trains per direction
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
CLASSES = {"SEDENTARY": "сидячий", "RESERVED_SEAT": "плацкарт", "COMPARTMENT": "купе",
           "LUX": "СВ", "SOFT": "люкс"}  # fmt: skip

# tutu slugs that the transliteration misses (checked by hand on the site, 2026-10-28).
TUTU_SLUG = {"Нижний Новгород": "Nizhnij-Novgorod", "Великий Новгород": "Novgorod"}
_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
     "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))  # fmt: skip


def slug_of(name: str) -> str:
    """«Ростов-на-Дону» → «Rostov-na-Donu», «Зелёный Дол» → «Zelenyy_Dol» (tutu.ru URLs)."""
    if name in TUTU_SLUG:
        return TUTU_SLUG[name]
    out = []
    for c in name:
        t = _TR.get(c.lower(), c)
        out.append(t.capitalize() if c.isupper() else t)
    return "".join(out).replace(" ", "_")


class Refused(RuntimeError):
    """The host refused the page (HTTP status other than 200, captcha): this pair only."""


class Offline(Refused):
    """The network failed (OSError, timeout): tutu is not asked for FAILED_S."""


def parse_route(html: str) -> list[dict]:
    """Direct trains of a tutu.ru route page; [] when the page has none. Raises on a bad page."""
    m = NEXT_DATA.search(html)
    if not m:
        raise ValueError("no __NEXT_DATA__")
    page = json.loads(m.group(1))["props"]["pageProps"]["pageData"]
    stations = page["dictionary"]["stations"]
    trains = {t["hash"]: t for t in page["dictionary"].get("trains", {}).values()}
    out, seen = [], set()
    for r in page["routes"]:
        if len(r["segments"]) != 1 or not r["segments"][0].get("carTypes"):
            continue  # a change of trains or no seats on sale: not read
        seg = r["segments"][0]
        start = datetime.fromisoformat(r["departure"]["departureDateTime"])
        end = datetime.fromisoformat(r["arrival"]["arrivalDateTime"])
        a = stations[str(r["departure"]["departureGeoId"])]
        b = stations[str(r["arrival"]["arrivalGeoId"])]
        cheap = min(seg["carTypes"], key=lambda c: c["minPrice"]["amount"])
        price = cheap["minPrice"]["amount"] / 10 ** cheap["minPrice"].get("precision", 0)
        train = trains.get(seg["trainHash"], {})
        row = {
            "number": train.get("number") or "",
            "name": train.get("name") or "",
            "from": a["name"]["nominative"],
            "to": b["name"]["nominative"],
            "from_tz": a.get("tzName") or "",
            "to_tz": b.get("tzName") or "",
            "depart": start.isoformat(),
            "minutes": round((end - start).total_seconds() / 60),
            "class": CLASSES.get(cheap["type"], ""),
            "price": price,
            "days": list(r["calendar"]),
        }
        key = (row["number"], row["depart"], row["minutes"])
        if price > 0 and start.tzinfo is not None and key not in seen:
            seen.add(key)
            out.append(row)
    return out


def _night(at: datetime) -> bool:
    """The night of the timeline (NIGHT_FROM-NIGHT_TO): reception and transfers may not work."""
    return at.time() >= NIGHT_FROM or at.time() < NIGHT_TO


def _read(path: Path) -> list[dict] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("tutu cache %s: %s", path.name, e)
        return None


class TutuTrains:
    """Trains from tutu.ru for the chosen place; flights, cars and the fallback from `base`."""

    def __init__(self, base, get=None, cache_dir: Path = CACHE_DIR):
        self.base = base
        self.get = get or self._get  # (path) -> page text; raises Offline or Refused
        self.cache_dir = cache_dir
        self.failed_at = -float("inf")
        self.robots: str | None = None

    def legs(self, spec: TripSpec, origin: Place, place: Place) -> list[TransportOffer]:
        legs = self.base.legs(spec, origin, place)
        chosen = {_norm((catalog.resolve(d) or {"name": d})["name"]) for d in spec.destinations}
        russia = origin.get("country") == place.get("country") == "Россия"
        if not legs or not russia or _norm(place["name"]) not in chosen:
            return legs
        try:
            return self._real(spec, origin, place, legs)
        except Exception:  # live trains must never break a search; the log says why (TRAP-6)
            log.warning("tutu %s: сбой слоя, поезда синтетика", place["name"], exc_info=True)
            return legs

    def _real(
        self, spec: TripSpec, origin: Place, place: Place, legs: list[TransportOffer]
    ) -> list[TransportOffer]:
        there, back = _dates(spec)
        ways = [(origin, place, there), (place, origin, back)]
        trains = {x.origin for x in legs if x.mode == "train"}
        asked = [(a, b) for a, b, _ in ways if a["name"] in trains]
        with ThreadPoolExecutor(max_workers=2) as pool:  # two pages in the time of one
            rows = dict(
                zip((a["name"] for a, _ in asked), pool.map(lambda w: self._rows(*w), asked))
            )
        out: list[TransportOffer] = []
        for a, b, day in ways:
            mine = [x for x in legs if x.origin == a["name"]]
            got = rows.get(a["name"])
            real = self._trains(spec, a, b, day, place, got) if got else []
            if real:
                mine = [x for x in mine if x.mode != "train"] + real
            out += mine
        return out

    def _trains(
        self, spec: TripSpec, a: Place, b: Place, day: date, place: Place, rows: list[dict]
    ) -> list[TransportOffer]:
        """Real trains a -> b for `day`; [] means the trains of the base adapter stay."""
        legs: dict[str, TransportOffer] = {}
        earliest = _back_from(place, "train", day) if a is place else None
        try:
            for r in rows:
                za, zb = ZoneInfo(r["from_tz"] or a["tz"]), ZoneInfo(r["to_tz"] or b["tz"])
                clock = datetime.fromisoformat(r["depart"]).astimezone(za).time()
                for d in map(date.fromisoformat, r["days"]):
                    depart = datetime.combine(d, clock, za)
                    arrive = _shift(depart, timedelta(minutes=r["minutes"])).astimezone(zb)
                    if earliest is not None:  # back: on the check-out day, after check-out
                        ok = d == day and depart >= earliest and not _night(depart)
                    else:  # there: arrives on the check-in day, not at night (TRAP-5)
                        at = arrive.astimezone(ZoneInfo(place["tz"]))
                        ok = at.date() == day and not _night(at)
                    if ok:
                        leg = self._leg(spec, a, b, r, depart, arrive)
                        legs[leg.offer_id] = min(legs.get(leg.offer_id, leg), leg,
                                                 key=lambda x: x.price_rub)  # fmt: skip
        except (KeyError, TypeError, ValueError) as e:
            log.warning("tutu %s-%s %s: bad trains (%s), synthetic", a["name"], b["name"], day, e)
            return []
        if not legs:
            log.warning("tutu %s-%s %s: no train that day, synthetic", a["name"], b["name"], day)
        return sorted(legs.values(), key=lambda x: (x.price_rub, x.depart_at))[:MAX_LEGS]

    def _leg(
        self, spec: TripSpec, a: Place, b: Place, r: dict, depart: datetime, arrive: datetime
    ) -> TransportOffer:
        name = f" «{r['name']}»" if r["name"] else ""
        title = f"Поезд {r['number']}{name} {r['from']} → {r['to']}, {depart:%d.%m %H:%M}"
        if r["class"]:
            title += f", {r['class']}"
        if seats := _seats_label(spec, "train"):
            title += f", {seats}"
        title += ", цена «от» tutu.ru"  # the cheapest seat of the train, not of that date
        if any(_child_fare("train", age)[0] != 1 for age in spec.children_ages):
            title += ", детский тариф — оценка"  # a share of the adult price, not tutu (TRAP-7)
        return TransportOffer(
            offer_id=f"t-{a['slug']}-{b['slug']}-train-{depart:%Y%m%d%H%M}-{_guests_tag(spec)}-tutu",
            title=title,
            mode="train",
            origin=a["name"],
            destination=b["name"],
            depart_at=depart,
            arrive_at=arrive,
            price_rub=max(1, round(r["price"] * _fares(spec, "train"))),
            synthetic=False,
            pets_allowed=True,  # as the synthetic train: pets ride in the cars that take them
        )

    def _rows(self, a: Place, b: Place) -> list[dict] | None:
        """Trains of the page a -> b: the fresh cache, the site, a stale cache; None if none."""
        path = f"/poezda/{slug_of(a['name'])}/{slug_of(b['name'])}/"
        file = self.cache_dir / f"{slug_of(a['name'])}-{slug_of(b['name'])}.json"
        if file.exists() and time.time() - file.stat().st_mtime < CACHE_S:
            return _read(file)
        rows = None
        if time.monotonic() - self.failed_at >= FAILED_S:
            try:
                if self._allowed(path):
                    rows = parse_route(self.get(path))
                else:
                    log.warning("tutu %s: robots.txt disallows, synthetic", path)
            except Offline as e:
                self.failed_at = time.monotonic()
                log.warning("tutu %s: %s", path, e)
            except Refused as e:
                log.warning("tutu %s: %s, synthetic", path, e)
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                log.warning("tutu %s: bad page (%s), synthetic", path, e)
        if rows:
            try:
                file.parent.mkdir(parents=True, exist_ok=True)
                tmp = file.with_suffix(".tmp")
                tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                tmp.replace(file)
            except OSError as e:  # a read-only disk: the trains still serve
                log.warning("tutu cache %s: %s", file.name, e)
            return rows
        return _read(file) if file.exists() else None

    def _allowed(self, path: str) -> bool:
        if self.robots is None:
            try:
                self.robots = self.get("/robots.txt")
            except Offline:
                raise
            except Refused as e:  # no robots.txt read: allowed, the page itself may still fail
                log.warning("tutu robots.txt: %s; считаем путь разрешённым", e)
                self.robots = ""
        return allowed(self.robots, path)

    def _get(self, path: str) -> str:
        ask = {"User-Agent": UA, "Accept-Encoding": "gzip"}  # a page is 600 KB, 80 KB in gzip
        req = urllib.request.Request(f"https://{HOST}{path}", headers=ask)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                status, body = r.status, r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                text = body.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, text = e.code, ""
        except OSError as e:  # URLError, timeout, refused connection, broken gzip
            raise Offline(f"{HOST}{path}: сеть ({e!r})") from e
        if status != 200 or refused(text):
            raise Refused(f"{HOST}{path}: HTTP {status}, отказ хоста")
        return text
