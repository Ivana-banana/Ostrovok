"""#617: with a key the model words the short answer about the plan; checks as for `explain`."""

import json
import re

import pytest
from talk import say

from api import llm
from api.store import SessionStore

SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых, отель до 40 тысяч, нужен завтрак"
QUESTION = "почему не поезд?"
NULLS = dict.fromkeys(llm.SPEC_FIELDS)


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
def plan(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    r = say(store, SOCHI)
    r = say(store, "беру", r.session_id)
    assert r.stage == "assemble"
    template = say(store, QUESTION, r.session_id).reply  # no key: the template
    return store, r.session_id, template


def with_model(monkeypatch, reply):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_TIMEOUT_S", "1")

    def urlopen(req, timeout=None):
        payload = json.loads(req.data)
        if payload["messages"][0]["content"] == llm.ANSWER_PROMPT:
            return Resp({"reply": reply(payload) if callable(reply) else reply})
        return Resp(NULLS)

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)


def asked(plan):
    store, sid, _ = plan
    return say(store, QUESTION, sid)


def test_answer_in_model_words(plan, monkeypatch):
    own = "Поезд я не выбрал: смотрите цены и время в пути ниже."
    with_model(monkeypatch, own)
    r = asked(plan)
    assert r.reply == own and r.llm is True and r.stage == "assemble"


def test_trap_1_no_key_keeps_the_template_answer(plan):
    r = asked(plan)
    assert r.reply == plan[2] and r.llm is False


def test_the_model_gets_the_template_as_facts(plan, monkeypatch):
    seen = []

    def reply(payload):
        seen.append(json.loads(payload["messages"][1]["content"]))
        return "Ответ."

    with_model(monkeypatch, reply)
    asked(plan)
    assert seen[0] == {"question": QUESTION, "answer": plan[2]}


def test_trap_4_made_up_number_keeps_the_template_answer(plan, monkeypatch):
    with_model(monkeypatch, plan[2] + " Это дороже на 77 777 ₽.")
    r = asked(plan)
    assert r.reply == plan[2] and r.llm is False


def test_trap_4_made_up_bare_number_keeps_the_template_answer(plan, monkeypatch):
    with_model(monkeypatch, "Дорога займёт 987 минут.")
    r = asked(plan)
    assert r.reply == plan[2] and r.llm is False


def test_trap_7_lost_price_mark_keeps_the_template_answer(plan, monkeypatch):
    template = plan[2]
    assert re.search(r"₽ \(синтетика\)", template)
    with_model(monkeypatch, template.replace(" (синтетика)", ""))
    r = asked(plan)
    assert r.reply == template and r.llm is False
