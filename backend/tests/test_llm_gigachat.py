"""GigaChat in llm.py (#117): OAuth token, response_format order, one stream, TLS root.

The HTTP layer is a fake urlopen: no network. The live smoke test runs only with
LLM_LIVE=1 and GIGACHAT_AUTH_KEY, LLM_BASE_URL, LLM_MODEL in the environment.
"""

import hashlib
import io
import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import uuid
from pathlib import Path

import pytest

from api import llm
from api.contracts import TripSpec
from api.guards import llm_available

OAUTH = "https://oauth.test/api/v2/oauth"
CHAT = "https://giga.test/v1/chat/completions"
NULLS = dict.fromkeys(llm.SPEC_FIELDS)
ROOT_SHA256 = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"


class Resp:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url: str, code: int, message: str = "") -> urllib.error.HTTPError:
    body = json.dumps({"status": code, "message": message}).encode()
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(body) if message else None)


EMPTY_SCHEMA = "Empty schema with json format type is not supported"  # GigaChat, OpenAI form


class FakeGiga:
    """urlopen stand-in for the OAuth endpoint and the chat endpoint."""

    def __init__(self):
        self.oauth: list = []  # OAuth requests
        self.chat: list[tuple[str, dict]] = []  # (Authorization header, payload)
        self.contexts: list = []
        self.tokens = iter(f"token-{i}" for i in range(1, 100))
        self.expires_in_s = 1800.0
        self.on_oauth = None  # an exception to raise instead of a token
        self.on_chat = lambda auth, payload: Resp(
            {"choices": [{"message": {"content": json.dumps(NULLS)}}]}
        )

    def __call__(self, req, timeout=None, context=None):
        self.contexts.append(context)
        if req.full_url == OAUTH:
            self.oauth.append(req)
            if self.on_oauth is not None:
                raise self.on_oauth
            expires = int((time.time() + self.expires_in_s) * 1000)
            return Resp({"access_token": next(self.tokens), "expires_at": expires})
        assert req.full_url == CHAT
        payload = json.loads(req.data)
        auth = req.get_header("Authorization")
        self.chat.append((auth, payload))
        out = self.on_chat(auth, payload)
        if isinstance(out, BaseException):
            raise out
        return out


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    def deny(*a, **kw):
        raise AssertionError("network in tests")

    monkeypatch.setattr(llm.urllib.request, "urlopen", deny)
    for k in (
        "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_TIMEOUT_S",
        "GIGACHAT_AUTH_KEY", "GIGACHAT_SCOPE", "GIGACHAT_OAUTH_URL",
    ):  # fmt: skip
        monkeypatch.delenv(k, raising=False)
    llm.reset()
    yield
    llm.reset()


@pytest.fixture
def giga(monkeypatch):
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "auth-key")
    monkeypatch.setenv("GIGACHAT_OAUTH_URL", OAUTH)
    monkeypatch.setenv("LLM_BASE_URL", "https://giga.test/v1")
    monkeypatch.setenv("LLM_MODEL", "GigaChat-2")
    monkeypatch.setenv("LLM_TIMEOUT_S", "1")
    f = FakeGiga()
    monkeypatch.setattr(llm.urllib.request, "urlopen", f)
    return f


def ask() -> dict | None:
    return llm.complete_json("system", {"x": 1}, "trip_spec_patch", llm.SPEC_SCHEMA)


def test_trap_1_no_key_means_rules_and_no_request(monkeypatch):
    """TRAP-1: address and model without any key: rule mode, nothing goes to the network."""
    monkeypatch.setenv("LLM_BASE_URL", "https://giga.test/v1")
    monkeypatch.setenv("LLM_MODEL", "GigaChat-2")
    assert not llm_available()
    assert ask() is None  # clean_env fails the test on any request
    assert llm_available({"GIGACHAT_AUTH_KEY": "k", "LLM_BASE_URL": "u", "LLM_MODEL": "m"})
    assert not llm_available({"GIGACHAT_AUTH_KEY": "k", "LLM_MODEL": "m"})
    assert not llm_available({"GIGACHAT_AUTH_KEY": " ", "LLM_BASE_URL": "u", "LLM_MODEL": "m"})


def test_trap_1_env_example_as_copied_by_the_jury_is_rule_mode():
    """TRAP-1: `cp .env.example .env` fills address and model, keys stay empty: rules."""
    path = Path(__file__).resolve().parents[2] / ".env.example"
    lines = [x for x in path.read_text("utf-8").splitlines() if "=" in x and x[0] != "#"]
    env = dict(x.split("=", 1) for x in lines)
    assert env["LLM_BASE_URL"] and env["LLM_MODEL"]
    assert env["LLM_API_KEY"] == env["GIGACHAT_AUTH_KEY"] == ""
    assert not llm_available(env)


