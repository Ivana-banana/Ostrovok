"""Calibration of catalog.json levels from the 101hotels aggregate (#119), scripts/calibrate_prices.py."""

import importlib.util
import json
from pathlib import Path

from api import catalog, offers

_path = Path(__file__).resolve().parents[2] / "scripts" / "calibrate_prices.py"
_spec = importlib.util.spec_from_file_location("calibrate_prices", _path)
cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal)


def row(city: str, n3: int, median3: int, share: float) -> dict:
    return {"city": city, "fetched_at": "2026-10-27", "n3": n3, "median3_rub": median3,
            "free_cancel_share3plus": share}  # fmt: skip


def test_level_from_the_3_star_median_only_with_five_3_star_hotels():
    places = [
        {"name": "Сочи", "country": "Россия", "level": 5000},
        {"name": "Звенигород", "country": "Россия", "level": 4000},
        {"name": "Пхукет", "country": "Таиланд", "level": 4500},
    ]
    rows = [row("Сочи", 6, 2888, 0.8), row("Звенигород", 1, 5400, 1.0), row("Пхукет", 9, 900, 0.1)]
    # 2888 / (0.95 * 0.95) = 3200: the synthetic October 3-star night is the median again
    assert cal.calibrate(places, rows) == [("Сочи", 5000, 3200)]
    assert places == [
        {"name": "Сочи", "country": "Россия", "level": 3200, "free_cancel_share": 0.8},
        {"name": "Звенигород", "country": "Россия", "level": 4000},
        {"name": "Пхукет", "country": "Таиланд", "level": 4500},
    ]


def test_divisor_matches_the_offers_factors():
    assert cal.STAR3 == offers.STAR_FACTOR[3]
    assert all(cal.SEASON[m] == offers.SEASON[m] for m in cal.SEASON)


def test_catalog_levels_equal_the_committed_aggregate():
    """A full rebuild of catalog.json (build_catalog.py) writes the team levels back: rerun
    scripts/calibrate_prices.py then."""
    rows = json.loads(cal.AGGREGATE.read_text("utf-8"))
    by_name = {p["name"]: p for p in catalog.places() if p["country"] == "Россия"}
    calibrated = [r for r in rows if cal.level_of(r) is not None and r["city"] in by_name]
    assert calibrated
    for r in calibrated:
        p = by_name[r["city"]]
        assert (p["level"], p["free_cancel_share"]) == (
            cal.level_of(r),
            r["free_cancel_share3plus"],
        )
