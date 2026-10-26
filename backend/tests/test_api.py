from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health():
    # /health требуют правила по технике (task/rules.md); /api/health — e2e пресета.
    for path in ("/health", "/api/health"):
        assert client.get(path).json() == {"status": "ok"}


def test_predict_follows_contract():
    r = client.post("/api/predict", json={"text": "пример"})
    assert r.status_code == 200
    assert set(r.json()) == {"label", "score", "explanation", "stub"}


def test_predict_rejects_bad_input():
    assert client.post("/api/predict", json={}).status_code == 422
