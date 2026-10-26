from app.baseline import MostFrequent
from app.contracts import Request
from app.model import predict


def test_model_returns_explained_prediction():
    p = predict(Request(text="любой текст"))
    assert p.explanation


def test_baseline_is_most_frequent_label():
    b = MostFrequent().fit(["a", "b", "a"])
    assert b.predict(Request(text="x")).label == "a"
