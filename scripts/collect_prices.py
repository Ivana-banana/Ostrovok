#!/usr/bin/env python3
"""Offline price collector for calibrating the synthetic offers (#119). Not part of the image.

    python3 scripts/collect_prices.py hotels Сочи Геленджик=gelendjik  # 101hotels.com city pages
    python3 scripts/collect_prices.py trains Москва:Санкт-Петербург  # tutu.ru route pages

Only facts are kept (price, stars, distances, free cancellation, rating, train times and fares):
no hotel names, descriptions, photos or reviews (rules 6.5(4), 6.9). Raw pages are cached in
data/prices/cache/, facts go to data/prices/<source>/ (data/ is outside git, D-004).

Politeness: robots.txt is checked before every request (Google semantics, from rlt
src/sources/robots.py); one request per host at a time with PAUSE_S between them; a refusal
(403, 429, captcha page) stops the whole run — the host is not retried.
Standard library only.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "prices"
PAUSE_S = 5.0
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
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


def allowed(robots_txt: str, path: str) -> bool:
    """May the `*` agent fetch `path` (with the query string)?"""
    best_len, verdict = -1, True
    for is_allow, pattern in _groups(robots_txt).get("*", []):
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


def _get(host: str, path: str) -> tuple[int, str]:
    wait = PAUSE_S - (time.monotonic() - _last.get(host, -1e9))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(f"https://{host}{path}", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    finally:
        _last[host] = time.monotonic()


def refused(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in REFUSAL) or (len(text) < CHALLENGE_MAX and "captcha" in low)


def fetch(host: str, path: str) -> str | None:
    """Page text, or None for 404. Cached on disk; raises Refused on a refusal or a robots ban."""
    cache = OUT / "cache" / host / (re.sub(r"[^\w.-]", "_", path.strip("/")) + ".html")
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    if host not in _robots:
        _robots[host] = _get(host, "/robots.txt")[1]
    if not allowed(_robots[host], path):
        raise Refused(f"robots.txt запрещает {host}{path}")
    status, text = _get(host, path)
    if status == 404:
        return None
    if status != 200 or refused(text):
        raise Refused(f"{host}{path}: HTTP {status}, отказ хоста — останавливаюсь")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


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
    m = re.search(label + r"[^<]*?\s([\d.,]+)\s*(км|м)<", text)
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
        price = re.search(r'data-price-value="(\d+)"', card)
        if not price:
            continue
        stars = re.search(r'class="stars stars_(\d)"', card)
        rating = re.search(r'itemprop="ratingValue">([\d.]+)', card)
        reviews = re.search(r'itemprop="reviewCount" content="(\d+)"', card)
        out.append(
            {
                "price_rub": int(price.group(1)),
                "stars": int(stars.group(1)) if stars else None,
                "center_km": _km(card, "До центра"),
                "sea_km": _km(card, "До (?:[А-ЯЁ][а-яё]+ )?(?:моря|океана|залива|пляжа)"),
                "free_cancel": "Бесплатная отмена" in card,
                "rating": float(rating.group(1)) if rating else None,
                "reviews": int(reviews.group(1)) if reviews else 0,
            }
        )
    return out


def parse_tutu(html: str) -> dict | None:
    """Fares of a tutu.ru train route page from its schema.org Product/AggregateOffer block."""
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
                    "category": o.get("category"),
                    "price_rub": o["price"],
                }
                for o in agg.get("offers", [])
            ],
        }
    return None


def summary(hotels: list[dict]) -> dict:
    """City aggregate that goes to the calibration: quartiles of the «from» price, shares."""
    prices = sorted(h["price_rub"] for h in hotels)
    q = statistics.quantiles(prices, n=4) if len(prices) > 1 else prices * 3
    return {
        "n": len(prices),
        "p25_rub": round(q[0]),
        "median_rub": round(q[1]),
        "p75_rub": round(q[2]),
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
        html = fetch("101hotels.com", f"/main/cities/{slug or translit(city)}")
        found = parse_101hotels(html) if html else []
        if not found:
            print(f"{city}: страницы нет или нет цен — пропуск")
            continue
        _save(
            "101hotels", translit(city), {"city": city, "hotels": found, "summary": summary(found)}
        )
        print(city, summary(found))


def trains(routes: list[str]) -> None:
    for route in routes:
        a, b = route.split(":")
        slug = "/".join(translit(x).title() for x in (a, b))
        html = fetch("www.tutu.ru", f"/poezda/{slug}/")
        fares = parse_tutu(html) if html else None
        if not fares:
            print(f"{route}: страницы нет или нет цен — пропуск")
            continue
        _save("tutu", f"{translit(a)}--{translit(b)}", {"route": [a, b], **fares})
        print(route, fares["low_rub"], fares["high_rub"], len(fares["trains"]), "поездов")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:] or ["", ""]
    try:
        {"hotels": hotels, "trains": trains}[cmd](args)
    except Refused as e:
        sys.exit(f"СТОП: {e}")
