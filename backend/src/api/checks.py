"""Проверки сценариев для make eval / make eval-final (scripts/eval_harness.py).

check_<id сценария>(case) -> bool | (bool, заметка). Без проверки — «не автоматизирован».
metric(split) -> {"name", "system", "baseline"} или None.
"""

from api.contracts import PredictRequest
from api.model import predict


def check_EXAMPLE(case: dict) -> tuple[bool, str]:
    p = predict(PredictRequest(text=case.get("input", "")))
    return bool(p.explanation), f"label={p.label}"


def metric(split: str) -> dict | None:
    # TODO(kickoff): метрика жюри против baseline из eval/baseline.json.
    return None
