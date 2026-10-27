"""#141: the default departure city is told when it drops out; a dash in a question is no route."""

import pytest

from api import llm
from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore

TOLD = "город вылета по умолчанию"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


@pytest.mark.parametrize(
    "text, dests",
    [
        ("Москва или Питер", ["Санкт-Петербург"]),
        ("Питер или Москва", ["Санкт-Петербург"]),
        ("Казань — Москва", ["Казань"]),
    ],
)
def test_dropped_default_origin_is_told(store, text, dests):
    r = say(store, text)
    assert r.plan.spec.destinations == dests
    assert r.plan.spec.origin == "Москва"
    assert any(TOLD in a and "«из …»" in a for a in r.plan.spec.assumptions)


def test_named_origin_is_not_told(store):
    r = say(store, "Из Москвы в Казань или Сочи")
    assert not any(TOLD in a for a in r.plan.spec.assumptions)


def test_dash_before_a_question_is_no_route(store):
    r = say(store, "Сочи, Анапа — что лучше в июле?")
    assert r.plan.spec.destinations == ["Сочи", "Анапа"]
    assert "как один план я пока не собираю" not in r.reply
    assert any(a.startswith("варианты:") for a in r.plan.spec.assumptions)
    assert not any(a.startswith("маршрут:") for a in r.plan.spec.assumptions)


def test_model_cannot_bring_the_origin_back_as_a_stay(monkeypatch, store):
    def stub(message, spec, until=None, today=None):
        return {"destinations": ["Москва", "Казань"]}

    monkeypatch.setattr(llm, "extract_spec", stub)
    r = say(store, "хочу куда-нибудь на выходные")
    assert r.llm is True
    assert "Москва" not in r.plan.spec.destinations
    assert r.plan.spec.destinations == ["Казань"]
