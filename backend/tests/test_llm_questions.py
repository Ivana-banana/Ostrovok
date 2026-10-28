"""#259: with a key the model words the clarifying questions; checks as for `explain`."""

import json

import pytest
from talk import ask

from api import llm
from api.contracts import Question
from api.store import SessionStore

NULLS = dict.fromkeys(llm.SPEC_FIELDS)
OWN = "Подскажите, откуда вы вылетаете и когда хотите поехать?"


class Resp:
    def __init__(self, obj):
        self.body = json.dumps(
            {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]}
        ).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_TIMEOUT_S", "GIGACHAT_AUTH_KEY"):
        monkeypatch.delenv(k, raising=False)
    llm.reset()
    yield
    llm.reset()


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "s.db")


def with_model(monkeypatch, reply):
    """A fake model: the spec call finds nothing, the question call answers `reply`."""
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_TIMEOUT_S", "1")
    calls = []

    def urlopen(req, timeout=None):
        payload = json.loads(req.data)
        calls.append(payload)
        if payload["messages"][0]["content"] == llm.ASK_PROMPT:
            return Resp({"reply": reply})
        return Resp(NULLS)

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    return calls


def test_clarify_reply_in_model_words(store, monkeypatch):
    calls = with_model(monkeypatch, OWN)
    r = ask(store, "хочу в отпуск")
    assert r.stage == "clarify" and r.reply == OWN and r.llm is True
    assert r.questions and any(c["messages"][0]["content"] == llm.ASK_PROMPT for c in calls)


def test_trap_1_no_key_keeps_the_template_clarify_text(store):
    r = ask(store, "хочу в отпуск")  # any request out fails: urlopen is the real one, no URL set
    assert r.stage == "clarify" and r.llm is False
    assert r.reply.startswith("Чтобы подобрать поездку, уточню: ")


def test_trap_4_made_up_number_keeps_the_template_clarify_text(store, monkeypatch):
    with_model(monkeypatch, "Какой бюджет — например, 77 777 ₽ на всю поездку?")
    r = ask(store, "хочу в отпуск")
    assert r.llm is False and r.reply.startswith("Чтобы подобрать поездку, уточню: ")


def test_trap_4_number_from_the_question_text_passes(monkeypatch):
    q = [Question(text="Дорога туда и обратно ≈ 12 000 ₽. Откуда летите?", options=["Из Казани"])]
    with_model(monkeypatch, "Билеты туда и обратно стоят ≈ 12 000 ₽. Из какого вы города?")
    assert (
        llm.rewrite_questions(q) == "Билеты туда и обратно стоят ≈ 12 000 ₽. Из какого вы города?"
    )


def test_trap_4_new_rouble_sum_in_the_rewrite_is_rejected(monkeypatch):
    q = [Question(text="Дорога ≈ 12 000 ₽. Откуда летите?", options=[])]
    with_model(monkeypatch, "Дорога ≈ 13 000 ₽. Откуда летите?")
    assert llm.rewrite_questions(q) is None


def test_trap_7_lost_price_mark_in_the_rewrite_is_rejected(monkeypatch):
    q = [Question(text="Дорога 12 000 ₽ (синтетика). Откуда летите?", options=[])]
    with_model(monkeypatch, "Дорога 12 000 ₽. Откуда летите?")
    assert llm.rewrite_questions(q) is None


def test_trap_4_repeated_prompt_line_in_the_rewrite_is_rejected(monkeypatch):
    q = [Question(text="Откуда летите?", options=[])]
    with_model(monkeypatch, "Ask every question. Do not add facts. Do not list the options.")
    assert llm.rewrite_questions(q) is None
