#!/usr/bin/env python3
"""Probe: hotel offers WITH DATES from the JSON call a 101hotels hotel page makes itself (#119).
Dev tool, not part of the image.

    python3 scripts/probe_hotel_dates.py sochi 2026-12-10 2026-12-14 --adults 2 --hotels 4

Flow (the browser flow, no captcha): city page (cookies + hotel ids) →
GET /public/booking/get_rooms_variants/<hotel_id>/<dd.mm.yyyy>/<dd.mm.yyyy> per hotel.
The answer is JSON whose `response.content` is an HTML table: one row per rate with the price
for the whole stay, the meal and the free-cancellation deadline.

Rules as in collect_prices.py: honest UA, robots.txt (Google semantics) checked before every
request, one request at a time, PAUSE_S between them, stop after 3 failures in a row or at
--budget requests. Only facts are kept: no hotel names, descriptions or photos. Raw answers go to
data/real/ (outside git); stdout is an aggregate. Standard library only.
"""

from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_prices import UA, Refused, allowed, refused

HOST = "101hotels.com"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "real" / "101hotels"
PAUSE_S = 3.0
FAILS_MAX = 3

_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))
_state = {"last": -1e9, "requests": 0, "fails": 0, "budget": 20}
_robots: list[str] = []


def get(path: str, *, ajax: bool = False, referer: str | None = None) -> tuple[int, str, float]:
    """(status, text, seconds). Every request counts against the budget."""
    if _state["requests"] >= _state["budget"]:
        raise Refused(f"бюджет {_state['budget']} запросов исчерпан")
    if _robots and not allowed(_robots[0], path):
        raise Refused(f"robots.txt запрещает {path}")
    wait = PAUSE_S - (time.monotonic() - _state["last"])
    if wait > 0:
        time.sleep(wait)
    headers = {"User-Agent": UA}
    if ajax:
        headers |= {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
    if referer:
        headers["Referer"] = referer
    started = time.monotonic()
    try:
        with _opener.open(
            urllib.request.Request(f"https://{HOST}{path}", headers=headers), timeout=40
        ) as r:
            status, text = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, ""
    except OSError as e:
        status, text = 0, repr(e)
    _state["last"] = time.monotonic()
    _state["requests"] += 1
    ok = status == 200 and not refused(text if not ajax else "")
    _state["fails"] = 0 if ok else _state["fails"] + 1
    if _state["fails"] >= FAILS_MAX:
        raise Refused(f"{FAILS_MAX} отказа подряд (последний: {path} → {status})")
    return status, text, time.monotonic() - started


def hotel_ids(city_html: str) -> list[tuple[int, int | None]]:
    """(hotel id, stars) for the cards of a city page."""
    cards = re.split(
        r'<li class="item[^"]*"\s+itemscope\s+itemtype="https://schema.org/Hotel">', city_html
    )[1:]
    out = []
    for card in cards:
        card = card.split("</article>", 1)[0]
        hid = re.search(r'hotel_id="(\d+)"', card) or re.search(r'data-hotel-id="(\d+)"', card)
        stars = re.search(r'class="stars stars_(\d)"', card)
        if hid:
            out.append((int(hid.group(1)), int(stars.group(1)) if stars else None))
    return out


def _text(s: str) -> str:
    s = html.unescape(html.unescape(s))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s).replace("\xa0", " ")).strip()


def meal_of(text: str) -> str:
    low = text.lower()
    if "без питания" in low:
        return "none"
    if "завтрак" in low and "обед" in low and "ужин" in low:
        return "full_board"
    if "завтрак" in low and ("ужин" in low or "обед" in low):
        return "half_board"
    if "завтрак" in low:
        return "breakfast"
    return "other"