def test_auth_key_is_exchanged_for_a_token(giga):
    assert ask() == NULLS
    assert len(giga.oauth) == 1
    req = giga.oauth[0]
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Basic auth-key"
    assert req.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert req.get_header("Accept") == "application/json"
    uuid.UUID(req.get_header("Rquid"))  # a fresh RqUID on each request
    assert urllib.parse.parse_qs(req.data.decode()) == {"scope": ["GIGACHAT_API_PERS"]}
    assert giga.chat[0][0] == "Bearer token-1"
    assert all(isinstance(c, ssl.SSLContext) for c in giga.contexts)  # https: bundled root


def test_scope_comes_from_the_env(monkeypatch, giga):
    monkeypatch.setenv("GIGACHAT_SCOPE", "GIGACHAT_API_B2B")
    ask()
    assert urllib.parse.parse_qs(giga.oauth[0].data.decode()) == {"scope": ["GIGACHAT_API_B2B"]}


def test_token_is_cached_until_it_nearly_expires(giga):
    ask()
    ask()
    assert len(giga.oauth) == 1
    assert [a for a, _ in giga.chat] == ["Bearer token-1", "Bearer token-1"]
    llm._token["until"] = time.time() - 1  # expires_at minus the margin has passed
    ask()
    assert len(giga.oauth) == 2 and giga.chat[-1][0] == "Bearer token-2"


def test_token_close_to_expiry_is_not_kept(giga):
    giga.expires_in_s = llm.TOKEN_MARGIN_S / 2
    ask()
    ask()
    assert len(giga.oauth) == 2


def test_401_on_chat_takes_a_new_token_once(giga):
    def chat(auth, payload):
        if auth == "Bearer token-1":
            return http_error(CHAT, 401)
        return Resp({"choices": [{"message": {"content": json.dumps(NULLS)}}]})

    giga.on_chat = chat
    assert ask() == NULLS
    assert len(giga.oauth) == 2
    assert [a for a, _ in giga.chat] == ["Bearer token-1", "Bearer token-2"]
    assert llm._state["down_until"] == 0.0


def test_401_after_a_new_token_pauses_the_llm(giga):
    giga.on_chat = lambda auth, payload: http_error(CHAT, 401)
    assert ask() is None
    assert len(giga.oauth) == 2 and len(giga.chat) == 2
    assert ask() is None and len(giga.chat) == 2  # paused: no more requests


@pytest.mark.parametrize("code", [400, 401, 403])
def test_rejected_auth_key_pauses_and_is_no_format_error(giga, code):
    """A wrong key or scope on OAuth is not a rejected response_format."""
    giga.on_oauth = http_error(OAUTH, code)
    assert ask() is None
    assert not giga.chat and llm._state["format"] == {}
    assert llm._state["down_until"] - llm._now() > llm.PAUSE_S
    assert ask() is None and len(giga.oauth) == 1


def test_static_key_wins_and_needs_no_oauth(monkeypatch, giga):
    monkeypatch.setenv("LLM_API_KEY", "static")
    assert ask() == NULLS
    assert not giga.oauth and giga.chat[0][0] == "Bearer static"


def reject_openai_form(auth, payload):
    """GigaChat: the OpenAI json_schema form is a 400, the flat form works."""
    if "json_schema" in payload["response_format"]:
        return http_error(CHAT, 400, EMPTY_SCHEMA)
    return Resp({"choices": [{"message": {"content": json.dumps(NULLS)}}]})


def test_gigachat_key_sends_the_flat_form_first(giga, caplog):
    """#568: with GIGACHAT_AUTH_KEY the first request is the working one, no 400 round trip."""
    giga.on_chat = reject_openai_form
    with caplog.at_level(logging.WARNING, logger="api.llm"):
        assert ask() == NULLS
    assert [p["response_format"] for _, p in giga.chat] == [
        {"type": "json_schema", "schema": llm.SPEC_SCHEMA}
    ]
    assert "rejected" not in caplog.text


def test_response_format_order_and_memory(monkeypatch, giga, caplog):
    """A static key: OpenAI json_schema first, then the flat GigaChat form; remembered."""
    monkeypatch.setenv("LLM_API_KEY", "static")
    giga.on_chat = reject_openai_form
    with caplog.at_level(logging.WARNING, logger="api.llm"):
        assert ask() == NULLS
    formats = [p["response_format"] for _, p in giga.chat]
    assert formats[0]["json_schema"]["schema"] == llm.SPEC_SCHEMA
    assert formats[1] == {"type": "json_schema", "schema": llm.SPEC_SCHEMA}
    assert "json_schema/openai" in caplog.text  # never a silent switch
    ask()
    assert giga.chat[-1][1]["response_format"] == formats[1]  # remembered: one request
    assert len(giga.chat) == 3


def test_every_format_rejected_is_logged_with_its_name(giga, caplog):
    giga.on_chat = lambda auth, payload: http_error(CHAT, 400, "Unknown type in response_format")
    with caplog.at_level(logging.WARNING, logger="api.llm"):
        assert ask() is None
    kinds = [p["response_format"] for _, p in giga.chat]
    assert [("json_schema" in k, "schema" in k, k["type"]) for k in kinds] == [
        (False, True, "json_schema"),
        (True, False, "json_schema"),
        (False, False, "json_object"),
    ]
    for name in ("json_schema/openai", "json_schema/gigachat", "json_object"):
        assert name in caplog.text


