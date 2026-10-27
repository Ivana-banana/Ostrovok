"""#132: «на пару дней», «на сутки», «а на неделю?» are a trip length (not the default 7 nights)."""

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


@pytest.mark.parametrize(
    ("text", "nights"),
    [
        ("Хотим в Казань на пару дней", 2),
        ("Калининград на сутки", 1),
        ("Сочи на двое суток", 2),
        ("Сочи, сократить расходы на 3 дня", 2),
    ],
)
def test_first_message_duration(store, text, nights):
    assert say(store, text).plan.spec.nights == nights


def test_follow_up_week_changes_length(store):
    first = say(store, "Сочи на 3 ночи в ноябре")
    assert first.plan.spec.nights == 3
    reply = say(store, "а на неделю?", first.session_id)
    assert reply.plan.spec.nights == 7
    assert "Было → стало" in reply.reply
