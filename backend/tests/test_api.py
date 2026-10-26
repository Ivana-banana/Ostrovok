import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.contracts import ChatReply
from api.main import app

client = TestClient(app)


def test_health():
    # /health требуют правила по технике (task/rules.md); /api/health — e2e пресета.
    for path in ("/health", "/api/health"):
        assert client.get(path).json() == {"status": "ok"}


def test_chat_follows_contract():
    r = client.post("/api/chat", json={"message": "хочу к морю"})
    assert r.status_code == 200
    body = r.json()
    assert {"session_id", "stage", "reply", "plan", "cart", "llm"} <= set(body)
    again = client.post("/api/chat", json={"session_id": body["session_id"], "message": "да"})
    assert again.json()["session_id"] == body["session_id"]


def test_chat_rejects_empty_message():
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_contract_fixture_round_trips():
    # Same fixture as web/src/contracts.test.ts: Python and TS mirrors cannot drift apart.
    path = Path(__file__).resolve().parents[2] / "tests/fixtures/chat_reply.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert ChatReply.model_validate(fixture).model_dump(mode="json") == fixture
