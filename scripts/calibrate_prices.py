#!/usr/bin/env python3
"""Calibrate the synthetic hotel prices of catalog.json from the collected 101hotels facts (#119).

    python3 scripts/calibrate_prices.py [--facts data/prices]

Rule: a Russian place whose 101hotels city page gave n >= MIN_N hotels with a price gets
- level = the median of the hotels' «от N ₽» per night, rounded to 100 ₽;
- free_cancel_share = the share of those hotels with «Бесплатная отмена».
Other places keep their level (a team estimate) and get no share.

Why the median goes in as is: level is a night in a 3-star room for two. Over the 45 cities with
5+ three-star hotels the 3-star median equals the all-stars median (ratio 1.00), so the city
median stands for 3 stars. «От N ₽» is the cheapest room of a hotel without dates, on the day of
the run (late October, off season by the sea): it is lower than a dated price in season; offers.py
still applies its SEASON and weekend factors on top. No hotel names go to git: only the two
numbers per place and the source with the date in catalog.json `_source`.

Run it again after scripts/build_catalog.py: the builder writes the team levels back.
Reads data/prices/101hotels/*.json and manifest.jsonl (outside git). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_catalog import write  # one place a line, the format of the builder

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "backend" / "src" / "api" / "catalog.json"
MIN_N = 5
SOURCE_RE = re.compile(r" Калибровка цен \(scripts/calibrate_prices\.py.*$")


def calibrate(places: list[dict], summaries: dict[str, dict]) -> list[tuple[str, int, int]]:
    """Apply the rule in place; summaries: city name -> 101hotels summary. (name, old, new)."""
    changed = []
    for p in places:
        s = summaries.get(p["name"])
        if p["country"] != "Россия" or not s or s["n"] < MIN_N:
            continue
        old, p["level"] = p["level"], int(round(s["from_median_rub"], -2))
        p["free_cancel_share"] = s["free_cancel_share"]
        changed.append((p["name"], old, p["level"]))
    return changed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", type=Path, default=ROOT / "data" / "prices")
    args = ap.parse_args()
    summaries, urls = {}, set()
    for f in sorted((args.facts / "101hotels").glob("*.json")):
        d = json.loads(f.read_text("utf-8"))
        summaries[d["city"]] = d["summary"]
        urls.add(d["source_url"])
    days = sorted(
        {
            line["fetched_at"][:10]
            for line in map(json.loads, (args.facts / "manifest.jsonl").open(encoding="utf-8"))
            if line["url"] in urls
        }
    )
    snap = json.loads(CATALOG.read_text("utf-8"))
    changed = calibrate(snap["places"], summaries)
    when = days[0] if len(days) == 1 else f"{days[0]}…{days[-1]}"
    snap["_source"] = SOURCE_RE.sub("", snap["_source"]) + (
        f" Калибровка цен (scripts/calibrate_prices.py, #119): у {len(changed)} мест России level"
        " — медиана цен «от N ₽» за ночь без дат по отелям страницы города на 101hotels.com,"
        f" снято {when}, если отелей с ценой не меньше {MIN_N}; free_cancel_share — доля этих"
        " отелей с бесплатной отменой. Цены предложений остаются синтетикой."
    )
    write(CATALOG, snap)
    ratios = [new / old for _, old, new in changed]
    moved = sum(old != new for _, old, new in changed)
    print(f"мест с фактами (n ≥ {MIN_N}): {len(changed)}, level изменён: {moved}")
    if len(ratios) > 1:
        q = statistics.quantiles(ratios, n=10)
        print(
            f"медиана 101hotels / прежний level: медиана {statistics.median(ratios):.2f},"
            f" p10 {q[0]:.2f}, p90 {q[-1]:.2f}"
        )


if __name__ == "__main__":
    main()
