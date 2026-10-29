#!/usr/bin/env python3
"""Reference of real dated hotel offers and real transport for the agent check (#263, #607).

    python3 scripts/build_reference_offers.py

Hotels. Reads data/real/offers_{booking,agoda}_*.json (written by collect_hotel_offers.py, #331) and
writes eval/reference_offers.json. For each request it keeps 4 offers: the Booking offer at
the median night price of 3, 4 and 5 stars, and the Agoda offer at the median night price (no
stars). `market` holds the numbers of all Booking offers of the place and stay by stars.
The file has no hotel names, ids, urls or photos (the decision on named raw data is open).
Prices stay in the site currency: Booking in RUB, Agoda in EUR; no exchange rate is applied.

Transport (#607). None of the 6 hotel requests has a collected train or flight route (they fly
abroad; the data covers Russian routes), so `transport_requests` are 3 transport-only requests
of cases S6, S7, S11 (Moscow to Kazan, Sochi, Saint Petersburg). `transport` has one row per leg:
- trains: data/prices/tutu/moskva--<city>.json (tutu.ru, 27.10). The price is the cheapest seat
  «от» of a train, not the price of a date, so `date` is null and `price_kind` is "from". The
  numbers are min / median / max over the distinct trains of the page (one price per train).
  The times are local station times and tell only «arrive < depart»: the duration is right for a
  ride under 24 hours, so it is given for Kazan and Saint Petersburg and null for Sochi.
- flights: data/travelpayouts/<from>-<to>-<date>.json (cached Aviasales answers, the cheapest
  ticket of the date for one adult). `price_kind` is "ticket"; a flight leg has n = 1.
A row has no train or flight number, airline, name, link or gate: route, date, mode, price,
duration, collected_at, source. The folder of the data is `DATA_DIR` (default `<repo>/data`).
Standard library only.
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
FACTS = DATA / "real"
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

# id, case, message, city, check-in, check-out, train routes (tutu slug), flight legs (from, to, date).
# The flight codes are Aviasales city codes. A train route has the duration only if every ride is
# under 24 hours (see the docstring).
TRANSPORT_REQUESTS = [
    ("T1", "S6", "Казань, 11-13 декабря, 2 взрослых, из Москвы", "Казань", "2026-12-11", "2026-12-13",
     [("kazan", "Москва", "Казань", True)],
     [("MOW", "KZN", "Москва", "Казань", "2026-12-11"), ("KZN", "MOW", "Казань", "Москва", "2026-12-13")]),
    ("T2", "S7", "Сочи, 14-19 ноября, 2 взрослых, из Москвы", "Сочи", "2026-11-14", "2026-11-19",
     [("sochi", "Москва", "Сочи", False)], []),
    ("T3", "S11", "Санкт-Петербург, 20-22 ноября, 2 взрослых, из Москвы", "Санкт-Петербург",
     "2026-11-20", "2026-11-22", [("sankt-peterburg", "Москва", "Санкт-Петербург", True)], []),
]  # fmt: skip


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


def spread(values: list[int]) -> dict:
    return {"min": min(values), "median": round(statistics.median(values)), "max": max(values)}


def minutes(clock: str) -> int:
    return int(clock[:2]) * 60 + int(clock[3:5])


def train_row(rid: str, slug: str, origin: str, destination: str, with_duration: bool) -> dict:
    path = DATA / "prices" / "tutu" / f"moskva--{slug}.json"
    trains = json.loads(path.read_text("utf-8"))["trains"]
    cheapest = {}  # a train has one row per car class; the key is its number and departure
    for t in trains:
        key = (t["number"], t["depart"])
        if key not in cheapest or t["price_rub"] < cheapest[key]["price_rub"]:
            cheapest[key] = t
    rides = list(cheapest.values())
    row = {
        "request": rid,
        "mode": "train",
        "origin": origin,
        "destination": destination,
        "date": None,
        "price_kind": "from",
        "n": len(rides),
        "price_rub": spread([t["price_rub"] for t in rides]),
        "duration_min": None,
        "transfers": 0,
        "source": "tutu",
        "collected_at": datetime.fromtimestamp(path.stat().st_mtime).date().isoformat(),
    }
    if with_duration:
        long = [
            (minutes(t["arrive"]) - minutes(t["depart"])) % 1440 for t in rides
        ]  # a ride under 24 hours; a ride under an hour does not exist on these routes
        row["duration_min"] = spread(long)
    return row


def flight_row(
    rid: str, code_from: str, code_to: str, origin: str, destination: str, day: str
) -> dict:
    path = DATA / "travelpayouts" / f"{code_from}-{code_to}-{day}.json"
    ticket = json.loads(path.read_text("utf-8"))["data"][0]
    assert ticket["departure_at"][:10] == day
    return {
        "request": rid,
        "mode": "flight",
        "origin": origin,
        "destination": destination,
        "date": day,
        "price_kind": "ticket",
        "n": 1,
        "price_rub": spread([ticket["price"]]),
        "duration_min": spread([ticket["duration_to"]]),
        "transfers": ticket["transfers"],
        "source": "travelpayouts",
        "collected_at": datetime.fromtimestamp(path.stat().st_mtime).date().isoformat(),
    }


def transport() -> tuple[list[dict], list[dict]]:
    requests, rows = [], []
    for rid, case, message, city, start, end, trains, flights in TRANSPORT_REQUESTS:
        requests.append(
            {
                "id": rid,
                "case": case,
                "message": message,
                "city": city,
                "origin": "Москва",
                "check_in": start,
                "check_out": end,
                "adults": 2,
            }
        )
        rows += [train_row(rid, *train) for train in trains]
        rows += [flight_row(rid, *flight) for flight in flights]
    return requests, rows


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
    transport_requests, transport_rows = transport()
    doc = {
        "about": "Real dated hotel offers (Booking RUB, Agoda EUR) and real transport for the check "
        "of the agent, #263, #607. No names, ids, urls, train or flight numbers. `market`: night "
        "price of all Booking offers of the stay by stars. `transport`: one row per leg, price in "
        "RUB for one adult; trains are tutu «от» prices (no date), flights are cached Aviasales tickets.",
        "requests": requests,
        "offers": offers_out,
        "transport_requests": transport_requests,
        "transport": transport_rows,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(
        f"{OUT.relative_to(ROOT)}: {len(requests)} requests, {len(offers_out)} offers, "
        f"{len(transport_rows)} transport legs"
    )


if __name__ == "__main__":
    main()
