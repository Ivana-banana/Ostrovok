"""Real flights from Travelpayouts (Aviasales Data API, #261): an adapter of TransportProvider.

main.py assigns it to offers.TRANSPORT only when TRAVELPAYOUTS_TOKEN is set (TRAP-1).

- One request per direction: GET /aviasales/v3/prices_for_dates, one way, one date; the
  departure city by its IATA city code (CITY_IATA), else airport IATA codes of the catalogue.
  The answer is the cheapest ticket of that date from the Aviasales search cache, not a
  bookable fare: price for one adult in roubles, departure with its UTC offset, duration in
  minutes, transfers.
- Only the flights of the synthetic adapter are replaced: trains and cars stay synthetic. A
  direction gets real flights only when the synthetic adapter flies it (distance, closed
  airports, spec.modes), so the rules of the core do not move.
- Live only for the place the user chose (spec.destinations): inspiration and dead-end checks
  price dozens of places per turn, the live API there does not fit the turn (ADR-0002).
- Times: departure and arrival in the zones of their airports (catalog airports), else of the
  place (TRAP-5). A leg there arrives by the end of the check-in day; a leg back leaves after
  check-out. Price: the API price x full fares of the party (offers._fares); the child share
  is the team rule of the synthetic adapter, the title says so (TRAP-4, TRAP-7).
- Real legs have synthetic=False; a fallback leg stays synthetic=True (TRAP-7).
- Fallback to synthetic flights of that direction: no IATA code, a network error or timeout
  (TIMEOUT_S per request, two requests fit the 5 s of ADR-0002), a bad answer, or an empty one
  (logged). After a network error the API is not asked for FAILED_S (TRAP-6).
- Disk cache: data/travelpayouts/<from>-<to>-<date>.json, fresh for CACHE_S; a stale file
  serves when the network fails (TRAP-6).
"""

import json
import logging
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from api import catalog
from api.contracts import Place, TransportOffer, TripSpec
from api.offers import (
    SyntheticTransport,
    _back_from,
    _child_fare,
    _dates,
    _fares,
    _guests_tag,
    _norm,
    _seats_label,
    _shift,
)

log = logging.getLogger(__name__)

URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "travelpayouts"  # /app/data in Docker
TIMEOUT_S = 2.5  # one request; a search makes two
CACHE_S = 6 * 3600  # a cached answer is fresh this long
FAILED_S = 60  # after a network error the API is not asked again for this long

# IATA city codes of the departure cities with several airports: the API then searches all
# of them (MOW: SVO, DME, VKO, ZIA). The place of the trip keeps its catalogue airport.
CITY_IATA = {"Москва": "MOW"}

Fetch = Callable[[str, str, date], dict]  # (from IATA, to IATA, date) -> the JSON answer


@cache
def airport_tz() -> dict[str, str]:
    """IANA zone of each airport of the catalogue snapshot."""
    return {a[0]: a[3] for a in catalog.snapshot()["airports"]}


def _iata(p: Place) -> str:
    return (p.get("airport") or {}).get("iata", "")


def _code(p: Place, place: Place) -> str:
    """The departure city by its city code when it has one; the place of the trip by its airport."""
    return _iata(p) if p is place else CITY_IATA.get(p["name"]) or _iata(p)


def _zone(iata: str, p: Place) -> ZoneInfo:
    return ZoneInfo(airport_tz().get(iata) or (p.get("airport") or {}).get("tz") or p["tz"])


