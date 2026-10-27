"""Calibration of catalog.json levels from 101hotels facts (#119), scripts/calibrate_prices.py."""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "scripts" / "calibrate_prices.py"
_spec = importlib.util.spec_from_file_location("calibrate_prices", _path)
cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal)


def test_level_is_the_101hotels_median_only_with_five_hotels():
    places = [
        {"name": "Сочи", "country": "Россия", "level": 5000},
        {"name": "Кунгур", "country": "Россия", "level": 4000},
        {"name": "Пхукет", "country": "Таиланд", "level": 4500},
    ]
    facts = {
        "Сочи": {"n": 20, "from_median_rub": 3049, "free_cancel_share": 0.85},
        "Кунгур": {"n": 4, "from_median_rub": 2048, "free_cancel_share": 0.5},
        "Пхукет": {"n": 20, "from_median_rub": 900, "free_cancel_share": 0.1},
    }
    assert cal.calibrate(places, facts) == [("Сочи", 5000, 3000)]
    assert places == [
        {"name": "Сочи", "country": "Россия", "level": 3000, "free_cancel_share": 0.85},
        {"name": "Кунгур", "country": "Россия", "level": 4000},
        {"name": "Пхукет", "country": "Таиланд", "level": 4500},
    ]
