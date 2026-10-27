#!/usr/bin/env python3
"""Aggregate of the real hotel offers: median per place and stars (#331).

    python3 scripts/aggregate_hotel_offers.py

Reads data/real/offers_*.json (written by collect_hotel_offers.py) and writes
backend/src/api/prices_offers.json. A row holds numbers only: no hotel names, descriptions,
photos or ids (the jury allowed such aggregates in the repository, docs/questions-for-organizers.md).
Prices stay in the site currency: Booking in RUB, Agoda in EUR; no exchange rate is applied.
Agoda gives no stars, so its rows have `stars: null` (the whole place).
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "data" / "real"
AGGREGATE = ROOT / "backend" / "src" / "api" / "prices_offers.json"


def rows_of(summary: dict, offers: list[dict], collected_at: str) -> list[dict]:
    """One row per stars value of one place and one stay."""
    rows = []
    for stars in sorted({o["stars"] for o in offers}, key=lambda s: (s is None, s)):
        group = [o for o in offers if o["stars"] == stars and o.get("night_price")]
        if not group:
            continue
        nights = [o["night_price"] for o in group]
        rows.append(
            {
                "source": summary["source"],
                "place": summary["place"],
                "check_in": summary["check_in"],
                "check_out": summary["check_out"],
                "adults": summary["adults"],
                "currency": summary["currency"][0],
                "collected_at": collected_at,
                "stars": stars,
                "n": len(group),
                "min_night": round(min(nights)),
                "median_night": round(statistics.median(nights)),
                "free_cancel_share": round(
                    sum(bool(o["free_cancel"]) for o in group) / len(group), 2
                ),
                "breakfast_share": round(sum(bool(o["breakfast"]) for o in group) / len(group), 2),
            }
        )
    return rows


def main() -> None:
    rows = []
    for f in sorted(FACTS.glob("offers_*.json")):
        data = json.loads(f.read_text("utf-8"))
        collected_at = datetime.fromtimestamp(f.stat().st_mtime).date().isoformat()
        rows += rows_of(data["summary"], data["offers"], collected_at)
    rows.sort(key=lambda r: (r["source"], r["place"], r["stars"] is None, r["stars"] or 0))
    text = "[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n]\n"
    AGGREGATE.write_text(text, encoding="utf-8")
    print(f"строк {len(rows)} из {len(list(FACTS.glob('offers_*.json')))} файлов → {AGGREGATE}")


if __name__ == "__main__":
    main()
