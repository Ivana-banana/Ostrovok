"""#385: «давай на 1 человека?» at a built plan is a party edit, not a price unit and no consent.
Without a model (TRAP-1)."""

import pytest
from talk import say

from api.store import SessionStore

FAMILY = "Сочи из Москвы с 3 по 8 декабря, двое взрослых и ребёнок 6 лет"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GIGACHAT_AUTH_KEY", raising=False)
    return SessionStore(tmp_path / "sessions.db")


@pytest.mark.parametrize("text", ["давай на 1 человека?", "А можно на 1 человека?"])
def test_lets_make_it_for_one_is_a_party_edit(store, text):
    r = say(store, FAMILY)
    assert r.stage == "compare"
    r2 = say(store, text, r.session_id)
    assert r2.stage == "compare"
    assert r2.plan.spec.adults == 1


def test_room_question_with_a_request_word_changes_no_party(store):
    r = say(store, "Едем вчетвером в Сочи на 5 ночей с 1 июля")
    r2 = say(store, "Можно номер на 2 человека?", r.session_id)
    assert r2.plan.spec.adults == 4
