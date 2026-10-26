"""Контракты между модулями: вход и выход модели. Потребители — demo, checks, tests.

Изменение — отдельный PR с меткой contract и списком потребителей.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Request:
    text: str


@dataclass(frozen=True)
class Prediction:
    label: str
    score: float
    explanation: str
