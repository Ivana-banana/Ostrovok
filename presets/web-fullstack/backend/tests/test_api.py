from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_predict_follows_contract():
    r = client.post("/api/predict", json={"text": "пример"})
    assert r.status_code == 200
    assert set(r.json()) == {"label", "score", "explanation", "stub"}


def test_predict_rejects_bad_input():
    assert client.post("/api/predict", json={}).status_code == 422
