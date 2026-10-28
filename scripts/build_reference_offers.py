#!/usr/bin/env python3
"""Reference of real dated hotel offers for the agent check (#263).

    python3 scripts/build_reference_offers.py

Reads data/real/offers_{booking,agoda}_*.json (written by collect_hotel_offers.py, #331) and
writes eval/reference_offers.json. For each request it keeps 4 offers: the Booking offer at
the median night price of 3, 4 and 5 stars, and the Agoda offer at the median night price (no
stars). `market` holds the numbers of all Booking offers of the place and stay by stars.
The file has no hotel names, ids, urls or photos (the decision on named raw data is open).
Prices stay in the site currency: Booking in RUB, Agoda in EUR; no exchange rate is applied.
Standard library only.
"""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "data" / "real"
OUT = ROOT / "eval" / "reference_offers.json"

NOV = ("2026-11-14", "2026-11-18")
NEW_YEAR = ("2026-12-28", "2027-01-04")
# id, case of eval/cases.json it comes from, message, city of the agent, Booking place, Agoda place.
# The cities of the cases (Sochi, Kazan, Kaliningrad) have no collected offers: the requests keep
# the idea of the case (S1 warm sea, S2 Antalya, S5 Istanbul and Georgia) with a collected city and stay.
REQUESTS = [
    ("R1", "S2", "Анталья, 14-18 ноября, 2 взрослых, из Москвы", "Анталия", "Анталья", "antalya-tr", NOV),
    ("R2", "S1", "Хургада, 14-18 ноября, 2 взрослых, из Москвы", "Хургада", "Хургада", "hurghada-eg", NOV),
    ("R3", "S5", "Стамбул, 14-18 ноября, 2 взрослых, из Москвы", "Стамбул", "Стамбул", "istanbul-tr", NOV),
    ("R4", "S5", "Тбилиси, 28 декабря - 4 января, 2 взрослых, из Москвы", "Тбилиси", "Тбилиси", "tbilisi-ge", NEW_YEAR),
    ("R5", "S1", "Бангкок, 14-18 ноября, 2 взрослых, из Москвы", "Бангкок", "Бангкок", "bangkok-th", NOV),
    ("R6", "S1", "Пхукет, 28 декабря - 4 января, 2 взрослых, из Москвы", "Пхукет", "Пхукет", "phuket-th", NEW_YEAR),
]  # fmt: skip
STARS = (3, 4, 5)


def load(source: str, place: str, stay: tuple[str, str]) -> tuple[dict, str]:
    name = f"offers_{source}_{place.replace('-', '_') if source == 'agoda' else place}_{stay[0]}_{stay[1]}.json"
    path = FACTS / name
    collected_at = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
    return json.loads(path.read_text("utf-8")), collected_at


def median_offer(offers: list[dict]) -> dict:
    """The offer nearest to the median night price (the cheaper one on a tie)."""
    mid = statistics.median(o["night_price"] for o in offers)
    return min(offers, key=lambda o: (abs(o["night_price"] - mid), o["night_price"]))


def offer_row(rid: str, city: str, summary: dict, o: dict, collected_at: str) -> dict:
    row = {
        "request": rid,
        "city": city,
        "check_in": summary["check_in"],
        "check_out": summary["check_out"],
        "nights": summary["nights"],
        "adults": summary["adults"],
        "children": summary["children"],
        "stars": o["stars"],
        "night_price": round(o["night_price"]),
        "total_price": round(o["total_price"]),
        "currency": o["currency"],
        "meal": "BB" if o["breakfast"] else "RO",
        "free_cancel": bool(o["free_cancel"]),
        "source": o["source"],
        "collected_at": collected_at,
    }
    if o.get("free_cancel_until"):
        row["free_cancel_until"] = o["free_cancel_until"]
    return row


def market_of(offers: list[dict]) -> dict:
    market = {}
    for stars in sorted({o["stars"] for o in offers if o["stars"]}):
        group = [o for o in offers if o["stars"] == stars]
        nights = sorted(o["night_price"] for o in group)
        q = (
            statistics.quantiles(nights, n=4, method="inclusive")
            if len(nights) > 1
            else [nights[0]] * 3
        )
        market[str(stars)] = {
            "n": len(group),
            "min": round(nights[0]),
            "p25": round(q[0]),
            "median": round(q[1]),
            "p75": round(q[2]),
            "max": round(nights[-1]),
            "free_cancel_share": round(sum(bool(o["free_cancel"]) for o in group) / len(group), 2),
            "breakfast_share": round(sum(bool(o["breakfast"]) for o in group) / len(group), 2),
        }
    return market


def main() -> None:
    requests, offers_out = [], []
    for rid, case, message, city, booking_place, agoda_place, stay in REQUESTS:
        booking, b_at = load("booking", booking_place, stay)
        agoda, a_at = load("agoda", agoda_place, stay)
        summary = booking["summary"]
        nights = (date.fromisoformat(stay[1]) - date.fromisoformat(stay[0])).days
        requests.append(
            {
                "id": rid,
                "case": case,
                "message": message,
                "city": city,
                "check_in": stay[0],
                "check_out": stay[1],
                "nights": nights,
                "adults": summary["adults"],
                "market_currency": "RUB",
                "market": market_of(booking["offers"]),
            }
        )
        for stars in STARS:
            group = [o for o in booking["offers"] if o["stars"] == stars]
            offers_out.append(offer_row(rid, city, summary, median_offer(group), b_at))
        offers_out.append(
            offer_row(rid, city, agoda["summary"], median_offer(agoda["offers"]), a_at)
        )
    doc = {
        "about": "Real dated hotel offers (Booking RUB, Agoda EUR) for the check of the agent, #263. "
        "No names, ids, urls. `market`: night price of all Booking offers of the stay by stars.",
        "requests": requests,
        "offers": offers_out,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(requests)} requests, {len(offers_out)} offers")


if __name__ == "__main__":
    main()
