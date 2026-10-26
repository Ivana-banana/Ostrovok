"""Модель решения. Пока — заглушка демо-пути (TODO: заменить по ADR)."""

from api.contracts import Prediction, PredictRequest


def predict(req: PredictRequest) -> Prediction:
    # TODO(kickoff): заглушка — сквозной путь демо работает до появления модели.
    return Prediction(label="unknown", score=0.0, explanation="заглушка: модели ещё нет", stub=True)
