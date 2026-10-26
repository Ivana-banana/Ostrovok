"""Контракт API. Потребители: web/src/contracts.ts (зеркало), web/e2e, checks.py, tests.

Изменение — отдельный PR с меткой contract; web/src/contracts.ts меняется в том же PR.
"""

from pydantic import BaseModel


class PredictRequest(BaseModel):
    text: str


class Prediction(BaseModel):
    label: str
    score: float
    explanation: str
    stub: bool
