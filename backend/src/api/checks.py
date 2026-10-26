"""Проверки сценариев для make eval / make eval-final (scripts/eval_harness.py).

check_<id сценария>(case) -> bool | (bool, заметка). Без проверки — «не автоматизирован».
metric(split) -> {"name", "system", "baseline"} или None.
"""

from api.contracts import ChatRequest
from api.model import chat


def check_EXAMPLE(case: dict) -> tuple[bool, str]:
    r = chat(ChatRequest(message=case.get("input", "") or "-"))
    return bool(r.reply), f"stage={r.stage}"


def metric(split: str) -> dict | None:
    # TODO(kickoff): метрика жюри против baseline из eval/baseline.json.
    return None
