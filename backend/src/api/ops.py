"""Operations (#290): a session id in every log line, visible provider failures, /health modes.

- Logs: the id of the session of the current request is in each record (`session=...`), also in
  the records of llm.py and the adapters. Outside a request it is «-». The text of a user
  message and the keys never go to a log.
- Provider failures: a port call that raises (hotels, transport, transfers; api/contracts.py,
  «Порты») is logged with the traceback and gives an empty result plus a mark. The chat turn
  collects the marks and puts them into plan.issues (severity warn) and into the reply text, so
  an empty list never looks like «nothing found».
- /health: the data mode (синтетика, снапшот, реальный) from the active adapters and the LLM
  mode (правила or the model name). No key, no address, no token.
"""

import logging
import os
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar

from api.contracts import ChatReply, TimelineIssue
from api.guards import llm_available

log = logging.getLogger(__name__)

NO_SESSION = "-"
SOURCES = {"hotels": "отели", "transport": "транспорт", "transfers": "трансферы"}
RANK = {"синтетика": 0, "снапшот": 1, "реальный": 2}  # the lowest one is shown for the whole

_session: ContextVar[str] = ContextVar("session", default=NO_SESSION)
_failures: ContextVar[list[str] | None] = ContextVar("failures", default=None)


@contextmanager
def session(sid: str | None):
    """Records logged inside the block carry this session id."""
    token = _session.set(sid or NO_SESSION)
    try:
        yield
    finally:
        _session.reset(token)


@contextmanager
def collecting():
    """The marks of the provider failures inside the block; the list fills while it runs."""
    marks: list[str] = []
    token = _failures.set(marks)
    try:
        yield marks
    finally:
        _failures.reset(token)


def guarded[T](source: str, call: Callable[[], T], empty: T) -> T:
    """call() or, when it raises, `empty` with a log record and a mark (not a silent []).

    source: a key of SOURCES. The mark names the kind of the failure, never the message of the
    exception: it may hold an address or a key of the provider.
    """
    try:
        return call()
    except Exception as e:
        log.exception("provider failure: %s", source)
        mark = (
            f"Источник «{SOURCES.get(source, source)}» не ответил ({type(e).__name__}): "
            "предложений этого вида нет, план неполный"
        )
        marks = _failures.get()
        if marks is not None and mark not in marks:
            marks.append(mark)
        return empty


def mark(reply: ChatReply, marks: list[str]) -> ChatReply:
    """The reply with the failure marks: in plan.issues and as «Внимание:» lines of the text."""
    have = {i.text for i in reply.plan.issues}
    new = [m for m in marks if m not in have]
    if not new:
        return reply
    plan = reply.plan.model_copy(
        update={
            "issues": [*reply.plan.issues, *(TimelineIssue(severity="warn", text=m) for m in new)]
        }
    )
    text = "\n".join([reply.reply, *(f"Внимание: {m}." for m in new)])
    return reply.model_copy(update={"plan": plan, "reply": text})


class _SessionFactory:
    """Log record factory: adds record.session."""

    def __init__(self, previous):
        self.previous = previous

    def __call__(self, *args, **kwargs):
        record = self.previous(*args, **kwargs)
        record.session = _session.get()
        return record


def setup_logging() -> None:
    """Root logger to stderr with the session id; LOG_LEVEL (default INFO). Safe to call twice."""
    if isinstance(logging.getLogRecordFactory(), _SessionFactory):
        return
    logging.setLogRecordFactory(_SessionFactory(logging.getLogRecordFactory()))
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s session=%(session)s %(message)s",
    )


def data_mode() -> dict[str, str]:
    """Mode of each source and of the whole: the lowest of the adapters (an adapter that does
    not declare `data_mode` counts as синтетика). The catalogue is a repository snapshot."""
    from api import offers  # offers imports this module

    modes = {
        name: getattr(adapter, "data_mode", "синтетика")
        for name, adapter in (
            ("hotels", offers.HOTELS),
            ("transport", offers.TRANSPORT),
            ("transfers", offers.TRANSFERS),
        )
    }
    modes = {k: v if v in RANK else "синтетика" for k, v in modes.items()}
    return {"data_mode": min(modes.values(), key=RANK.__getitem__), **modes, "catalog": "снапшот"}


def llm_mode() -> dict[str, object]:
    """«правила» without a key, address or model (TRAP-1), else the model name; paused: after
    a timeout or an error the model is skipped for a while (llm.py)."""
    if not llm_available():
        return {"llm_mode": "правила", "llm_paused": False}
    from api import llm  # heavy import, only when the model is on

    return {
        "llm_mode": os.environ["LLM_MODEL"].strip(),
        "llm_paused": llm._now() < llm._state["down_until"],
    }


def health() -> dict[str, object]:
    return {"status": "ok", **data_mode(), **llm_mode()}
