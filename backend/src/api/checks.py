"""Проверки сценариев для make eval / make eval-final (scripts/eval_harness.py).

check_<id сценария>(case) -> bool | (bool, заметка). Без проверки — «не автоматизирован».
metric(split) -> {"name", "system", "baseline"} или None.
"""

import tempfile
from pathlib import Path

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore


def _store() -> SessionStore:
    return SessionStore(Path(tempfile.mkdtemp()) / "eval.db")


def check_S1(case: dict) -> tuple[bool, str]:
    """Item 1: ≤ 2 questions in at most one message before the first recommendation,
    or a recommendation at once with stated assumptions. Items 2–4 are not automated."""
    store = _store()
    r = chat(ChatRequest(message=case["input"].strip("«» ")), store)
    asked, messages = 0, 0
    for _ in range(3):
        if r.stage != "clarify":
            break
        asked, messages = asked + len(r.questions), messages + 1
        answer = " ".join(q.options[0] for q in r.questions if q.options) or "не знаю"
        r = chat(ChatRequest(session_id=r.session_id, message=answer), store)
    recommended = bool(r.plan.destinations or r.plan.offers)
    stated = asked > 0 or bool(r.plan.spec.assumptions)
    ok = recommended and stated and asked <= 2 and messages <= 1
    note = (
        f"п.1: вопросов {asked} в {messages} сообщ., допущений {len(r.plan.spec.assumptions)}, "
        f"направлений {len(r.plan.destinations)}; п.2–4 не автоматизированы"
    )
    return ok, note


def metric(split: str) -> dict | None:
    # TODO(kickoff): метрика жюри против baseline из eval/baseline.json.
    return None
