"""Live 101hotels data for a Russian place at request time: the city price level (#511) and real
per-hotel offers (#528).

The city page is fetched once per process and parsed into cards. From the cards this module gives:
- the city price LEVEL, the #119/#497 calibration read now instead of from a snapshot —
      level = median «от N ₽» of the 3-star hotels / (STAR_FACTOR[3] * SEASON[month of the fetch])
  it is numbers only and may be cached to data/live/ (outside git);
- real per-hotel offers: the real name and «от N ₽» drive offers._hotel with synthetic=False; the
  stay total, meals and free-cancel deadline stay modelled from that real price (REAL_NOTE). Names
  live in process memory only, never on disk or in git (rules 6.5(4), 6.9).

Honest degradation (TRAP-1, TRAP-6): a non-Russian place, a missing slug, no network, a refusal or
too few named cards fall back to the base provider (synthetic), never an error. The first network
failure (OSError, timeout; robots.txt included) turns live off for the process, so an offline demo
stays fast: one TIMEOUT_S at most, no pause. A host refusal (HTTP 404 on a wrong slug, 403,
captcha) drops that slug only (#533). PAUSE_S separates two successful requests only. A live level
already written to data/live/ is still served offline, but real offers need a fresh fetch.
Standard library only.
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
PAUSE_S = 3.0  # between two successful requests to the host only
TIMEOUT_S = 3  # one offline wait stays under 4 s (TRAP-6, #533)
MIN_N = 3  # a median of fewer than this many 3-star hotels is not stable enough to calibrate
MIN_REAL = 3  # fewer named real cards than this: show the synthetic list instead, not a short one
REAL_NOTE = (
    "Отель и цена «от» — вживую с 101hotels. Итог за выбранные ночи, питание и срок бесплатной "
    "отмены — оценка от этой цены."
)
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


def _center_km(card: str) -> float | None:
    m = re.search(r"До центра\s+([\d.,]+)\s*(м|км)", card)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return round(value / 1000 if m.group(2) == "м" else value, 1)


def parse_city(html: str) -> list[dict]:
    """One dict per hotel card of a 101hotels city page; cards without a RUB price are skipped.

    Numbers (from_rub, stars, free_cancel, center_km, rating) feed the #511 city level, which is
    aggregated and may be cached to disk. Name and hotel_id serve the live per-hotel offers (#528);
    they stay in process memory and never reach disk or git (rules 6.5(4), 6.9)."""
    out = []
    for card in re.split(
        r'<li class="item[^"]*"\s+itemscope\s+itemtype="https://schema.org/Hotel">', html
    )[1:]:
        card = card.split("</article>", 1)[0]
        price = re.search(r'data-price-currency="(\w+)" data-price-value="(\d+)"', card)
        if not price or price.group(1) != "RUB":
            continue
        stars = re.search(r'class="stars stars_(\d)"', card)
        name = re.search(r'itemprop="name"[^>]*>\s*([^<]+?)\s*<', card)
        hotel_id = re.search(r'data-hotel-id="(\d+)"', card)
        rating = re.search(r'itemprop="ratingValue">\s*([\d.,]+)', card)
        out.append(
            {
                "from_rub": int(price.group(2)),
                "stars": int(stars.group(1)) if stars else None,
                "free_cancel": "Бесплатная отмена" in card,
                "center_km": _center_km(card),
                "rating": float(rating.group(1).replace(",", ".")) if rating else None,
                "name": name.group(1).strip() if name else None,
                "hotel_id": hotel_id.group(1) if hotel_id else None,
            }
        )
    return out


class Refused(RuntimeError):
    """The host refused this path (HTTP status other than 200, captcha): skip this slug only."""


class Offline(Refused):
    """The network failed (OSError, timeout): turn live off for the process."""


_robots: dict[str, str] = {}
_last = {"t": -1e9}  # time of the last successful request; failures do not count
_mem: dict[str, dict] = {}  # slug -> aggregate; the process cache
_cards: dict[str, list[dict] | None] = {}  # slug -> parsed cards (None: refused); names stay here
_offline = {"on": False}  # True after the first network failure: skip live, stay fast


def _now() -> datetime:
    """The fetch time; tests freeze it."""
    return datetime.now(UTC)


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
    except OSError as e:  # URLError, timeout, refused connection
        raise Offline(f"{HOST}{path}: сеть ({e!r})") from e
    if status != 200 or refused(text):
        raise Refused(f"{HOST}{path}: HTTP {status}, отказ хоста")
    _last["t"] = time.monotonic()
    return text


def _robots_ok(path: str) -> bool:
    """Offline propagates: the caller turns live off at once, without the city fetch."""
    if HOST not in _robots:
        try:
            _robots[HOST] = _get("/robots.txt")
        except Offline:
            raise
        except Refused as e:  # no robots.txt read -> treat as allowed; the fetch may still fail
            log.warning("live 101hotels robots.txt: %s; считаем путь разрешённым", e)
            _robots[HOST] = ""
    return allowed(_robots[HOST], path)


def _level(median3: int, month: int) -> int:
    """`month` is the month of the fetch: the «от N» on the page is the price of that day."""
    return round(median3 / (offers.STAR_FACTOR[3] * offers.SEASON[month]))


def _aggregate(cards: list[dict], month: int) -> dict | None:
    """`month` is the month of the fetch (see _level), not the month of the trip."""
    three = [c["from_rub"] for c in cards if c["stars"] == 3]
    upper = [c for c in cards if (c["stars"] or 0) >= 3]
    if len(three) < MIN_N:
        return None
    return {
        "level": _level(round(statistics.median(three)), month),
        "free_cancel_share": round(sum(c["free_cancel"] for c in upper) / len(upper), 2),
        "n3": len(three),
        "median3_rub": round(statistics.median(three)),
        "collected_at": _now().isoformat(timespec="seconds"),
    }


def _load(slug: str) -> dict | None:
    path = CACHE / f"{slug}.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _store(slug: str, agg: dict) -> None:
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        (CACHE / f"{slug}.json").write_text(json.dumps(agg, ensure_ascii=False), "utf-8")
    except OSError:
        pass  # a read-only data/ must not break a request


def _city_cards(place: Place) -> list[dict] | None:
    """Parsed 101hotels cards for a Russian place, fetched once per process and kept in `_cards`.
    None for a foreign place, a network failure or a host refusal; [] for a disallowed path. The
    page is «от N ₽» as of the fetch, so the trip month does not key it. A refusal (404 on a wrong
    slug) is cached for that slug only; a network failure turns live off for the process. Names
    never leave process memory."""
    if place.get("country") != "Россия":
        return None
    slug = slug_of(place)
    if slug in _cards:
        return _cards[slug]
    if _offline["on"]:
        return None
    path = f"/main/cities/{slug}"
    try:
        if not _robots_ok(path):
            _cards[slug] = []
            return []
        cards = parse_city(_get(path))
    except Offline as e:
        log.warning("live 101hotels выключен до перезапуска: %s", e)
        _offline["on"] = True  # one timeout, then every place falls back fast
        return None
    except Refused as e:
        log.warning("live 101hotels %s: %s; город на синтетике", slug, e)
        _cards[slug] = None
        return None
    _cards[slug] = cards
    return cards


def city_level(place: Place, when: date) -> dict | None:
    """Live 101hotels aggregate for a Russian place, or None (fall back). The city «от N» is the
    price of the fetch day, so the season divisor is SEASON[month of the fetch] and neither the
    cache key nor the level depends on `when` (#533); offers._hotel seasons the level by date."""
    if place.get("country") != "Россия":
        return None
    slug = slug_of(place)
    if slug in _mem:
        return _mem[slug]
    if disk := _load(slug):  # a snapshot serves even when offline
        _mem[slug] = disk
        return disk
    cards = _city_cards(place)
    if not cards:
        return None
    agg = _aggregate(cards, _now().month)
    if agg is None:
        return None
    log.info(
        "live 101hotels %s: level=%s free_cancel=%s (median3=%s ₽, n3=%s)",
        slug, agg["level"], agg["free_cancel_share"], agg["median3_rub"], agg["n3"],
    )  # fmt: skip
    _mem[slug] = agg
    _store(slug, agg)
    return agg


def _real_bases(place: Place, cards: list[dict]) -> list[dict]:
    """Bases for offers._hotel from real 101hotels cards: real name, «от N ₽», stars and free
    cancellation. The page has no coordinates, so the hotel sits at the city centre and amenities
    and pets stay empty (honest); `center_km` is the real distance when the card states it."""
    out = []
    for c in cards:
        if not c.get("name") or not c.get("hotel_id"):
            continue
        stars = c["stars"] if c["stars"] in offers.STAR_FACTOR else 3
        out.append(
            {
                "key": f"l{c['hotel_id']}",
                "name": c["name"],
                "title": c["name"],
                "stars": stars,
                "rating": c.get("rating"),
                "lat": place["lat"],
                "lon": place["lon"],
                "km": c.get("center_km"),
                "pets": False,
                "amenities": [],
                "note": REAL_NOTE,
                "night_rub": c["from_rub"],
                "free_cancel": c["free_cancel"],
            }
        )
    return out


class Live101Hotels:
    """Real 101hotels offers for a Russian place at request time, else the base provider.

    Online with at least MIN_REAL named cards: build offers from the real name and «от N ₽»
    (synthetic=False). Otherwise (foreign place, offline, a refusal or too few named cards) call
    the base provider unchanged — with the live city level fed in when it is known — so the demo
    always answers (TRAP-1, TRAP-6). The per-hotel stay total, meals and free-cancel deadline stay
    modelled from the real price, so each offer keeps the REAL_NOTE provenance line."""

    def __init__(self, base: object) -> None:
        self.base = base
        # No data_mode: the fallback list is still synthetic, so /health keeps «синтетика» (TRAP-7).

    def hotels(
        self, spec: TripSpec, place: Place, now: datetime
    ) -> list[tuple[HotelOffer, list[ExtraOffer]]]:
        try:
            date_in, _ = offers._dates(spec)
            agg = city_level(place, date_in)  # live or snapshot level; also warms _city_cards
            if agg:
                place = {
                    **place,
                    "level": agg["level"],
                    "free_cancel_share": agg["free_cancel_share"],
                }
            cards = _city_cards(place)
            bases = _real_bases(place, cards) if cards else []
        except Exception:  # live pricing must never break a search; the log says why
            log.warning(
                "live 101hotels %s: сбой слоя, отели на синтетике", place.get("name"), exc_info=True
            )
            bases = []
        if len(bases) >= MIN_REAL:
            return [pair for b in bases for pair in offers._hotel(spec, place, b, now)]
        return self.base.hotels(spec, place, now)
