"""Проверки сценариев для make eval / make eval-final (scripts/eval_harness.py).

check_<id сценария>(case) -> bool | (bool, заметка); metric(split) -> dict | None.
Логика — app_core.core (без ROS). Прогон по bag — tools/bag_read.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "app_core"))

from app_core.core import Smoother


def check_EXAMPLE(case: dict) -> tuple[bool, str]:
    s = Smoother(0.5)
    return s.update(1.0) == 1.0, "заглушка"


def metric(split: str) -> dict | None:
    # TODO(kickoff): метрика жюри на bag сплита против baseline (eval/baseline.json).
    return None
