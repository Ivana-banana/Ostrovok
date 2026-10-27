"""Aggregate of real hotel offers (#331): rows_of of scripts/aggregate_hotel_offers.py."""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "scripts" / "aggregate_hotel_offers.py"
_spec = importlib.util.spec_from_file_location("aggregate_hotel_offers", _path)
agg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agg)

SUMMARY = {
    "source": "booking",
    "place": "Анталья",
    "check_in": "2026-11-14",
    "check_out": "2026-11-18",
    "adults": 2,
    "currency": ["RUB"],
}


def offer(night, stars, *, free=False, breakfast=False):
    # `id` is in the real offers; it must not reach the aggregate
    return {
        "id": "abc",
        "night_price": night,
        "stars": stars,
        "free_cancel": free,
        "breakfast": breakfast,
    }


def test_one_row_per_stars_with_median_and_shares_and_no_ids():
    offers = [
        offer(1000, 3, free=True),
        offer(3000, 3),
        offer(5000, 3, breakfast=True),
        offer(8000, None),
    ]
    rows = agg.rows_of(SUMMARY, offers, "2026-10-27")
    by_stars = {r["stars"]: r for r in rows}
    assert by_stars[3]["n"] == 3
    assert by_stars[3]["min_night"] == 1000
    assert by_stars[3]["median_night"] == 3000
    assert by_stars[3]["free_cancel_share"] == 0.33
    assert by_stars[3]["breakfast_share"] == 0.33
    assert by_stars[None]["median_night"] == 8000
    assert all("id" not in r for r in rows)
