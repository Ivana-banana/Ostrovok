"""Логика решения без ROS: тестируется на хосте (tests/) и в colcon test (test/).

Пока — заглушка: экспоненциальное сглаживание входа (TODO: заменить по ADR).
"""


class Smoother:
    def __init__(self, alpha: float = 0.5) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        self.alpha = alpha
        self.value: float | None = None

    def update(self, x: float) -> float:
        self.value = x if self.value is None else self.alpha * x + (1 - self.alpha) * self.value
        return self.value
