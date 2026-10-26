"""Тривиальный ответ из probe (eval/baseline.json): планка, которую система обязана побить."""

from collections import Counter

from app.contracts import Prediction, Request


class MostFrequent:
    def fit(self, labels: list[str]) -> "MostFrequent":
        self.label = Counter(labels).most_common(1)[0][0] if labels else ""
        return self

    def predict(self, request: Request) -> Prediction:
        return Prediction(label=self.label, score=1.0, explanation="самый частый класс")