def parse_offers(content: str) -> list[dict]:
    """One dict per rate row. `price_rub` is for the whole stay (checked: min / nights = the
    «от N» of the city page). Rows are cut at the meal cell; the price is in the next cell."""
    offers = []
    for part in content.split('<td class="meal-type-cell">')[1:]:
        meal_cell, _, rest = part.partition("room-price-cell")
        price = re.search(
            r'class="price-value"[^>]*data-price-value="(\d+)" data-price-currency="(\w+)"', rest
        ) or re.search(r'data-price-value="(\d+)"[^>]*data-price-currency="(\w+)"', rest)
        if not price:
            continue
        if price.group(2) != "RUB":
            raise ValueError(f"цена в {price.group(2)}, ждали RUB")
        policy = re.search(r'data-policy="([^"]*)"', meal_cell)
        policy_text = _text(policy.group(1)) if policy else ""
        until = re.search(
            r"Бесплатная отмена до (\d\d:\d\d:\d\d) ?MSK (\d\d)\.(\d\d)\.(\d{4})", policy_text
        )
        offers.append(
            {
                "price_rub": int(price.group(1)),
                "meal": meal_of(
                    _text(
                        re.sub(r'data-policy="[^"]*"', "", meal_cell.split("booking-guarantee")[0])
                    )
                ),
                "free_cancel_until": f"{until.group(4)}-{until.group(3)}-{until.group(2)}"
                if until
                else None,
                "prepay": "full"
                if "полной стоимости" in policy_text
                else "first_night"
                if "первых суток" in policy_text
                else None,
            }
        )
    return offers


def hotel_facts(offers: list[dict], nights: int) -> dict:
    prices = [o["price_rub"] for o in offers]
    return {
        "offers": len(offers),
        "min_night_rub": round(min(prices) / nights) if prices else None,
        "median_night_rub": round(statistics.median(prices) / nights) if prices else None,
        "meals": sorted({o["meal"] for o in offers}),
        "free_cancel_share": round(
            sum(o["free_cancel_until"] is not None for o in offers) / len(offers), 2
        )
        if offers
        else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("city_slug")
    ap.add_argument("check_in")
    ap.add_argument("check_out")
    ap.add_argument("--adults", type=int, default=2)
    ap.add_argument("--hotels", type=int, default=4)
    ap.add_argument("--budget", type=int, default=20)
    args = ap.parse_args()
    _state["budget"] = args.budget
    d_in, d_out = date.fromisoformat(args.check_in), date.fromisoformat(args.check_out)
    nights = (d_out - d_in).days
    fmt = lambda d: d.strftime("%d.%m.%Y")
    OUT.mkdir(parents=True, exist_ok=True)

    status, text, _ = get("/robots.txt")
    if status != 200:
        raise Refused(f"robots.txt: HTTP {status}")
    _robots.append(text)

    city_path = f"/main/cities/{args.city_slug}"
    status, city, t = get(city_path)
    if status != 200:
        raise Refused(f"{city_path}: HTTP {status}")
    cards = hotel_ids(city)[: args.hotels]
    print(f"город {args.city_slug}: карточек {len(cards)} (страница {t:.1f} с)")

    rows = []
    for hid, stars in cards:
        path = f"/public/booking/get_rooms_variants/{hid}/{fmt(d_in)}/{fmt(d_out)}?in={fmt(d_in)}&out={fmt(d_out)}&adults={args.adults}"
        status, body, t = get(path, ajax=True, referer=f"https://{HOST}{city_path}")
        if status != 200:
            print(f"  {hid}: HTTP {status}")
            continue
        data = json.loads(body)
        (OUT / f"{hid}_{args.check_in}_{args.check_out}.json").write_text(body, encoding="utf-8")
        resp = data["response"]
        offers = parse_offers(resp["content"]) if resp["available"] else []
        facts = {
            "hotel_id": hid,
            "stars": stars,
            "available": resp["available"],
            "seconds": round(t, 1),
            "bytes": len(body),
            **hotel_facts(offers, nights),
        }
        rows.append(facts)
        print(f"  {json.dumps(facts, ensure_ascii=False)}")

    summary = {
        "city": args.city_slug,
        "check_in": args.check_in,
        "check_out": args.check_out,
        "adults": args.adults,
        "nights": nights,
        "hotels_asked": len(cards),
        "hotels_with_offers": sum(1 for r in rows if r["offers"]),
        "requests": _state["requests"],
    }
    (OUT / f"summary_{args.city_slug}_{args.check_in}_{args.check_out}.json").write_text(
        json.dumps({"summary": summary, "hotels": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refused as e:
        print(f"СТОП: {e}", file=sys.stderr)
        raise SystemExit(2) from e
