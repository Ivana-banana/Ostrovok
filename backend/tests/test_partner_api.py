"""Partner API (#435): plan in one call, partner keys, booking webhook, Idempotency-Key, CORS,
the embed widget and the OpenAPI a partner reads."""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import api.main
from api import cart
from api.cart import SimulatedBooking
from api.main import app, get_store
from api.partner import cors_options
from api.store import SessionStore

KAZAN = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё."
ROOT = Path(__file__).resolve().parents[2]
ENV = (
    "LLM_API_KEY",
    "GIGACHAT_AUTH_KEY",
    "PARTNER_API_KEYS",
    "BOOKING_WEBHOOK_URL",
    "BOOKING_WEBHOOK_TIMEOUT_S",
    "ALLOWED_ORIGINS",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    store = SessionStore(tmp_path / "sessions.db")
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _planned(client, message=KAZAN, headers=None):
    response = client.post("/api/v1/plan", json={"message": message}, headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()


def _checkout(client, reply, headers=None):
    body = {"session_id": reply["session_id"], "confirm_total_rub": reply["cart"]["total_rub"]}
    return client.post("/api/v1/checkout", json=body, headers=headers or {})


# --- POST /api/v1/plan ----------------------------------------------------------------------


def test_plan_one_call_fills_the_cart_without_llm_trap1_trap4(client):
    """TRAP-1: no LLM key, the rules build the plan. TRAP-4: the cart holds plan offers only."""
    reply = _planned(client)
    assert reply["llm"] is False
    assert reply["stage"] == "checkout"
    assert reply["questions"] == []
    assert reply["cart"]["items"] and reply["cart"]["total_rub"] > 0
    offers = {o["offer_id"]: o["price_rub"] for o in reply["plan"]["offers"]}
    for item in reply["cart"]["items"]:
        assert offers[item["offer_id"]] == item["price_rub"]
    assert reply["cart"]["total_rub"] == sum(i["price_rub"] for i in reply["cart"]["items"])
    assert reply["plan"]["explanations"]
    assert reply["voucher"] is None  # plan books nothing
    # the same session as the chat: the web client and /api/v1/chat continue it
    assert client.get(f"/api/session/{reply['session_id']}").json() == reply


def test_plan_picks_a_destination_from_inspiration(client):
    # the origin is named: an unnamed one is a blocking question (#436)
    reply = _planned(
        client, "тёплое море в ноябре на 7 ночей, недорого, 2 взрослых и ребёнок 5 лет, из Москвы"
    )
    assert reply["stage"] == "checkout"
    assert len(reply["plan"]["spec"]["destinations"]) == 1
    assert reply["cart"]["items"]


def test_plan_stops_at_a_blocking_question_and_goes_on_after_the_answer(client):
    reply = _planned(client, "тёплое море в ноябре, недорого, с ребёнком")
    assert reply["questions"] and reply["stage"] != "checkout"
    assert reply["cart"]["items"] == []
    sid = reply["session_id"]
    response = client.post("/api/v1/plan", json={"session_id": sid, "message": "2 взрослых"})
    reply = response.json()
    assert reply["session_id"] == sid
    assert reply["stage"] == "checkout" and reply["cart"]["items"]
    assert reply["plan"]["spec"]["adults"] == 2


def test_plan_then_checkout_books_the_planned_cart(client):
    reply = _planned(client)
    booked = _checkout(client, reply)
    assert booked.status_code == 200
    voucher = booked.json()
    assert voucher["simulated"] is True
    assert voucher["total_rub"] == reply["cart"]["total_rub"]


def test_plan_rejects_an_empty_message(client):
    assert client.post("/api/v1/plan", json={"message": ""}).status_code == 422


# --- Partner keys -----------------------------------------------------------------------------


def test_empty_partner_keys_keep_api_open(client):
    """The jury runs `docker compose up` without setup: no key in .env, no key needed."""
    assert client.post("/api/v1/chat", json={"message": KAZAN}).status_code == 200


def test_partner_keys_guard_v1_only(client, monkeypatch):
    monkeypatch.setenv("PARTNER_API_KEYS", " key-one , key-two ")
    msg = {"message": KAZAN}
    for path in ("/api/v1/chat", "/api/v1/plan"):
        missing = client.post(path, json=msg)
        assert missing.status_code == 401
        assert "X-API-Key" in missing.json()["detail"]
        assert client.post(path, json=msg, headers={"X-API-Key": "wrong"}).status_code == 401
        assert (
            client.post(path, json=msg, headers={"X-API-Key": "ключ".encode()}).status_code == 401
        )
    ok = client.post("/api/v1/chat", json=msg, headers={"X-API-Key": "key-two"})
    assert ok.status_code == 200
    sid = ok.json()["session_id"]
    assert client.get(f"/api/v1/session/{sid}").status_code == 401
    assert client.get(f"/api/v1/session/{sid}", headers={"X-API-Key": "key-one"}).status_code == 200
    assert client.post("/api/v1/checkout", json={"session_id": sid, "confirm_total_rub": 1}).status_code == 401  # fmt: skip
    cart_edit = {"session_id": sid, "action": "add", "offer_id": "x"}
    assert client.post("/api/v1/cart", json=cart_edit).status_code == 401
    # the web client (/api/*), health and mode need no key
    assert client.post("/api/chat", json=msg).status_code == 200
    assert client.get(f"/api/session/{sid}").status_code == 200
    assert client.get("/api/v1/health").json()["status"] == "ok"  # plus the modes since #290
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/v1/mode").status_code == 200


# --- Booking webhook --------------------------------------------------------------------------


class _Receiver(BaseHTTPRequestHandler):
    bodies: ClassVar[list[dict]] = []
    status = 200
    delay = 0.0

    def do_POST(self):
        size = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(size))
        time.sleep(type(self).delay)
        type(self).bodies.append({"body": body, "headers": dict(self.headers)})
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def receiver():
    handler = type("Receiver", (_Receiver,), {"bodies": [], "status": 200, "delay": 0.0})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield handler, f"http://127.0.0.1:{server.server_port}/hooks/booking?token=secret"
    server.shutdown()
    server.server_close()


def test_webhook_gets_the_voucher_once(client, monkeypatch, receiver):
    handler, url = receiver
    monkeypatch.setenv("BOOKING_WEBHOOK_URL", url)
    reply = _planned(client)
    voucher = _checkout(client, reply).json()
    assert len(handler.bodies) == 1
    sent = handler.bodies[0]
    assert sent["body"] == voucher
    assert sent["headers"]["Content-Type"] == "application/json"
    assert sent["headers"]["X-Booking-Id"] == voucher["booking_id"]
    # a repeat of the payment and the chat after the booking send nothing new
    assert _checkout(client, reply).json() == voucher
    client.post("/api/v1/chat", json={"session_id": reply["session_id"], "message": "подтверждаю"})
    assert len(handler.bodies) == 1


def test_webhook_after_chat_confirmation(client, monkeypatch, receiver):
    handler, url = receiver
    monkeypatch.setenv("BOOKING_WEBHOOK_URL", url)
    reply = _planned(client)
    sid = reply["session_id"]
    booked = client.post("/api/v1/chat", json={"session_id": sid, "message": "подтверждаю"}).json()
    assert booked["voucher"] is not None
    assert [b["body"]["booking_id"] for b in handler.bodies] == [booked["voucher"]["booking_id"]]


@pytest.mark.parametrize("failure", ["http_500", "timeout", "refused", "bad_url"])
def test_webhook_failure_keeps_the_booking_and_is_logged(
    client, monkeypatch, receiver, caplog, failure
):
    handler, url = receiver
    if failure == "http_500":
        handler.status = 500
    elif failure == "timeout":
        handler.delay = 1.0
        monkeypatch.setenv("BOOKING_WEBHOOK_TIMEOUT_S", "0.2")
    elif failure == "refused":
        url = "http://127.0.0.1:9/booking"  # discard port: nothing listens
    else:
        url = "not a url"
    monkeypatch.setenv("BOOKING_WEBHOOK_URL", url)
    reply = _planned(client)
    with caplog.at_level(logging.WARNING, logger="api.webhook"):
        booked = _checkout(client, reply)
    assert booked.status_code == 200
    voucher = booked.json()
    assert voucher["status"] == "confirmed"
    stored = client.get(f"/api/v1/session/{reply['session_id']}").json()
    assert stored["voucher"] == voucher
    warnings = [r.getMessage() for r in caplog.records if r.name == "api.webhook"]
    assert any(voucher["booking_id"] in w and "not sent" in w for w in warnings), warnings
    assert not any("secret" in w for w in warnings)  # the query token stays out of the log


def test_webhook_log_has_no_url_even_when_urllib_puts_it_in_the_error(client, monkeypatch, caplog):
    """urllib's ValueError for a URL without a scheme quotes the whole URL, token included."""
    monkeypatch.setenv("BOOKING_WEBHOOK_URL", "hooks.example.com/x?token=SECRET")
    reply = _planned(client)
    with caplog.at_level(logging.WARNING, logger="api.webhook"):
        assert _checkout(client, reply).status_code == 200
    warnings = [r.getMessage() for r in caplog.records if r.name == "api.webhook"]
    assert warnings and not any("SECRET" in w or "token" in w for w in warnings), warnings
    assert any("ValueError" in w for w in warnings), warnings


# --- Idempotency-Key --------------------------------------------------------------------------


class _Counting(SimulatedBooking):
    def __init__(self):
        super().__init__()
        self.books = 0

    def book(self, spec, cart_, session_id):
        self.books += 1
        return super().book(spec, cart_, session_id)


def test_idempotency_key_returns_the_first_voucher(client, monkeypatch):
    booking = _Counting()
    monkeypatch.setattr(cart, "BOOKING", booking)
    reply = _planned(client)
    key = {"Idempotency-Key": "order-42"}
    first = _checkout(client, reply, key)
    assert first.status_code == 200
    # the traveller starts a new trip in the same session; a retry of the old order with
    # the same key still gets the old voucher and books nothing
    sid = reply["session_id"]
    client.post("/api/v1/chat", json={"session_id": sid, "message": "начать заново: Сочи"})
    again = _checkout(client, reply, key)
    assert again.status_code == 200
    assert again.json() == first.json()
    assert booking.books == 1


def test_idempotency_key_with_another_body_is_422(client):
    reply = _planned(client)
    key = {"Idempotency-Key": "order-43"}
    assert _checkout(client, reply, key).status_code == 200
    other = {"session_id": reply["session_id"], "confirm_total_rub": 1}
    response = client.post("/api/v1/checkout", json=other, headers=key)
    assert response.status_code == 422
    assert "Idempotency-Key" in response.json()["detail"]


def test_idempotency_key_after_a_refusal_can_retry(client):
    reply = _planned(client)
    key = {"Idempotency-Key": "order-44"}
    wrong = {"session_id": reply["session_id"], "confirm_total_rub": 1}
    assert client.post("/api/v1/checkout", json=wrong, headers=key).status_code == 409
    assert client.post("/api/v1/checkout", json=wrong, headers=key).status_code == 409
    right = _checkout(client, reply, {"Idempotency-Key": "order-45"})
    assert right.status_code == 200


# --- CORS ---------------------------------------------------------------------------------------


@pytest.fixture
def cors_app(monkeypatch, tmp_path):
    """The same CORS options api.main reads at import, around the app: no module reload."""
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://shop.example, https://b2b.example")
    options = cors_options()
    assert options is not None
    store = SessionStore(tmp_path / "sessions.db")
    api.main.app.dependency_overrides[get_store] = lambda: store
    try:
        yield TestClient(CORSMiddleware(api.main.app, **options))
    finally:
        api.main.app.dependency_overrides.pop(get_store, None)


def test_cors_allows_listed_origins_only(cors_app):
    pre = {
        "Origin": "https://shop.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-api-key,idempotency-key",
    }
    ok = cors_app.options("/api/v1/plan", headers=pre)
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "https://shop.example"
    allowed = ok.headers["access-control-allow-headers"].lower()
    assert "x-api-key" in allowed and "idempotency-key" in allowed
    reply = cors_app.post(
        "/api/v1/plan", json={"message": KAZAN}, headers={"Origin": "https://b2b.example"}
    )
    assert reply.headers["access-control-allow-origin"] == "https://b2b.example"
    other = cors_app.options("/api/v1/plan", headers={**pre, "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_no_cors_headers_without_allowed_origins(client):
    response = client.get("/api/v1/health", headers={"Origin": "https://shop.example"})
    assert "access-control-allow-origin" not in response.headers


# --- Widget and OpenAPI -----------------------------------------------------------------------


def test_embed_js_injects_the_chat_iframe(client):
    response = client.get("/embed.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    js = response.text
    for part in ("iframe", "data-partner", "#FFD800", "#141414", "currentScript"):
        assert part in js
    page = (ROOT / "examples" / "embed.html").read_text()
    assert 'src="http://localhost:8080/embed.js"' in page
    assert "data-partner=" in page


def test_openapi_describes_the_partner_api(client):
    schema = client.get("/openapi.json").json()
    plan = schema["paths"]["/api/v1/plan"]["post"]
    assert plan["summary"] and plan["tags"] == ["Партнёрский API /api/v1"]
    assert plan["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ChatReply"
    )
    assert {"APIKeyHeader": []} in plan["security"]
    assert schema["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-API-Key"
    assert "security" not in schema["paths"]["/api/chat"]["post"]  # the web needs no key
    assert "security" not in schema["paths"]["/api/v1/health"]["get"]
    checkout = schema["paths"]["/api/v1/checkout"]["post"]
    assert "Idempotency-Key" in {p["name"] for p in checkout["parameters"]}
    models = schema["components"]["schemas"]
    assert models["ChatRequest"]["examples"] and models["CheckoutRequest"]["examples"]
    for path, ops in schema["paths"].items():
        for op in ops.values():
            assert op.get("summary"), path
            assert op.get("tags"), path
