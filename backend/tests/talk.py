"""The chat helper of the tests (#436).

The user of these tests flies from Москва: before #436 the agent assumed it silently. Now an
unnamed origin is the first question. say() answers it with the chip «Из Москвы» when it is the
only question of a new session, so a test of another stage starts where it started before. The
origin question itself is tested in test_ask_origin.py with chat().
"""

from api.contracts import ChatReply, ChatRequest
from api.model import ORIGIN_ASK, chat
from api.store import SessionStore

FROM_MOSCOW = "Из Москвы"


def origin_only(r: ChatReply) -> bool:
    return r.stage == "clarify" and [q.text.startswith(ORIGIN_ASK) for q in r.questions] == [True]


def say(store: SessionStore, text: str, sid: str | None = None) -> ChatReply:
    r = chat(ChatRequest(session_id=sid, message=text), store)
    if sid is None and origin_only(r):
        r = chat(ChatRequest(session_id=r.session_id, message=FROM_MOSCOW), store)
    return r


def ask(store: SessionStore, text: str, sid: str | None = None) -> ChatReply:
    """One turn as it is: the origin question is not answered for the test."""
    return chat(ChatRequest(session_id=sid, message=text), store)
