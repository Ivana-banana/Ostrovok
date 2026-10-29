"""#596: each clarifying question has «Не важно, решите сами» and «Свой вариант»; the default of
assume() goes first with «(рекомендуем)». A button is read by the rules, never by the model
(TRAP-1). The options name no price except the fixed budget ranges (TRAP-4)."""

from datetime import date
from pathlib import Path

import pytest

from api import llm
from api.contracts import ChatRequest
from api.extract import MONTH_NAMES
from api.model import OWN_ANSWER, RECOMMENDED, SKIP_ASK, chat, step
from api.store import SessionStore

NEXT_MONTH = MONTH_NAMES[date.today().month % 12]


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def test_every_question_ends_with_skip_and_own_answer(store):
    r = say(store, "Хочу отдохнуть")
    assert r.stage == "clarify" and len(r.questions) == 2  # D-031: the limit stays
    for q in r.questions:
        assert q.options[-2:] == [SKIP_ASK, OWN_ANSWER]


def test_the_default_has_the_mark(store):
    month = next(q for q in say(store, "Хочу отдохнуть").questions if "Когда" in q.text)
    assert month.options[0] == NEXT_MONTH + RECOMMENDED
    party = say(store, "Сочи с ребёнком 5 лет в июле на 4 ночи, из Казани").questions
    assert "Двое взрослых" + RECOMMENDED in party[0].options


def test_TRAP_4_no_price_in_the_options(store):
    r = say(store, "Хочу отдохнуть из Казани в июле")  # origin and month known: the budget is asked
    assert [q.text for q in r.questions] == ["Какой бюджет на всю поездку?"]
    priced = [o for q in r.questions for o in q.options if "₽" in o]
    assert priced == ["до 50 000 ₽", "до 100 000 ₽", "до 200 000 ₽"]


def test_recommended_button_fills_the_field(store):
    r = say(store, "Хочу отдохнуть")
    after = say(store, NEXT_MONTH + RECOMMENDED, r.session_id)
    assert after.plan.spec.month == date.today().month % 12 + 1
    assert not any(RECOMMENDED.strip() in a for a in after.plan.spec.assumptions)


def test_the_mark_is_cut_before_the_rules_read_the_answer(store):
    # «Казань (рекомендуем)» is read as a destination, bare «Казань» as the origin: the strip in step()
    r = say(store, "Хочу отдохнуть")
    s = say(store, "Казань" + RECOMMENDED, r.session_id).plan.spec
    assert s.origin == "Казань" and s.destinations == []


def test_own_answer_matches_the_web():
    # the web compares the button text with its own constant; a drift makes the button send a text
    src = (Path(__file__).parents[2] / "web/src/App.tsx").read_text(encoding="utf-8")
    assert f'export const OWN_ANSWER = "{OWN_ANSWER}";' in src


def test_skip_names_the_assumptions(store):
    r = say(store, "Хочу отдохнуть")
    after = say(store, SKIP_ASK, r.session_id)
    s = after.plan.spec
    assert after.stage != "clarify" and not after.llm
    assert s.origin == "Москва" and s.month and s.nights
    assert "откуда: Москва (город вылета не назван)" in s.assumptions
    assert "Допущения:" in after.reply


def test_TRAP_1_skip_goes_to_no_model(monkeypatch):
    def no_model(*_a, **_k):
        raise AssertionError("«Не важно» went to the model")

    first = step(None, "s1", "Хочу отдохнуть")
    monkeypatch.setattr(llm, "extract_spec", no_model)
    assert step(first, "s1", SKIP_ASK, use_llm=True).llm is False
