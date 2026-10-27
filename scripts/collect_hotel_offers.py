#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["curl_cffi", "playwright"]
# ///
"""Dated hotel offers from WAF-protected sites: «browser enters, curl_cffi collects» (#119).
Dev tool, not part of the image. Methods and limits: research/data-sources.md.

    uv run scripts/collect_hotel_offers.py booking Анталья 2026-12-10 2026-12-14 --adults 2
    uv run scripts/collect_hotel_offers.py agoda antalya-tr 2026-12-10 2026-12-14 --hotels 6

Flow. A real Brave (Playwright, CDP) opens one page and passes the WAF:
  booking: cookie `aws-waf-token` → plain curl_cffi (Chrome TLS) GET of the search page;
  agoda:   the page itself calls POST /api/v1/property/room-grid → we copy its headers and body,
           then per hotel: curl_cffi GET of the hotel page (property id) + POST room-grid.
The browser is needed once per session, not once per request.

Rules as in collect_prices.py: robots.txt (Google semantics) before every path, one request at a
time, PAUSE_S between requests, stop after FAILS_MAX failures in a row or at --budget requests.
Parsed offers keep only facts: price, dates, stars, guest score, meal, cancellation. The raw pages
and JSON saved beside them DO hold hotel names, descriptions and photo links: they stay in
data/real/ (outside git) and never go to git or tests/fixtures/. stdout is an aggregate.
The browser visits obey the same rules: robots.txt first, pause, budget.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import html
import json
import re
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_prices import Refused, allowed

BRAVE = "/usr/bin/brave"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "real"
PAUSE_S = 3.0
FAILS_MAX = 3
LANG = {"Accept-Language": "ru-RU,ru;q=0.9"}
MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}  # fmt: skip


class Disallowed(Refused):
    """robots.txt closes this one path: skip it, the run goes on."""


_state = {"last": -1e9, "requests": 0, "fails": 0, "budget": 40}
_robots: dict[str, str] = {}
_session = None


def session():
    global _session
    if _session is None:
        from curl_cffi import requests

        _session = requests.Session(impersonate="chrome")
    return _session


def pace() -> None:
    """Budget and pause for one request, whether curl_cffi or the browser makes it."""
    if _state["requests"] >= _state["budget"]:
        raise Refused(f"бюджет {_state['budget']} запросов исчерпан")
    wait = PAUSE_S - (time.monotonic() - _state["last"])
    if wait > 0:
        time.sleep(wait)


def check_robots(host: str, path: str) -> None:
    """robots.txt is read once per host. A non-200 answer (a WAF challenge, a block) stops the run:
    an HTML page would otherwise read as «everything allowed»."""
    if host not in _robots:
        _robots[host] = ""  # robots.txt itself is not checked against robots.txt
        r = get(host, "/robots.txt")
        if r.status_code != 200:
            del _robots[host]
            raise Refused(f"{host}/robots.txt: HTTP {r.status_code}")
        _robots[host] = r.text
    if not allowed(_robots[host], path):
        raise Disallowed(f"robots.txt запрещает {host}{path}")


def get(host: str, path: str, *, method="GET", headers=None, data=None):
    """curl_cffi request. Every call is checked against robots.txt and counts against the budget."""
    if path != "/robots.txt":
        check_robots(host, path)
    pace()
    r = session().request(
        method,
        f"https://{host}{path}",
        headers=headers,
        data=data,
        timeout=45,
        allow_redirects=True,
    )
    _state["last"] = time.monotonic()
    _state["requests"] += 1
    _state["fails"] = 0 if r.status_code == 200 else _state["fails"] + 1
    if _state["fails"] >= FAILS_MAX:
        raise Refused(f"{FAILS_MAX} отказа подряд (последний: {host}{path} → {r.status_code})")
    return r


def browser_visit(url: str, *, ready: str, links: str | None = None, capture: str | None = None):
    """Open `url` in Brave. Returns cookies, the first request whose URL contains `capture`
    (headers, body) and the hrefs of `links`. `ready` is a selector or a request-URL part to wait for."""
    from playwright.sync_api import sync_playwright

    parts = urlsplit(url)
    check_robots(parts.netloc, f"{parts.path}?{parts.query}" if parts.query else parts.path)
    pace()
    seen: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=BRAVE,
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(locale="ru-RU", viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        if capture:

            def on_request(req):
                if capture in req.url and req.method == "POST" and "req" not in seen:
                    seen["req"] = {"headers": req.headers, "body": req.post_data}

            page.on("request", on_request)
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        status = resp.status if resp else 0
        with contextlib.suppress(Exception):  # a WAF challenge page has no such element
            page.wait_for_selector(ready, timeout=25000)
        for _ in range(6):  # lazy blocks (room grid) load on scroll
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(900)
        hrefs = page.eval_on_selector_all(links, "els => els.map(e => e.href)") if links else []
        cookies = ctx.cookies()
        browser.close()
    _state["last"] = time.monotonic()
    _state["requests"] += 1
    return status, cookies, seen.get("req"), hrefs


def load_cookies(cookies: list[dict]) -> None:
    for c in cookies:
        session().cookies.set(c["name"], c["value"], domain=c["domain"].lstrip("."), path=c["path"])


def text_of(fragment: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " "),
    ).strip()


def stat(rows: list[dict], nights: int) -> dict:
    prices = [r["night_price"] for r in rows if r.get("night_price")]
    return {
        "offers": len(rows),
        "min_night": round(min(prices)) if prices else None,
        "median_night": round(statistics.median(prices)) if prices else None,
        "free_cancel_share": round(sum(bool(r["free_cancel"]) for r in rows) / len(rows), 2)
        if rows
        else None,
        "breakfast_share": round(sum(bool(r["breakfast"]) for r in rows) / len(rows), 2)
        if rows
        else None,
    }


# --- booking.com -------------------------------------------------------------------------------


ORDERS = ["popularity", "price", "review_score_and_price", "class", "class_asc"]


def booking_url(city: str, d_in: date, d_out: date, adults: int, children: int, order: str) -> str:
    """The static page holds 20 cards and ignores `offset`: other sort orders give other cards."""
    return (
        f"/searchresults.ru.html?ss={quote(city)}&checkin={d_in}&checkout={d_out}"
        f"&group_adults={adults}&group_children={children}&no_rooms=1&selected_currency=RUB&order={order}"
    )


def parse_booking(page: str, nights: int) -> list[dict]:
    """One dict per property card. The price is for the whole stay, for the guests asked."""
    rows = []
    for card in page.split('data-testid="property-card"')[1:]:
        card = card.split('data-testid="property-card"')[0]
        price = re.search(
            r'data-testid="price-and-discounted-price"[^>]*>(.*?)</span>',
            card,
            re.DOTALL,
        )
        if not price:
            continue
        digits = re.sub(r"\D", "", text_of(price.group(1)))
        if not digits:
            continue
        txt = text_of(card)
        stars = re.search(r"Оценка объекта:\s*(\d)", html.unescape(card))
        link = re.search(r'href="(https://www\.booking\.com/hotel/[^"?]+)', card)
        if not link:
            continue
        score = re.search(r"Оценка[: ]+(\d+[,.]\d)", txt)
        rows.append(
            {
                "source": "booking",
                "id": hashlib.sha1(link.group(1).encode()).hexdigest()[:10],
                "total_price": int(digits),
                "currency": "RUB",
                "night_price": round(int(digits) / nights),
                "stars": int(stars.group(1)) if stars else None,
                "score": float(score.group(1).replace(",", ".")) if score else None,
                "free_cancel": "Бесплатная отмена" in txt,
                "breakfast": bool(re.search(r"Завтрак\s+включ", txt)),
            }
        )
    return rows


def run_booking(args, d_in: date, d_out: date, nights: int) -> list[dict]:
    path = booking_url(args.place, d_in, d_out, args.adults, args.children, ORDERS[0])
    for attempt in (1, 2):  # the WAF challenge sometimes wins the first try
        status, cookies, _, _ = browser_visit(
            f"https://www.booking.com{path}", ready='[data-testid="property-card"]'
        )
        load_cookies(cookies)
        if any(c["name"] == "aws-waf-token" for c in cookies):
            break
        print(f"  вход {attempt}: HTTP {status}, токена WAF нет")
        time.sleep(PAUSE_S)
    else:
        raise Refused("Booking: токен WAF не получен за 2 попытки")
    rows: dict[str, dict] = {}
    for order in ORDERS[: args.pages]:
        r = get(
            "www.booking.com",
            booking_url(args.place, d_in, d_out, args.adults, args.children, order),
            headers=LANG,
        )
        if r.status_code != 200:
            print(f"  order={order}: HTTP {r.status_code}")
            break
        (OUT / "booking").mkdir(parents=True, exist_ok=True)
        (OUT / "booking" / f"raw_{args.place}_{d_in}_{d_out}_{order}.html").write_text(
            r.text, "utf-8"
        )
        page_rows = parse_booking(r.text, nights)
        new = [x for x in page_rows if x["id"] not in rows]
        rows.update({x["id"]: x for x in new})
        print(f"  order={order}: {len(page_rows)} карточек, новых {len(new)}")
    return list(rows.values())


# --- agoda.com ---------------------------------------------------------------------------------


def agoda_date(text: str) -> str | None:
    """«7 дек. 26 г.» → 2026-12-07."""
    m = re.match(r"(\d{1,2})\s+([а-я]+)\.?\s+(\d{2})\b", text)
    if not m or m.group(2)[:3] not in MONTHS:
        return None
    return f"20{m.group(3)}-{MONTHS[m.group(2)[:3]]:02d}-{int(m.group(1)):02d}"


def find_key(obj, key: str):
    """First value of `key` anywhere inside obj."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            hit = find_key(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = find_key(v, key)
            if hit is not None:
                return hit
    return None


