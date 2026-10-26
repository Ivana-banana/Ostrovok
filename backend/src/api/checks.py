"""Проверки сценариев для make eval / make eval-final (scripts/eval_harness.py).

check_<id сценария>(case) -> bool | (bool, заметка). Без проверки — «не автоматизирован».
metric(split) -> {"name", "system", "baseline"} или None.
"""

import os
import re
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.contracts import ChatReply, ChatRequest
from api.fixtures import WARM_WATER_C
from api.main import app, get_store
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


# S3: the next user message for each stage on the way to the booking.
S3_ANSWERS = {"inspire": "1", "compare": "беру", "assemble": "да", "checkout": "подтверждаю"}


def _s3_walk(client: TestClient, text: str) -> tuple[list[ChatReply], list[int]]:
    """POST /api/chat until the booking; clarify gets the first option of each question."""
    replies: list[ChatReply] = []
    codes: list[int] = []
    sid = None
    for _ in range(8):
        res = client.post("/api/chat", json={"session_id": sid, "message": text})
        codes.append(res.status_code)
        if res.status_code != 200:
            break
        r = ChatReply.model_validate(res.json())
        replies.append(r)
        sid = r.session_id
        if r.stage == "booked" or r.stage == "select":
            break
        first = " ".join(q.options[0] for q in r.questions if q.options) or "пока не знаю"
        text = first if r.stage == "clarify" else S3_ANSWERS[r.stage]
    return replies, codes


def check_S3(case: dict) -> tuple[bool, str]:
    """A and B reach a booking without a model: key empty (A1) and invalid (A2).

    Rule mode: llm=False, the UI adds «упрощённый режим без ИИ» (web/src/format.ts).
    A2 equals A1 until the LLM client exists (T11, #19): the key is not read yet.
    """
    quotes = re.findall(r"«([^»]+)»", case["input"])
    if len(quotes) != 2:
        return False, f"ожидал 2 запроса в «», нашёл {len(quotes)}: формат input изменился"
    exact, vague = quotes
    store = _store()
    app.dependency_overrides[get_store] = lambda: store
    saved = os.environ.get("LLM_API_KEY")
    fails, notes = [], []
    try:
        for variant, key in (("A1", ""), ("A2", "sk-invalid")):
            os.environ["LLM_API_KEY"] = key
            client = TestClient(app, raise_server_exceptions=False)
            if client.get("/health").status_code != 200:
                fails.append(f"{variant}: /health")
            for name, text in (("A", exact), ("B", vague)):
                replies, codes = _s3_walk(client, text)
                tag = f"{variant}/{name}"
                if any(c >= 500 for c in codes) or not replies:
                    fails.append(f"{tag}: ответ {codes[-1]}")
                    continue
                last = replies[-1]
                if last.stage != "booked" or last.voucher is None:
                    fails.append(f"{tag}: остановился на {last.stage}")
                if any(r.llm for r in replies):
                    fails.append(f"{tag}: llm=True")
                asked = sum(len(r.questions) for r in replies)
                if name == "A" and (asked or replies[0].stage != "compare"):
                    fails.append(f"{tag}: точный запрос не разобран ({replies[0].stage})")
                if variant == "A1":
                    total = last.voucher.total_rub if last.voucher else 0
                    budget = last.plan.spec.budget_rub
                    over = f" > бюджета {budget}" if budget and total > budget else ""
                    notes.append(
                        f"{name}: вопросов {asked}, ходов {len(replies)}, итог {total}{over}"
                    )
    finally:
        app.dependency_overrides.clear()
        if saved is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = saved
    note = "; ".join(notes) + (
        f"; не выполнено: {', '.join(fails)}" if fails else "; A1, A2, B без 5xx"
    )
    return not fails, note


def metric(split: str) -> dict | None:
    # TODO(kickoff): метрика жюри против baseline из eval/baseline.json.
    return None
