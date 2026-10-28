"""Versioned HTTP integration, with the existing clients sharing the same sessions."""

import pytest
from fastapi.testclient import TestClient

from api.main import app, get_store
from api.store import SessionStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ("LLM_API_KEY", "GIGACHAT_AUTH_KEY"):
        monkeypatch.delenv(key, raising=False)
    store = SessionStore(tmp_path / "sessions.db")
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_openapi_describes_both_versions(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    operations = []
    for path, method in (
        ("health", "get"),
        ("chat", "post"),
        ("session/{session_id}", "get"),
        ("cart", "post"),
        ("checkout", "post"),
    ):
        legacy = schema["paths"][f"/api/{path}"][method]
        versioned = schema["paths"][f"/api/v1/{path}"][method]
        if path == "health":
            for operation in (legacy, versioned):
                health = operation["responses"]["200"]["content"]["application/json"]["schema"]
                assert health["type"] == "object"
                assert health["additionalProperties"] is True  # modes since #290
        else:
            assert versioned["responses"] == legacy["responses"]
        assert versioned.get("requestBody") == legacy.get("requestBody")
        operations.extend([legacy["operationId"], versioned["operationId"]])
    assert len(operations) == len(set(operations))


def test_versioned_trip_can_be_restored_by_legacy_client(client):
    response = client.post(
        "/api/v1/chat",
        json={
            "message": "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё."
        },
    )
    assert response.status_code == 200
    reply = response.json()
    assert reply["stage"] == "compare"
    assert reply["llm"] is False
    sid = reply["session_id"]
    assert client.get(f"/api/session/{sid}").json() == reply
    assert client.get(f"/api/v1/session/{sid}").json() == reply
    response = client.post("/api/chat", json={"session_id": sid, "message": "беру"})
    assert response.status_code == 200
    response = client.post("/api/v1/chat", json={"session_id": sid, "message": "да"})
    assert response.status_code == 200
    reply = response.json()
    assert reply["stage"] == "checkout"
    item = reply["cart"]["items"][-1]
    action = {"session_id": sid, "action": "remove", "offer_id": item["offer_id"]}
    removed = client.post("/api/v1/cart", json=action)
    assert removed.status_code == 200
    assert item["offer_id"] not in {x["offer_id"] for x in removed.json()["items"]}
    action["action"] = "add"
    restored = client.post("/api/cart", json=action)
    assert restored.status_code == 200
    payment = {"session_id": sid, "confirm_total_rub": restored.json()["total_rub"]}
    booked = client.post("/api/v1/checkout", json=payment)
    assert booked.status_code == 200
    assert booked.json()["simulated"] is True
    assert client.post("/api/checkout", json=payment).json() == booked.json()


def test_versioned_errors_and_health(client):
    assert client.get("/api/v1/health").json() == client.get("/api/health").json()
    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert client.get("/api/v1/session/absent").status_code == 404
    assert client.post("/api/v1/chat", json={"message": ""}).status_code == 422
