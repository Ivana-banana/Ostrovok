"""Модель решения. Пока — заглушка демо-пути (TODO: заменить по ADR)."""

from app.contracts import Prediction, Request


def predict(request: Request) -> Prediction:
    # TODO(kickoff): заглушка — сквозной путь работает до появления модели.
    return Prediction(label="unknown", score=0.0, explanation="заглушка: модели ещё нет")
