"""The chat helper of the tests (#436, #630).

The user of these tests flies from Москва: before #436 the agent assumed it silently. Now an
unnamed origin is the first question. say() answers it with the chip «Из Москвы» when the other
questions of that turn are of #630 only, so a test of another stage starts where it started
before. #630 asks the month, the nights, the party and the budget too: say() answers a turn of
these questions only with «Не важно, решите сами», so the plan has the assumptions it had before
#630. The questions themselves are tested in test_ask_origin.py and test_ask_five_630.py with
ask().
"""

from api.contracts import ChatReply, ChatRequest
from api.model import BUDGET_ASK, MONTH_ASK, NIGHTS_ASK, ORIGIN_ASK, SKIP_ASK, WHO_ASK, chat
from api.store import SessionStore

FROM_MOSCOW = "Из Москвы"
NEW_ASKS = {MONTH_ASK, NIGHTS_ASK, WHO_ASK, BUDGET_ASK}  # #630: the agent assumed them before


def origin_only(r: ChatReply) -> bool:
    """The origin question and, next to it, only the questions of #630."""
    texts = [q.text for q in r.questions]
    origin = [t for t in texts if t.startswith(ORIGIN_ASK)]
    return r.stage == "clarify" and len(origin) == 1 and set(texts) - set(origin) <= NEW_ASKS


def new_only(r: ChatReply) -> bool:
    """Only the questions of #630. The month without a place and a wish was asked before #630
    too: the test answers it."""
    texts = {q.text for q in r.questions}
    spec = r.plan.spec
    old_month = MONTH_ASK in texts and not (spec.destinations or spec.wishes)
    return r.stage == "clarify" and bool(texts) and texts <= NEW_ASKS and not old_month


def say(store: SessionStore, text: str, sid: str | None = None) -> ChatReply:
    r = chat(ChatRequest(session_id=sid, message=text), store)
    if sid is None and origin_only(r):
        r = chat(ChatRequest(session_id=r.session_id, message=FROM_MOSCOW), store)
    if new_only(r):
        r = chat(ChatRequest(session_id=r.session_id, message=SKIP_ASK), store)
    return r


def ask(store: SessionStore, text: str, sid: str | None = None) -> ChatReply:
    """One turn as it is: the questions are not answered for the test."""
    return chat(ChatRequest(session_id=sid, message=text), store)


def planned(store: SessionStore, text: str, sid: str | None = None) -> ChatReply:
    """say(); the questions left (the party of #630 too) get «Не важно, решите сами»."""
    r = say(store, text, sid)
    for _ in range(2):
        if r.stage != "clarify" or not r.questions:
            break
        r = chat(ChatRequest(session_id=r.session_id, message=SKIP_ASK), store)
    return r