def _read(path: Path) -> list[dict] | None:
    """Rows of a cached answer; None when the file is broken."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))["data"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("travelpayouts cache %s: %s", path.name, e)
        return None


class TravelpayoutsTransport:
    """Flights from Travelpayouts for the chosen place; trains, cars and fallback synthetic."""

    def __init__(self, token: str, fetch: Fetch | None = None, cache_dir: Path = CACHE_DIR):
        self.token = token
        self.fetch = fetch or self._get
        self.cache_dir = cache_dir
        self.failed_at = -float("inf")

    def legs(self, spec: TripSpec, origin: Place, place: Place) -> list[TransportOffer]:
        legs = SyntheticTransport().legs(spec, origin, place)
        chosen = {_norm((catalog.resolve(d) or {"name": d})["name"]) for d in spec.destinations}
        if not legs or _norm(place["name"]) not in chosen:  # no legs: no date or no way
            return legs
        there, back = _dates(spec)
        out: list[TransportOffer] = []
        for a, b, day in ((origin, place, there), (place, origin, back)):
            mine = [x for x in legs if x.origin == a["name"]]
            flights = [x for x in mine if x.mode == "flight"]
            real = self._flights(spec, a, b, day, place) if flights else []
            if real:
                mine = real + [x for x in mine if x.mode != "flight"]
            out += mine
        return out

    def _flights(
        self, spec: TripSpec, a: Place, b: Place, day: date, place: Place
    ) -> list[TransportOffer]:
        """Real flights a -> b on `day`; [] means the synthetic ones stay."""
        src, dst = (_code(p, place) for p in (a, b))
        if not src or not dst:
            return []
        rows = self._answer(src, dst, day)
        if rows is None:
            return []
        try:
            legs = [self._leg(spec, a, b, r) for r in rows]
        except (KeyError, TypeError, ValueError) as e:
            log.warning("travelpayouts %s-%s %s: bad answer (%s), synthetic", src, dst, day, e)
            return []
        if a is place:  # back: not before check-out, the ride and the margin (#370, TRAP-5)
            earliest = _back_from(place, "flight", day)
            legs = [x for x in legs if x.depart_at >= earliest]
        else:  # there: on the check-in day, not after it (TRAP-5)
            legs = [x for x in legs if x.arrive_at.astimezone(ZoneInfo(place["tz"])).date() <= day]
        if not legs:
            log.warning("travelpayouts %s-%s %s: no flight, synthetic", src, dst, day)
        return legs

    def _answer(self, src: str, dst: str, day: date) -> list[dict] | None:
        """Rows of the answer: the fresh cache, the API, a stale cache; None when none of them."""
        path = self.cache_dir / f"{src}-{dst}-{day.isoformat()}.json"
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_S:
            return _read(path)
        got = None
        if time.monotonic() - self.failed_at >= FAILED_S:
            try:
                got = self.fetch(src, dst, day)
            except (OSError, ValueError) as e:  # URLError, timeout, HTTP error, bad JSON
                self.failed_at = time.monotonic()
                log.warning("travelpayouts %s-%s %s: %s", src, dst, day, e)
        rows = got.get("data") if isinstance(got, dict) and got.get("success") else None
        if isinstance(rows, list):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
                tmp.replace(path)
            except OSError as e:  # a read-only disk: the answer still serves
                log.warning("travelpayouts cache %s: %s", path.name, e)
            return rows
        if got is not None:
            log.warning("travelpayouts %s-%s %s: refused: %s", src, dst, day, got)
        return _read(path) if path.exists() else None

    def _get(self, src: str, dst: str, day: date) -> dict:
        ask = {"origin": src, "destination": dst, "departure_at": day.isoformat(),
               "one_way": "true", "currency": "rub", "sorting": "price", "limit": 5}  # fmt: skip
        query = urllib.parse.urlencode(ask)
        req = urllib.request.Request(f"{URL}?{query}", headers={"X-Access-Token": self.token})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read())

    def _leg(self, spec: TripSpec, a: Place, b: Place, row: dict) -> TransportOffer:
        src, dst = row["origin_airport"], row["destination_airport"]
        depart = datetime.fromisoformat(row["departure_at"])
        if depart.tzinfo is None:
            raise ValueError(f"departure_at without a zone: {row['departure_at']}")
        depart = depart.astimezone(_zone(src, a))
        flight = timedelta(minutes=int(row["duration_to"]))
        arrive = _shift(depart, flight).astimezone(_zone(dst, b))
        changes = int(row["transfers"])
        title = f"Перелёт {a['name']} → {b['name']} ({dst}), {depart:%d.%m %H:%M}"
        title += f", рейс {row['airline']} {row['flight_number']}"
        if changes:
            title += f", пересадок: {changes}"
        if seats := _seats_label(spec, "flight"):
            title += f", {seats}"
        if any(_child_fare("flight", age)[0] != 1 for age in spec.children_ages):
            title += ", детский тариф — оценка"  # a share of the adult price, not the API (TRAP-7)
        return TransportOffer(
            offer_id=f"t-{a['slug']}-{b['slug']}-flight-{depart:%Y%m%d%H%M}-{_guests_tag(spec)}-tp",
            title=title,
            mode="flight",
            origin=a["name"],
            destination=b["name"],
            depart_at=depart,
            arrive_at=arrive,
            changes=changes,
            price_rub=round(float(row["price"]) * _fares(spec, "flight")),
            synthetic=False,
        )
