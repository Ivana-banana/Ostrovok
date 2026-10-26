"""Проверки сценариев для make eval / make eval-final (scripts/eval_harness.py).

check_<id сценария>(case) -> bool | (bool, заметка). Сценарий без проверки — «не
автоматизирован». metric(split) -> {"name", "system", "baseline"} или None.
Holdout сюда не копируем: его кейсы открываются только в eval-final.
"""

from app.contracts import Request
from app.model import predict


def check_EXAMPLE(case: dict) -> tuple[bool, str]:
    p = predict(Request(text=case.get("input", "")))
    return bool(p.explanation), f"label={p.label}"


def metric(split: str) -> dict | None:
    # TODO(kickoff): метрика жюри на сплите split против baseline (app.baseline).
    return None