@pytest.mark.parametrize(
    "message", ["", "Invalid params: messages too long", "<html>bad request</html>"]
)
def test_400_without_a_format_reason_keeps_the_format(giga, caplog, message):
    """Review of #117: only a 400 about response_format moves to the next format."""
    giga.on_chat = lambda auth, payload: http_error(CHAT, 400, message)
    with caplog.at_level(logging.WARNING, logger="api.llm"):
        assert ask() is None
    assert len(giga.chat) == 1 and llm._state["format"] == {}
    assert llm._state["down_until"] - llm._now() > llm.RATE_PAUSE_S  # the usual pause


def test_error_log_has_the_code_and_the_message_only(giga, caplog):
    text = "Unknown type in response_format " + "x" * 300
    giga.on_chat = lambda auth, payload: http_error(CHAT, 400, text)
    with caplog.at_level(logging.WARNING, logger="api.llm"):
        ask()
    assert "x" * 120 not in caplog.text and "Unknown type in response_format" in caplog.text
    assert '"status"' not in caplog.text  # not the raw body


def test_429_is_a_short_pause(giga):
    giga.on_chat = lambda auth, payload: http_error(CHAT, 429)
    assert ask() is None
    assert 0 < llm._state["down_until"] - llm._now() <= llm.RATE_PAUSE_S


def test_one_request_at_a_time_and_the_wait_has_a_deadline(giga):
    """GigaChat gives a key one stream: a request waits for the lock, not past the deadline."""
    assert llm._lock.acquire()
    try:
        out = llm.complete_json("s", {}, "n", {}, until=llm._now() + llm.MIN_LEFT_S + 0.2)
    finally:
        llm._lock.release()
    assert out is None and not giga.chat and not giga.oauth
    assert llm._state["down_until"] == 0.0  # a busy stream does not pause the LLM

    done = []
    assert llm._lock.acquire()
    worker = threading.Thread(target=lambda: done.append(ask()))
    worker.start()
    time.sleep(0.1)
    assert not giga.chat  # waits for the stream
    llm._lock.release()
    worker.join(5)
    assert done == [NULLS] and len(giga.chat) == 1


def test_oauth_time_counts_against_the_turn_deadline(monkeypatch, giga):
    clock = [1000.0]
    monkeypatch.setattr(llm, "_now", lambda: clock[0])
    real = giga.__call__
    timeouts = []

    def slow(req, timeout=None, context=None):
        timeouts.append(timeout)
        if req.full_url == OAUTH:
            clock[0] += 4.5  # OAuth took most of the time
        return real(req, timeout, context)

    monkeypatch.setattr(llm.urllib.request, "urlopen", slow)
    monkeypatch.setenv("LLM_TIMEOUT_S", "8")
    assert llm.complete_json("s", {}, "n", {}, until=1006.0) == NULLS
    assert timeouts == [6.0, 1.5]  # the chat request gets what OAuth left


def test_tls_context_trusts_the_bundled_root_and_verifies():
    """GigaChat hosts need «Russian Trusted Root CA»; the image does not have it."""
    der = ssl.PEM_cert_to_DER_cert(llm.CA_FILE.read_text())
    assert hashlib.sha256(der).hexdigest() == ROOT_SHA256
    ctx = llm._tls()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    names = [dict(x[0] for x in c["subject"]).get("commonName") for c in ctx.get_ca_certs()]
    assert "Russian Trusted Root CA" in names


def test_http_url_gets_no_tls_context(monkeypatch):
    seen = []

    def fake(req, timeout=None, **kw):
        seen.append(kw)
        return Resp({"choices": [{"message": {"content": json.dumps(NULLS)}}]})

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    assert ask() == NULLS and seen == [{}]


# Read at import, before the fixtures clear the env and deny the network.
LIVE = {k: os.environ.get(k, "") for k in ("GIGACHAT_AUTH_KEY", "LLM_BASE_URL", "LLM_MODEL")}
REAL_URLOPEN = llm.urllib.request.urlopen


@pytest.mark.skipif(
    os.environ.get("LLM_LIVE") != "1" or not all(v.strip() for v in LIVE.values()),
    reason="live GigaChat: LLM_LIVE=1 and GIGACHAT_AUTH_KEY, LLM_BASE_URL, LLM_MODEL",
)
def test_live_gigachat_extracts_a_spec(monkeypatch):
    for k, v in LIVE.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("LLM_TIMEOUT_S", "20")
    monkeypatch.setattr(llm.urllib.request, "urlopen", REAL_URLOPEN)
    text = "Сочи с 20 ноября на 5 ночей, бюджет 120 тысяч"
    out = llm.extract_spec(text, TripSpec(), until=llm._now() + 40)
    assert out is not None and out.get("budget_rub") == 120000
