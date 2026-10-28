"""Live hotel price level for a Russian place from the 101hotels city page, at request time (#511).

The synthetic offer machine (offers._hotel) keeps building the tariffs; this adapter only replaces
`place["level"]` and `place["free_cancel_share"]` with the live city aggregate — the same rule as
the #119/#497 calibration, but read now instead of from a snapshot:

    level = median «от N ₽» of the 3-star hotels / (STAR_FACTOR[3] * SEASON[month of the fetch])

so offers._night_factor re-applies the season of the trip dates on top. Only numbers are read: no
hotel names, descriptions or photos (rules 6.5(4), 6.9). The shown per-hotel price stays modelled
and keeps its «синтетика» marker (TRAP-7); what is live is the price LEVEL of the city.

Honest degradation (TRAP-1, TRAP-6): a non-Russian place, a missing slug, no network, a refusal or
too few 3-star hotels fall back to the base provider (synthetic), never an error. The first network
failure turns live off for the process, so an offline demo stays fast; a live level already written
to data/live/ (outside git) is still served offline. Standard library only.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

from api import offers
from api.contracts import ExtraOffer, HotelOffer, Place, TripSpec

log = logging.getLogger(__name__)

HOST = "101hotels.com"
ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "data" / "live" / "101hotels"  # outside git (D-004)
UA = "Mozilla/5.0 (compatible; nedovost-live/0.1; +https://github.com/ostrovok-hackathon-2026/nedovost-org)"
PAUSE_S = 3.0
TIMEOUT_S = 8
MIN_N = 3  # a median of fewer than this many 3-star hotels is not stable enough to calibrate
REFUSAL = ("вы не робот", "проверяем, что вы не бот")
CHALLENGE_MAX = 50_000

# 101hotels slugs that a plain transliteration misses (from collect_prices.py SLUG_101, #119).
SLUG_101 = {
    "Великий Устюг": "velikii_ustyug",
    "Витязево": "vitiazevo",
    "Выборг": "viborg",
    "Геленджик": "gelendjik",
    "Дивеево": "diveevo",
    "Йошкар-Ола": "yoshkar_ola",
    "Санкт-Петербург": "saint_petersburg",
    "Сергиев Посад": "sergiev_posad",
    "Старая Русса": "staraya_russa",
    "Нижний Новгород": "nizhny_novgorod",
    "Великий Новгород": "velikiy_novgorod",
}
_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
     "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))  # fmt: skip


def translit(name: str) -> str:
    """«Сочи» → «sochi»; spaces become dashes (collect_prices.py)."""
    return "".join(_TR.get(c, c) for c in name.lower()).replace(" ", "-")


def slug_of(place: Place) -> str:
    return SLUG_101.get(place["name"]) or translit(place["name"])


# --- robots.txt (Google semantics: * and $, longest rule wins, Allow wins a tie; collect_prices.py) ---


def _groups(text: str) -> dict[str, list[tuple[bool, str]]]:
    groups: dict[str, list[tuple[bool, str]]] = {}
    agents: list[str] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(value.lower())
            groups.setdefault(value.lower(), [])
        elif key in ("allow", "disallow") and agents:
            in_rules = True
            for agent in agents:
                groups[agent].append((key == "allow", value))
    return groups


def allowed(robots_txt: str, path: str) -> bool:
    """May we fetch `path`? The «*» group; Google longest-match semantics."""
    rules = _groups(robots_txt).get("*", [])
    best_len, verdict = -1, True
    for is_allow, pattern in rules:
        if pattern == "":
            continue
        anchored = pattern.endswith("$")
        body = re.escape(pattern.rstrip("$")).replace(r"\*", ".*") + ("$" if anchored else "")
        if re.match(body, path) and (
            len(pattern) > best_len or (len(pattern) == best_len and is_allow)
        ):
            best_len, verdict = len(pattern), is_allow
    return verdict


def refused(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in REFUSAL) or (len(text) < CHALLENGE_MAX and "captcha" in low)


def parse_city(html: str) -> list[dict]:
    """(from_rub, stars, free_cancel) per hotel card of a 101hotels city page; cards without a
    RUB price are skipped. Only numbers (collect_prices.py parse_101hotels, trimmed)."""
    out = []
    for card in re.split(
        r'<li class="item[^"]*"\s+itemscope\s+itemtype="https://schema.org/Hotel">', html
    )[1:]:
        card = card.split("</article>", 1)[0]
        price = re.search(r'data-price-currency="(\w+)" data-price-value="(\d+)"', card)
        if not price or price.group(1) != "RUB":
            continue
        stars = re.search(r'class="stars stars_(\d)"', card)
        out.append(
            {
                "from_rub": int(price.group(2)),
                "stars": int(stars.group(1)) if stars else None,
                "free_cancel": "Бесплатная отмена" in card,
            }
        )
    return out


class Refused(RuntimeError):
    """The host refused us, or the network failed: turn live off for the process."""


_robots: dict[str, str] = {}
_last = {"t": -1e9}
_mem: dict[tuple[str, int], dict] = {}  # (slug, month) -> aggregate; the process cache
_offline = {"on": False}  # True after the first network failure: skip live, stay fast


def _get(path: str) -> str:
    wait = PAUSE_S - (time.monotonic() - _last["t"])
    if wait > 0:
        time.sleep(wait)
    try:
        with urllib.request.urlopen(
            urllib.request.Request(f"https://{HOST}{path}", headers={"User-Agent": UA}),
            timeout=TIMEOUT_S,
        ) as r:
            status, text = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, ""
    except OSError as e:
        raise Refused(f"{HOST}{path}: сеть ({e!r})") from e
    finally:
        _last["t"] = time.monotonic()
    if status != 200 or refused(text):
        raise Refused(f"{HOST}{path}: HTTP {status}, отказ хоста")
    return text


def _robots_ok(path: str) -> bool:
    if HOST not in _robots:
        try:
            self_path = "/robots.txt"
            _robots[HOST] = _get(self_path)
        except Refused:
            _robots[HOST] = (
                ""  # no robots.txt read -> treat as allowed, but the fetch may still fail
            )
    return allowed(_robots[HOST], path)


def _level(median3: int, month: int) -> int:
    return round(median3 / (offers.STAR_FACTOR[3] * offers.SEASON[month]))


def _aggregate(cards: list[dict], month: int) -> dict | None:
    three = [c["from_rub"] for c in cards if c["stars"] == 3]
    upper = [c for c in cards if (c["stars"] or 0) >= 3]
    if len(three) < MIN_N:
        return None
    return {
        "level": _level(round(statistics.median(three)), month),
        "free_cancel_share": round(sum(c["free_cancel"] for c in upper) / len(upper), 2),
        "n3": len(three),
        "median3_rub": round(statistics.median(three)),
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _load(slug: str, month: int) -> dict | None:
    path = CACHE / f"{slug}-{month:02d}.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _store(slug: str, month: int, agg: dict) -> None:
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        (CACHE / f"{slug}-{month:02d}.json").write_text(
            json.dumps(agg, ensure_ascii=False), "utf-8"
        )
    except OSError:
        pass  # a read-only data/ must not break a request


def city_level(place: Place, when: date) -> dict | None:
    """Live 101hotels aggregate for a Russian place, or None (fall back). Month of `when` drives
    both the cache key and the season divisor: the city «от N» reflects the day of the fetch."""
    if place.get("country") != "Россия":
        return None
    slug, month = slug_of(place), when.month
    if (slug, month) in _mem:
        return _mem[(slug, month)]
    if disk := _load(slug, month):  # a snapshot serves even when offline
        _mem[(slug, month)] = disk
        return disk
    if _offline["on"]:
        return None
    path = f"/main/cities/{slug}"
    try:
        if not _robots_ok(path):
            return None
        agg = _aggregate(parse_city(_get(path)), month)
    except Refused:
        _offline["on"] = True  # one timeout, then every place falls back fast
        return None
    if agg is None:
        return None
    log.info(
        "live 101hotels %s: level=%s free_cancel=%s (median3=%s ₽, n3=%s)",
        slug, agg["level"], agg["free_cancel_share"], agg["median3_rub"], agg["n3"],
    )  # fmt: skip
    _mem[(slug, month)] = agg
    _store(slug, month, agg)
    return agg


class Live101Hotels:
    """The base provider, but a Russian place is re-priced from the live 101hotels city aggregate.

    Wraps any HotelProvider (offers.OsmOrSynthetic by default); on any miss or failure it calls the
    base unchanged, so the demo always answers (TRAP-1, TRAP-6)."""

    def __init__(self, base: object) -> None:
        self.base = base
        # No data_mode: the per-hotel price is still modelled around the live median, so /health
        # keeps «синтетика» (TRAP-7), like the real-flight adapter. A fetched level is logged.

    def hotels(
        self, spec: TripSpec, place: Place, now: datetime
    ) -> list[tuple[HotelOffer, list[ExtraOffer]]]:
        try:
            date_in, _ = offers._dates(spec)
            agg = city_level(place, date_in)
        except Exception:  # noqa: BLE001 — live pricing must never break a search
            agg = None
        if agg:
            place = {**place, "level": agg["level"], "free_cancel_share": agg["free_cancel_share"]}
        return self.base.hotels(spec, place, now)
