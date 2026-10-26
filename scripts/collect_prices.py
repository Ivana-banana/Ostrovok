#!/usr/bin/env python3
"""Offline price collector for calibrating the synthetic offers (#119). Not part of the image.

    python3 scripts/collect_prices.py hotels Сочи Геленджик=gelendjik  # 101hotels.com city pages
    python3 scripts/collect_prices.py trains Москва:Санкт-Петербург  # tutu.ru route pages

Only facts are kept (price, stars, distances, free cancellation, rating, train times and fares):
no hotel names, descriptions, photos or reviews (rules 6.5(4), 6.9). Raw pages are cached in
data/prices/cache/, facts go to data/prices/<source>/ (data/ is outside git, D-004).

Politeness: an honest UA with a link to the repository; robots.txt (Google semantics, from rlt
src/sources/robots.py) is read for our UA and checked before every request; a robots.txt that is
not 200/404 stops the run. One request per host at a time with PAUSE_S between them. A refusal
(403, 429, captcha page, a redirect, a 200 page without the expected facts) stops the whole run:
the host is not retried and the page is not cached.
Provenance: every page gets a line in data/prices/manifest.jsonl (url, time, sha1, facts).
Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "prices"
PAUSE_S = 5.0
AGENT = "nedovost-price-probe"
UA = f"Mozilla/5.0 (compatible; {AGENT}/0.1; +https://github.com/ostrovok-hackathon-2026/nedovost-org)"
# Normal pages load captcha scripts too (101hotels: reCAPTCHA, SmartCaptcha), so the word counts
# only on a small page: challenge pages in the recon were 4-25 KB, city pages 500+ KB.
REFUSAL = ("вы не робот", "проверяем, что вы не бот")
CHALLENGE_MAX = 50_000


class Refused(RuntimeError):
    """The host refused us: stop, do not retry."""


# --- robots.txt (Google semantics: * and $ in paths, longest rule wins, Allow wins a tie) ---


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


def allowed(robots_txt: str, path: str, agent: str = AGENT) -> bool:
    """May `agent` fetch `path` (with the query string)? Its own group if any, else `*`."""
    groups = _groups(robots_txt)
    specific = [a for a in groups if a != "*" and agent.lower().startswith(a)]
    rules = groups[max(specific, key=len)] if specific else groups.get("*", [])
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


# --- polite fetch ---

_robots: dict[str, str] = {}
_last: dict[str, float] = {}


def _get(host: str, path: str) -> tuple[int, str, str]:
    """(status, final url, text); urlopen follows redirects, the caller compares the url."""
    wait = PAUSE_S - (time.monotonic() - _last.get(host, -1e9))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(f"https://{host}{path}", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.geturl(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.geturl(), ""
    finally:
        _last[host] = time.monotonic()


def refused(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in REFUSAL) or (len(text) < CHALLENGE_MAX and "captcha" in low)


def _robots_txt(host: str) -> str:
    if host not in _robots:
        status, _, text = _get(host, "/robots.txt")
        if status not in (200, 404):  # Google: 5xx means «all disallowed»; 403 here is a block
            raise Refused(f"{host}/robots.txt: HTTP {status} — останавливаюсь")
        _robots[host] = text
    return _robots[host]


def fetch[T](host: str, path: str, parse: Callable[[str], T]) -> T | None:
    """Facts parsed from the page, or None for 404. Raises Refused on a refusal, a robots ban,
    a redirect or a page without facts; only a page that gave facts is cached."""
    url = f"https://{host}{path}"
    cache = OUT / "cache" / host / (re.sub(r"[^\w.-]", "_", path.strip("/")) + ".html")
    if cache.exists():
        return parse(cache.read_text(encoding="utf-8"))
    if not allowed(_robots_txt(host), path):
        raise Refused(f"robots.txt запрещает {url}")
    status, final, text = _get(host, path)
    if status == 404:
        return None
    if final != url:
        raise Refused(f"{url}: редирект на {final} — останавливаюсь")
    if status != 200 or refused(text):
        raise Refused(f"{url}: HTTP {status}, отказ хоста — останавливаюсь")
    try:
        facts = parse(text)
    except ValueError as e:
        raise Refused(f"{url}: {e} — останавливаюсь") from e
    if not facts:
        raise Refused(f"{url}: HTTP 200 без фактов (вызов или новая разметка) — останавливаюсь")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    line = {
        "url": url,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ua": UA,
        "sha1": hashlib.sha1(text.encode()).hexdigest(),
        "facts": len(facts["trains"]) if isinstance(facts, dict) else len(facts),
    }
    with (OUT / "manifest.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return facts


# --- names → URL slugs ---

_TR = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        [
            "a",
            "b",
            "v",
            "g",
            "d",
            "e",
            "e",
            "zh",
            "z",
            "i",
            "y",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "r",
            "s",
            "t",
            "u",
            "f",
            "kh",
            "ts",
            "ch",
            "sh",
            "shch",
            "",
            "y",
            "",
            "e",
            "yu",
            "ya",
        ],
    )
)


def translit(name: str) -> str:
    """«Санкт-Петербург» → «sankt-peterburg»; spaces become dashes."""
    return "".join(_TR.get(c, c) for c in name.lower()).replace(" ", "-")


# --- parsers (pure: html → facts) ---


def _num(s: str) -> float:
    return float(s.replace(",", ".").replace("\xa0", "").replace(" ", ""))


def _km(text: str, label: str) -> float | None:
    m = re.search(label + r"[^<\d]*(\d[\d\s.,]*?)\s*(км|м)<", text)
    if not m:
        return None
    return round(_num(m.group(1)) / (1 if m.group(2) == "км" else 1000), 3)


def parse_101hotels(html: str) -> list[dict]:
    """One dict per hotel card of a 101hotels city page; cards without a price are skipped."""
    cards = re.split(
        r'<li class="item[^"]*"\s+itemscope\s+itemtype="https://schema.org/Hotel">', html
    )[1:]
    out = []
    for card in cards:
        card = card.split("</article>", 1)[0]  # the last card would run to the end of the page
        price = re.search(r'data-price-currency="(\w+)" data-price-value="(\d+)"', card)
        if not price:
            continue
        if price.group(1) != "RUB":
            raise ValueError(f"цена в {price.group(1)}, ждали RUB")
        stars = re.search(r'class="stars stars_(\d)"', card)
        rating = re.search(r'itemprop="ratingValue">([\d.,]+)', card)
        reviews = re.search(r'itemprop="reviewCount" content="(\d+)"', card)
        out.append(
            {
                "from_rub": int(price.group(2)),
                "stars": int(stars.group(1)) if stars else None,
                "center_km": _km(card, "До центра"),
                "sea_km": _km(card, "До (?:[А-ЯЁ][а-яё]+ )?(?:моря|океана|залива|пляжа)"),
                "free_cancel": "Бесплатная отмена" in card,
                "rating": _num(rating.group(1)) if rating else None,
                "reviews": int(reviews.group(1)) if reviews else 0,
            }
        )
    return out


def parse_tutu(html: str) -> dict | None:
    """Fares of a tutu.ru train route page from its schema.org Product/AggregateOffer block.
    Times are local station times; `arrive_next_day` only says arrive < depart — a train of two
    or more nights is not told apart, so the timeline (TRAP-5) must not take these times as is."""
    for block in re.findall(
        r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html, re.DOTALL
    ):
        data = json.loads(block)
        if data.get("@type") != "Product":
            continue
        agg = data["offers"]
        return {
            "low_rub": agg["lowPrice"],
            "high_rub": agg["highPrice"],
            "trains": [
                {
                    "number": o["itemOffered"].get("trainNumber"),
                    "depart": o["itemOffered"]["departureTime"][:5],
                    "arrive": o["itemOffered"]["arrivalTime"][:5],
                    "arrive_next_day": o["itemOffered"]["arrivalTime"]
                    < o["itemOffered"]["departureTime"],
                    "category": o.get("category"),
                    "price_rub": o["price"],
                }
                for o in agg.get("offers", [])
            ],
        }
    return None


def summary(hotels: list[dict]) -> dict:
    """City aggregate for the calibration. «From» = the cheapest room per night without dates,
    on the day of the run (season!), not a typical price."""
    prices = sorted(h["from_rub"] for h in hotels)
    q = statistics.quantiles(prices, n=4) if len(prices) > 1 else prices * 3
    return {
        "n": len(prices),
        "from_p25_rub": round(q[0]),
        "from_median_rub": round(q[1]),
        "from_p75_rub": round(q[2]),
        "free_cancel_share": round(sum(h["free_cancel"] for h in hotels) / len(hotels), 2),
    }


# --- commands ---


def _save(source: str, key: str, facts: object) -> None:
    path = OUT / source / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")


def hotels(cities: list[str]) -> None:
    for arg in cities:
        # 101hotels slugs are irregular (gelendjik, hanty-mansiysk): «Город=slug» overrides translit
        city, _, slug = arg.partition("=")
        path = f"/main/cities/{slug or translit(city)}"
        found = fetch("101hotels.com", path, parse_101hotels)
        if found is None:
            print(f"{city}: 404 — пропуск")
            continue
        facts = {"city": city, "source_url": f"https://101hotels.com{path}", "hotels": found}
        _save("101hotels", translit(city), {**facts, "summary": summary(found)})
        print(city, summary(found))


def trains(routes: list[str]) -> None:
    for route in routes:
        a, b = route.split(":")
        slug = "/".join(translit(x).title() for x in (a, b))
        path = f"/poezda/{slug}/"
        fares = fetch("www.tutu.ru", path, parse_tutu)
        if fares is None:
            print(f"{route}: 404 — пропуск")
            continue
        facts = {"route": [a, b], "source_url": f"https://www.tutu.ru{path}", **fares}
        _save("tutu", f"{translit(a)}--{translit(b)}", facts)
        print(route, fares["low_rub"], fares["high_rub"], len(fares["trains"]), "поездов")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:] or ["", ""]
    try:
        {"hotels": hotels, "trains": trains}[cmd](args)
    except Refused as e:
        sys.exit(f"СТОП: {e}")