def parse_agoda(grid: dict, nights: int, stars: int | None) -> list[dict]:
    """One dict per rate. Agoda shows the price per night with taxes, in the site currency."""
    rows = []
    for room in grid.get("rooms", []):
        for offer in room.get("offers", []):
            final = (offer.get("price") or {}).get("final") or {}
            if not final.get("amountNumber"):
                continue
            free = re.search(
                r"Бесплатная отмена до ([^\"]+?г\.)",
                json.dumps(offer, ensure_ascii=False),
            )
            rows.append(
                {
                    "source": "agoda",
                    "night_price": final["amountNumber"],
                    "total_price": round(final["amountNumber"] * nights),
                    "currency": {"€": "EUR", "$": "USD", "₽": "RUB"}.get(
                        final.get("currency"), final.get("currency")
                    ),
                    "stars": stars,
                    "free_cancel": bool(find_key(offer, "isFreeCancellation")),
                    "free_cancel_until": agoda_date(free.group(1)) if free else None,
                    "breakfast": bool(find_key(offer, "isBreakfastIncluded")),
                }
            )
    return rows


def run_agoda(args, d_in: date, d_out: date, nights: int) -> list[dict]:
    host = "www.agoda.com"
    query = f"checkIn={d_in}&los={nights}&adults={args.adults}&rooms=1"
    status, _, _, hrefs = browser_visit(
        f"https://{host}/ru-ru/city/{args.place}.html",
        ready='a[href*="/hotel/"]',
        links='a[href*="/hotel/"]',
    )
    slugs = []
    for h in dict.fromkeys(h.split(host, 1)[1] for h in hrefs if "/hotel/" in h):
        try:
            check_robots(host, f"{h.split('?')[0]}?{query}")
        except Disallowed as e:
            print(f"  пропуск: {e}")
            continue
        slugs.append(h)
    slugs = slugs[: args.hotels]
    if not slugs:
        raise Refused(f"Agoda: на странице города нет отелей (HTTP {status})")
    print(f"  отелей на странице города: {len(slugs)}")
    first = f"{slugs[0].split('?')[0]}?{query}"
    status, cookies, req, _ = browser_visit(
        f"https://{host}{first}",
        ready="[data-selenium='room-grid-table'], #roomGrid",
        capture="/room-grid",
    )
    if not req:
        raise Refused(f"Agoda: страница не вызвала room-grid (HTTP {status})")
    load_cookies(cookies)
    template = json.loads(req["body"])
    skip = {
        "cookie",
        "host",
        "content-length",
        "accept-encoding",
        "connection",
        "user-agent",
    }
    headers = {
        k: v
        for k, v in req["headers"].items()
        if k.lower() not in skip and not k.startswith("sec-")
    }
    rows: list[dict] = []
    (OUT / "agoda").mkdir(parents=True, exist_ok=True)
    for slug in slugs:
        try:
            page = get(host, f"{slug.split('?')[0]}?{query}", headers=LANG)
        except Disallowed as e:
            print(f"  пропуск: {e}")
            continue
        pid = re.search(r"hotel_id=(\d+)", page.text)
        if page.status_code != 200 or not pid:
            print(f"  {slug[:40]}: нет id (HTTP {page.status_code})")
            continue
        body = copy.deepcopy(template)
        body["propertyId"] = int(pid.group(1))
        body["searchCriteria"].update(checkIn=str(d_in), checkOut=str(d_out), adults=args.adults)
        r = get(
            host,
            "/api/v1/property/room-grid",
            method="POST",
            headers=headers,
            data=json.dumps(body),
        )
        if r.status_code != 200:
            print(f"  {pid.group(1)}: room-grid HTTP {r.status_code}")
            continue
        (OUT / "agoda" / f"raw_{pid.group(1)}_{d_in}_{d_out}.json").write_text(r.text, "utf-8")
        grid = r.json()
        got = parse_agoda(grid, nights, None)  # stars are not in the room-grid answer
        rows += got
        print(
            f"  {pid.group(1)}: {len(got)} тарифов{' (распродан)' if grid.get('isSoldOut') else ''}"
        )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", choices=["booking", "agoda"])
    ap.add_argument(
        "place",
        help="booking: название места; agoda: часть URL города, например antalya-tr",
    )
    ap.add_argument("check_in")
    ap.add_argument("check_out")
    ap.add_argument("--adults", type=int, default=2)
    ap.add_argument("--children", type=int, default=0, help="только booking")
    ap.add_argument("--hotels", type=int, default=6, help="только agoda")
    ap.add_argument(
        "--pages",
        type=int,
        default=3,
        help="только booking: сколько порядков сортировки (по 20 карточек)",
    )
    ap.add_argument("--budget", type=int, default=40)
    args = ap.parse_args()
    _state["budget"] = args.budget
    d_in, d_out = date.fromisoformat(args.check_in), date.fromisoformat(args.check_out)
    nights = (d_out - d_in).days
    rows = (run_booking if args.source == "booking" else run_agoda)(args, d_in, d_out, nights)
    summary = {
        "source": args.source,
        "place": args.place,
        "check_in": args.check_in,
        "check_out": args.check_out,
        "adults": args.adults,
        "children": args.children,
        "nights": nights,
        "currency": sorted({r["currency"] for r in rows}),
        "requests": _state["requests"],
        **stat(rows, nights),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    place = re.sub(r"\W+", "_", args.place)
    name = f"offers_{args.source}_{place}_{args.check_in}_{args.check_out}.json"
    (OUT / name).write_text(
        json.dumps({"summary": summary, "offers": rows}, ensure_ascii=False, indent=1),
        "utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if rows else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refused as e:
        print(f"СТОП: {e}", file=sys.stderr)
        raise SystemExit(2) from e
