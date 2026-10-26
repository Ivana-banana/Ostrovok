"""Проверки сценариев для make eval / make eval-final (scripts/eval_harness.py).

check_<id сценария>(case) -> bool | (bool, заметка). Без проверки — «не автоматизирован».
metric(split) -> {"name", "system", "baseline"} или None.
"""

import tempfile
from pathlib import Path

from api.contracts import ChatReply, ChatRequest
from api.fixtures import WARM_WATER_C
from api.model import chat
from api.store import SessionStore

MAX_FLIGHT_H = 5.0  # S1: "перелёт дольше 5 ч"


def _store() -> SessionStore:
    return SessionStore(Path(tempfile.mkdtemp()) / "eval.db")


def _s1_items(r: ChatReply, asked: int, messages: int) -> dict[str, bool]:
    """Pass items of S1 from eval/cases.json, checked on plan fields."""
    dests = r.plan.destinations
    complete = all(
        d.air_c is not None
        and d.water_c is not None
        and d.travel_hours > 0
        and d.est_total_rub > 0
        and d.why
        and d.tradeoffs
        for d in dests
    )
    bad = [d for d in dests if d.travel_hours > MAX_FLIGHT_H or (d.water_c or 0) < WARM_WATER_C]
    stated = asked > 0 or bool(r.plan.spec.assumptions)
    return {
        "1": bool(dests) and stated and asked <= 2 and messages <= 1,
        "2": 2 <= len(dests) <= 4 and complete,
        "3": all(d.violates for d in bad),
        "4": all(d.synthetic for d in dests) and "синтетика" in r.reply,
    }


def check_S1(case: dict) -> tuple[bool, str]:
    """All 4 pass items; climate is an estimate until T12 (#20), the check sees fields only."""
    store = _store()
    r = chat(ChatRequest(message=case["input"].strip("«» ")), store)
    asked, messages = 0, 0
    for _ in range(3):
        if r.stage != "clarify":
            break
        asked, messages = asked + len(r.questions), messages + 1
        answer = " ".join(q.options[0] for q in r.questions if q.options) or "не знаю"
        r = chat(ChatRequest(session_id=r.session_id, message=answer), store)
    items = _s1_items(r, asked, messages)
    failed = [k for k, ok in items.items() if not ok]
    note = (
        f"вопросов {asked}, допущений {len(r.plan.spec.assumptions)}, "
        f"направлений {len(r.plan.destinations)}; "
        + (
            f"не выполнены п.{', '.join(failed)}"
            if failed
            else "п.1–4 выполнены (климат — оценка до T12)"
        )
    )
    return not failed, note


def metric(split: str) -> dict | None:
    # TODO(kickoff): метрика жюри против baseline из eval/baseline.json.
    return None
