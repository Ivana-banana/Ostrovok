"""Calibration of catalog.json levels from the 101hotels aggregate (#119), scripts/calibrate_prices.py."""

import importlib.util
import json
import statistics
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


def test_foreign_rule_calibrates_abroad_and_skips_russia():
    places = [
        {"name": "Сочи", "country": "Россия", "level": 5000},
        {"name": "Пхукет", "country": "Таиланд", "level": 4500},
    ]
    rows = [row("Сочи", 6, 2888, 0.8), row("Пхукет", 9, 2394, 0.5) | {"fetched_at": "2026-11-14"}]
    # 2394 / (0.95 * 0.9 ноябрь) = 2800; Россия со своей строкой не трогается при foreign=True
    assert cal.calibrate(places, rows, foreign=True) == [("Пхукет", 4500, 2800)]
    assert places[0] == {"name": "Сочи", "country": "Россия", "level": 5000}
    assert places[1] == {"name": "Пхукет", "country": "Таиланд", "level": 2800,
                         "free_cancel_share": 0.5}  # fmt: skip


def test_foreign_rows_pick_november_and_weight_free_cancel(tmp_path, monkeypatch):
    def o(stars, month, n, med, fc):
        return {"source": "booking", "currency": "RUB", "stars": stars, "place": "Бали",
                "check_in": f"2026-{month}-14", "n": n, "median_night": med,
                "free_cancel_share": fc}  # fmt: skip

    offers = [
        o(3, "11", 10, 3000, 0.4),
        o(4, "11", 5, 7000, 1.0),  # ноябрьский прогон
        o(3, "12", 10, 9000, 0.9),  # декабрь: месяц вне SEASON, отбрасываем
        {
            "source": "agoda",
            "currency": "EUR",
            "stars": None,
            "place": "Бали",
            "check_in": "2026-11-14",
            "n": 8,
            "median_night": 50,
            "free_cancel_share": 1.0,
        },
    ]
    f = tmp_path / "prices_offers.json"
    f.write_text(json.dumps(offers), "utf-8")
    monkeypatch.setattr(cal, "FOREIGN", f)
    rows = cal.foreign_rows()
    assert len(rows) == 1
    r = rows[0]
    assert (r["fetched_at"], r["n3"], r["median3_rub"]) == ("2026-11-14", 10, 3000)
    assert r["free_cancel_share3plus"] == round((10 * 0.4 + 5 * 1.0) / 15, 2)  # 0.6


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


def test_route_row_keeps_numbers_only():
    facts = {"route": ["Москва", "Тверь"], "source_url": "https://www.tutu.ru/poezda/Moskva/Tver/",
             "low_rub": 500, "high_rub": 9000,
             "trains": [{"number": "1", "category": "Сидячий", "price_rub": p} for p in (900, 500, 700)]}  # fmt: skip
    assert cal.route_row(facts, "2026-10-27") == {
        "route": ["Москва", "Тверь"], "url": "https://www.tutu.ru/poezda/Moskva/Tver/",
        "fetched_at": "2026-10-27", "n": 3, "median_rub": 700, "low_rub": 500,
    }  # fmt: skip


def test_train_fares_match_the_tutu_medians():
    """#280: the synthetic full fare in the month of the run is the tutu median (±5 %) for the day
    and the overnight train; before the calibration it was 1.45x and 1.67x."""
    rows = json.loads(cal.TRAINS.read_text("utf-8"))
    for kind, ratios in cal.train_ratios(rows, offers.TRAIN_FARE).items():
        assert len(ratios) >= 20, kind
        assert 0.95 <= statistics.median(ratios) <= 1.05, kind
