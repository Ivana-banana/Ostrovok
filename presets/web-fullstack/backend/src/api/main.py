"""FastAPI: /api/* и собранный фронтенд (web/dist) на /."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.contracts import Prediction, PredictRequest
from api.model import predict

app = FastAPI(title="hackathon")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/predict")
def predict_route(req: PredictRequest) -> Prediction:
    return predict(req)


DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
