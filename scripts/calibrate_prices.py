#!/usr/bin/env python3
"""Calibrate the synthetic hotel prices of catalog.json from the 101hotels city aggregate (#119).

    python3 scripts/calibrate_prices.py aggregate --facts <data/prices>  # raw facts -> AGGREGATE
    python3 scripts/calibrate_prices.py                                   # AGGREGATE -> catalog.json
    python3 scripts/calibrate_prices.py trains --facts <data/prices>     # tutu facts -> TRAINS, fit

TRAINS (#280, in git): per route from Moscow only numbers: tutu.ru offers (n), the median and the
lowest fare of all classes, the date and the page. `trains` prints the fit of api/offers.py
TRAIN_FARE (base + per km of route at SEASON 1.0: least squares, scaled to the median ratio 1) and the ratio synthetic /
tutu before and after; the constants are set by hand from it, the test checks the ratio.
Assumptions: a tutu fare is the cheapest seat of a train and class on the day of the run, without
dates; the synthetic train has no class, so it matches the median of all classes.

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
FOREIGN = ROOT / "backend" / "src" / "api" / "prices_offers.json"  # Booking/Agoda, #497
TRAINS = ROOT / "backend" / "src" / "api" / "prices_tutu.json"  # tutu.ru, #280
MIN_N = 5
STAR3 = 0.95  # api/offers.py STAR_FACTOR[3]; the test checks it
SEASON = {10: 0.95, 11: 0.9}  # api/offers.py SEASON for the months of the runs; the test checks it
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


def foreign_rows() -> list[dict]:
    """Aggregate of Booking offers per foreign place: the 3-star median night and the free
    cancellation of 3+ star rows, numbers only (prices_offers.json, #497). Booking prices are in
    roubles for the dates of a run; the same shape and rule as city_row/level_of. A place has a
    run per month (November, December); only a month SEASON knows is kept, and of those the
    earliest run wins, so the result is deterministic. Agoda rows (EUR) are skipped: no star
    breakdown and no committed rate, so they do not calibrate."""
    offers = json.loads(FOREIGN.read_text("utf-8"))
    by_run: dict[tuple[str, str], dict[int, dict]] = {}  # (place, check_in) -> stars -> row
    for o in offers:
        if o["source"] != "booking" or o["currency"] != "RUB" or o.get("stars") is None:
            continue
        if int(o["check_in"][5:7]) not in SEASON:
            continue
        by_run.setdefault((o["place"], o["check_in"]), {})[o["stars"]] = o
    best: dict[str, dict] = {}  # place -> earliest run with a 3-star row
    for (place, check_in), stars in by_run.items():
        if 3 not in stars or (place in best and best[place]["fetched_at"] <= check_in):
            continue
        upper = [s for s in stars.values() if s["stars"] >= 3]
        n_upper = sum(s["n"] for s in upper)
        best[place] = {
            "city": place,
            "fetched_at": check_in,
            "n3": stars[3]["n"],
            "median3_rub": stars[3]["median_night"],
            "free_cancel_share3plus": (
                round(sum(s["n"] * s["free_cancel_share"] for s in upper) / n_upper, 2)
                if n_upper
                else None
            ),
        }
    return sorted(best.values(), key=lambda r: r["city"])


def level_of(row: dict) -> int | None:
    """Calibrated level of a city, None when it has fewer than MIN_N 3-star hotels."""
    if row["n3"] < MIN_N:
        return None
    month = int(row["fetched_at"][5:7])
    return int(round(row["median3_rub"] / (STAR3 * SEASON[month]), -2))


def calibrate(
    places: list[dict], rows: list[dict], *, foreign: bool = False
) -> list[tuple[str, int, int]]:
    """Apply the rule in place; (name, old level, new level) of the calibrated places. foreign:
    calibrate places outside Russia (Booking rows), else places in Russia (101hotels rows)."""
    by_city = {r["city"]: r for r in rows}
    changed = []
    for p in places:
        if (p["country"] != "Россия") != foreign:
            continue
        row = by_city.get(p["name"])
        level = level_of(row) if row else None
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
    foreign = foreign_rows()
    snap = json.loads(CATALOG.read_text("utf-8"))
    ru = calibrate(snap["places"], rows)
    fr = calibrate(snap["places"], foreign, foreign=True)
    changed = ru + fr
    days = sorted({r["fetched_at"] for r in rows if level_of(r) is not None})
    when = days[0] if len(days) == 1 else f"{days[0]}…{days[-1]}"
    fdays = sorted({r["fetched_at"] for r in foreign if level_of(r) is not None})
    fwhen = fdays[0] if len(fdays) == 1 else f"{fdays[0]}…{fdays[-1]}"
    snap["_source"] = SOURCE_RE.sub("", snap["_source"]) + (
        f" Калибровка цен (scripts/calibrate_prices.py, #119, #497): у {len(ru)} мест России, где"
        f" на странице города 101hotels.com не меньше {MIN_N} отелей 3★ с ценой (снято {when}), и"
        f" у {len(fr)} мест за рубежом, где агрегат Booking.com даёт не меньше {MIN_N} отелей 3★"
        f" (снято {fwhen}), level подобран так, что синтетическая ночь 3★ в месяц съёмки равна"
        " медиане этих отелей (без дат; допущение — номер на двоих); free_cancel_share — доля"
        " отелей 3★+ с бесплатной отменой там же. Агрегаты — prices_101hotels.json и"
        " prices_offers.json. Цены предложений остаются синтетикой."
    )
    write(CATALOG, snap)
    print(f"откалибровано: Россия {len(ru)}, зарубежье {len(fr)}; всего {len(changed)}")
    print(f"level изменён: {sum(o != n for _, o, n in changed)}")
    if len(changed) > 1:
        print("новый level / прежний:", _q([n / o for _, o, n in changed if o]))


def _when(facts_dir: Path) -> dict[str, str]:
    when = {}
    for line in (facts_dir / "manifest.jsonl").open(encoding="utf-8"):
        m = json.loads(line)
        when[m["url"]] = m["fetched_at"][:10]  # the last fetch of a page wins
    return when


def route_row(facts: dict, fetched_at: str) -> dict:
    """Aggregate of one tutu.ru route page: numbers only."""
    fares = [t["price_rub"] for t in facts["trains"]]
    return {
        "route": facts["route"],
        "url": facts["source_url"],
        "fetched_at": fetched_at,
        "n": len(fares),
        "median_rub": round(statistics.median(fares)),
        "low_rub": min(fares),
    }


def train_kinds(row: dict) -> list[str]:
    """Kinds of the synthetic trains api/offers.py runs on this route (transport_offers)."""
    from api import catalog as cat, offers

    a, b = (cat.resolve(n) for n in row["route"])
    if not a or not b or a["station_km"] is None or b["station_km"] is None:
        return []
    route = offers.km_between(a, b) * offers.RAIL_DETOUR
    if route > 3000:
        return []
    day = offers._day_train_hours(a, b, route) is not None
    return [k for k, on in (("day", day), ("night", route >= 600)) if on]


def train_ratios(rows: list[dict], fare: dict) -> dict[str, list[float]]:
    """Synthetic full fare in the month of the run / tutu median, per kind of train."""
    from api import catalog as cat, offers

    out: dict[str, list[float]] = {"day": [], "night": []}
    for r in rows:
        a, b = (cat.resolve(n) for n in r["route"])
        for kind in train_kinds(r):
            route = offers.km_between(a, b) * offers.RAIL_DETOUR
            fixed, per_km = fare[kind]
            month = int(r["fetched_at"][5:7])
            out[kind].append((fixed + per_km * route) * offers.SEASON[month] / r["median_rub"])
    return out


def trains(facts_dir: Path) -> None:
    sys.path.insert(0, str(ROOT / "backend" / "src"))
    from api import catalog as cat, offers

    when = _when(facts_dir)
    rows = []
    for f in sorted((facts_dir / "tutu").glob("*.json")):
        facts = json.loads(f.read_text("utf-8"))
        if facts["trains"]:
            rows.append(route_row(facts, when[facts["source_url"]]))
    rows.sort(key=lambda r: r["route"])
    text = "[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n]\n"
    TRAINS.write_text(text, encoding="utf-8")
    print(f"маршрутов {len(rows)}")
    fit = {}
    for kind in ("day", "night"):
        xs, ys = [], []
        for r in rows:
            if kind in train_kinds(r):
                a, b = (cat.resolve(n) for n in r["route"])
                xs.append(offers.km_between(a, b) * offers.RAIL_DETOUR)
                ys.append(r["median_rub"] / offers.SEASON[int(r["fetched_at"][5:7])])
        lr = statistics.linear_regression(xs, ys)
        # least squares gives the shape; the scale makes the median ratio 1
        k = statistics.median(y / (lr.intercept + lr.slope * x) for x, y in zip(xs, ys))
        fit[kind] = (round(k * lr.intercept / 50) * 50, round(k * lr.slope, 1))
        print(f"{kind}: маршрутов {len(xs)}, МНК {lr.intercept:.0f} + {lr.slope:.3f}·км,"
              f" масштаб {k:.3f} -> {fit[kind]}")  # fmt: skip
    for name, fare in (("TRAIN_FARE", offers.TRAIN_FARE), ("подбор", fit)):
        for kind, xs in train_ratios(rows, fare).items():
            print(f"{name} {kind}: синтетика / tutu — {_q(xs)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", choices=["aggregate", "trains"])
    ap.add_argument("--facts", type=Path, default=ROOT / "data" / "prices")
    args = ap.parse_args()
    if args.cmd == "aggregate":
        aggregate(args.facts)
    elif args.cmd == "trains":
        trains(args.facts)
    else:
        apply()
