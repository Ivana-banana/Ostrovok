#!/usr/bin/env python3
"""Calibrate the synthetic hotel prices of catalog.json from the 101hotels city aggregate (#119).

    python3 scripts/calibrate_prices.py aggregate --facts <data/prices>  # raw facts -> AGGREGATE
    python3 scripts/calibrate_prices.py                                   # AGGREGATE -> catalog.json

AGGREGATE (in git) has per city only numbers: hotels with a price (n), of them 3-star (n3), the
medians «от N ₽» of all, starred and 3-star hotels, the share of free cancellation among 3+ star hotels, the date and the
page. No hotel names or descriptions (rules 6.5(4), 6.9). The raw facts stay in data/ (outside git).

Rule: a Russian place with n3 >= MIN_N gets
- level = median3 / (STAR_FACTOR[3] * SEASON[month of the run]) of api/offers.py, rounded to
  100 ₽: so the synthetic 3-star night in the month of the run equals the 3-star median;
- free_cancel_share = the share of its 3+ star hotels with «Бесплатная отмена» (offers.py sells
  free cancellation only from 3 stars).
Other places keep their level (a team estimate) and get no share. The rule takes only the 3-star
median: the median of all objects or of the starred ones does not stand for 3 stars (the command
prints both ratios). Assumptions: «от N ₽» is the cheapest room of a hotel for two, without dates,
on the day of the run (late October, off season by the sea).

Run it again after scripts/build_catalog.py: the builder writes the team levels back;
backend/tests/test_calibrate_prices.py fails until then. Standard library only.
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
AGGREGATE = ROOT / "backend" / "src" / "api" / "prices_101hotels.json"
MIN_N = 5
STAR3 = 0.95  # api/offers.py STAR_FACTOR[3]; the test checks it
SEASON = {10: 0.95}  # api/offers.py SEASON for the months of the runs; the test checks it
SOURCE_RE = re.compile(r" Калибровка цен \(scripts/calibrate_prices\.py.*$")


def city_row(facts: dict, fetched_at: str) -> dict:
    """Aggregate of one 101hotels city page: numbers only."""
    hotels = facts["hotels"]
    three = sorted(h["from_rub"] for h in hotels if h["stars"] == 3)
    starred = [h["from_rub"] for h in hotels if h["stars"]]
    upper = [h for h in hotels if (h["stars"] or 0) >= 3]
    return {
        "city": facts["city"],
        "url": facts["source_url"],
        "fetched_at": fetched_at,
        "n": len(hotels),
        "n3": len(three),
        "median_rub": round(statistics.median(h["from_rub"] for h in hotels)),
        "median_starred_rub": round(statistics.median(starred)) if starred else None,
        "median3_rub": round(statistics.median(three)) if three else None,
        "n3plus": len(upper),
        "free_cancel_share3plus": (
            round(sum(h["free_cancel"] for h in upper) / len(upper), 2) if upper else None
        ),
    }


def level_of(row: dict) -> int | None:
    """Calibrated level of a city, None when it has fewer than MIN_N 3-star hotels."""
    if row["n3"] < MIN_N:
        return None
    month = int(row["fetched_at"][5:7])
    return int(round(row["median3_rub"] / (STAR3 * SEASON[month]), -2))


def calibrate(places: list[dict], rows: list[dict]) -> list[tuple[str, int, int]]:
    """Apply the rule in place; (name, old level, new level) of the calibrated places."""
    by_city = {r["city"]: r for r in rows}
    changed = []
    for p in places:
        row = by_city.get(p["name"])
        level = level_of(row) if row and p["country"] == "Россия" else None
        if level is None:
            continue
        old, p["level"] = p["level"], level
        p["free_cancel_share"] = row["free_cancel_share3plus"]
        changed.append((p["name"], old, level))
    return changed


def _q(xs: list[float]) -> str:
    q = statistics.quantiles(xs, n=10)
    return f"медиана {statistics.median(xs):.2f}, p10 {q[0]:.2f}, p90 {q[-1]:.2f} (мест {len(xs)})"


def aggregate(facts_dir: Path) -> None:
    when = {}
    for line in (facts_dir / "manifest.jsonl").open(encoding="utf-8"):
        m = json.loads(line)
        when[m["url"]] = m["fetched_at"][:10]  # the last fetch of a page wins
    rows = []
    for f in sorted((facts_dir / "101hotels").glob("*.json")):
        facts = json.loads(f.read_text("utf-8"))
        rows.append(city_row(facts, when[facts["source_url"]]))
    rows.sort(key=lambda r: r["city"])
    text = "[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n]\n"
    AGGREGATE.write_text(text, encoding="utf-8")
    proven = [r for r in rows if r["n3"] >= MIN_N]
    print(f"городов {len(rows)}, с 3★ ≥ {MIN_N}: {len(proven)}")
    print("медиана всех / медиана 3★:", _q([r["median_rub"] / r["median3_rub"] for r in proven]))
    print(
        "медиана со звёздами / медиана 3★:",
        _q([r["median_starred_rub"] / r["median3_rub"] for r in proven]),
    )


def apply() -> None:
    rows = json.loads(AGGREGATE.read_text("utf-8"))
    snap = json.loads(CATALOG.read_text("utf-8"))
    changed = calibrate(snap["places"], rows)
    days = sorted({r["fetched_at"] for r in rows if level_of(r) is not None})
    when = days[0] if len(days) == 1 else f"{days[0]}…{days[-1]}"
    snap["_source"] = SOURCE_RE.sub("", snap["_source"]) + (
        f" Калибровка цен (scripts/calibrate_prices.py, #119): у {len(changed)} мест России, где"
        f" на странице города 101hotels.com не меньше {MIN_N} отелей 3★ с ценой (снято {when}),"
        " level подобран так, что синтетическая ночь 3★ в месяц съёмки равна медиане «от N ₽»"
        " этих отелей (без дат; допущение — номер на двоих); free_cancel_share — доля отелей"
        " 3★+ с бесплатной отменой там же. Агрегат — prices_101hotels.json. Цены предложений"
        " остаются синтетикой."
    )
    write(CATALOG, snap)
    print(
        f"откалибровано мест: {len(changed)}, level изменён: {sum(o != n for _, o, n in changed)}"
    )
    if len(changed) > 1:
        print("новый level / прежний:", _q([n / o for _, o, n in changed]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", choices=["aggregate"])
    ap.add_argument("--facts", type=Path, default=ROOT / "data" / "prices")
    args = ap.parse_args()
    aggregate(args.facts) if args.cmd == "aggregate" else apply()
