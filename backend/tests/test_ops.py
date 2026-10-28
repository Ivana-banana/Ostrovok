"""Operations (#290): a session id in the logs, a provider failure is visible, /health modes."""

import logging
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from api import offers, ops
from api.main import app

client = TestClient(app)
SOON = date.today() + timedelta(days=60)
CHAT = "Хочу в Сочи из Москвы на 4 ночи вдвоём, с " + SOON.strftime("%d.%m.%Y")


class Broken:
    """A port whose source is down."""

    def hotels(self, spec, place, now):
        raise ConnectionError("https://hotels.example/api?key=SECRET-KEY refused")

    def legs(self, spec, origin, place):
        raise TimeoutError("transport timed out")  # an OSError, like ConnectionError

    def transfers(self, spec, place, hotels, legs):
        raise ConnectionResetError("transfers down")


@pytest.fixture(autouse=True)
def records():
    """The log records of the app (ops.setup_logging adds `session` to every record)."""
    ops.setup_logging()
    got: list[logging.LogRecord] = []

    class Grab(logging.Handler):
        def emit(self, record):
            got.append(record)

    handler = Grab()
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.INFO)  # pytest leaves the root at WARNING; the app sets INFO itself
    root.addHandler(handler)
    yield got
    root.removeHandler(handler)
    root.setLevel(level)


def test_log_records_carry_the_session_id(records):
    sid = client.post("/api/chat", json={"message": CHAT}).json()["session_id"]
    mine = [r for r in records if r.getMessage().startswith("chat ")]
    assert mine
    assert all(r.session == sid for r in mine)
    assert logging.getLogger("x").makeRecord("x", 20, "f", 1, "m", (), None).session == "-"


@pytest.mark.parametrize(
    ("var", "label"),
    [("HOTELS", "отели"), ("TRANSPORT", "транспорт"), ("TRANSFERS", "трансферы")],
)
def test_provider_failure_is_logged_and_marked_in_the_reply(monkeypatch, records, var, label):
    """A failing port: an ERROR record with the session id and a mark in the reply and the
    plan, not a silent empty result and not a 500."""
    monkeypatch.setattr(offers, var, Broken())
    r = client.post("/api/chat", json={"message": CHAT})
    assert r.status_code == 200
    body = r.json()
    sid = body["session_id"]
    failed = [
        r for r in records if r.levelno == logging.ERROR and "provider failure" in r.getMessage()
    ]
    assert failed
    assert all(r.session == sid for r in failed)
    assert len(failed) == 1  # one record per source per turn, not one per candidate
    assert f"provider failure: {var.lower()} (" in failed[0].getMessage()
    assert not any(r.exc_info for r in records if r.levelno >= logging.INFO)
    assert "SECRET-KEY" not in " ".join(r.getMessage() for r in records)
    assert f"Источник «{label}» не ответил" in body["reply"]
    # #532: the plan without transport also has a road error that names transport; the mark
    # is the issue of the source itself.
    marks = [i for i in body["plan"]["issues"] if i["text"].startswith(f"Источник «{label}»")]
    assert marks
    assert marks[0]["severity"] == "warn"
    assert "SECRET-KEY" not in r.text  # the exception text stays in the log, not in the reply


def test_a_bug_in_an_adapter_is_not_hidden(monkeypatch):
    """KeyError is our bug, not a provider failure: it propagates."""

    class Buggy(Broken):
        def hotels(self, spec, place, now):
            raise KeyError("airport")

    monkeypatch.setattr(offers, "HOTELS", Buggy())
    with pytest.raises(KeyError):
        TestClient(app).post("/api/chat", json={"message": CHAT})


def test_log_level_falls_back_to_info():
    assert ops._level("debug") == logging.DEBUG
    assert ops._level("") == logging.INFO
    assert ops._level("loud") == logging.INFO


def test_working_providers_leave_no_mark(records):
    body = client.post("/api/chat", json={"message": CHAT}).json()
    assert "не ответил" not in body["reply"]
    assert not [r for r in records if "provider failure" in r.getMessage()]


def test_health_reports_modes_without_rules_key(monkeypatch):
    for k in ("LLM_API_KEY", "GIGACHAT_AUTH_KEY"):
        monkeypatch.delenv(k, raising=False)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["data_mode"] == "синтетика"
    assert body["catalog"] == "снапшот"
    assert body["llm_mode"] == "правила"
    assert client.get("/api/health").json() == body


def test_health_names_the_model_and_hides_the_keys(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "model-x")
    monkeypatch.setenv("LLM_API_KEY", "SECRET-KEY")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "SECRET-AUTH")
    r = client.get("/health")
    assert r.json()["llm_mode"] == "model-x"
    assert "SECRET" not in r.text
    assert "llm.example" not in r.text


def test_health_data_mode_is_the_lowest_of_the_adapters(monkeypatch):
    class Real:
        data_mode = "реальный"

    class Snapshot:
        data_mode = "снапшот"

    monkeypatch.setattr(offers, "HOTELS", Real())
    monkeypatch.setattr(offers, "TRANSPORT", Real())
    monkeypatch.setattr(offers, "TRANSFERS", Snapshot())
    assert ops.data_mode()["data_mode"] == "снапшот"
    monkeypatch.setattr(offers, "TRANSFERS", Real())
    assert ops.data_mode()["data_mode"] == "реальный"
    monkeypatch.setattr(offers, "TRANSFERS", object())  # no declared mode: the safe side
    assert ops.data_mode()["data_mode"] == "синтетика"


def test_trap7_health_names_the_live_hotels_but_keeps_the_synthetic_rank(monkeypatch):
    from api.live_hotels import Live101Hotels

    monkeypatch.setattr(offers, "HOTELS", Live101Hotels(offers.HOTELS))
    body = ops.health()
    assert body["status"] == "ok"
    assert body["hotels"] == "101hotels (живые) / синтетика при сбое"
    # the fallback is synthetic: the whole is not «реальный»
    assert body["data_mode"] == "синтетика"
    assert body["transport"] == "синтетика"
