"""#197: a question about the month after named dates still applies the other fields.

The dates and the month stay; the answer keeps the question and its button; no consent.
"""

import json
from datetime import date

import pytest

from api import llm
from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore

NULLS = dict.fromkeys(llm.SPEC_FIELDS)


@pytest.fixture(autouse=True)
def today(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 27)

    for module in ("extract", "dag", "model"):
        monkeypatch.setattr(f"api.{module}.date", FixedDate)


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def named(store):
    r = say(store, "Сочи из Москвы с 3 по 8 декабря, двое взрослых")
    assert r.stage == "compare"
    return r


def dates_stay(r):
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2026, 12, 3), date(2026, 12, 8), 12)
    assert r.stage == "compare" and not r.cart.items
    assert not any(c.what in ("даты", "месяц") for c in r.plan.changes)


def changed(r, what):
    return next(c for c in r.plan.changes if c.what == what)


@pytest.fixture
def model(monkeypatch):
    """The model answer is set by the test: model.patch."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1/")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_TIMEOUT_S", "1")

    class Fake:
        def __init__(self):
            self.patch: dict = {}

        def __call__(self, req, timeout=None):
            payload = json.loads(req.data)
            if payload["messages"][0]["content"] == llm.SPEC_PROMPT:
                body = json.dumps({**NULLS, **self.patch}, ensure_ascii=False)
            else:
                body = json.dumps({"explanations": []})
            return Resp({"choices": [{"message": {"content": body}}]})

    fake = Fake()
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    llm.reset()
    yield fake
    llm.reset()


class Resp:
    def __init__(self, obj):
        self.body = json.dumps(obj).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def asks_to_move(r):
    assert "Перенести на январь (тот же день и длина) или назовёте даты?" in r.reply
    assert next(q.options for q in r.questions) == ["Перенести на январь"]


def test_a_budget_in_a_question_about_the_month_is_applied(store):
    first = named(store)
    r = say(store, "может в январе? бюджет 100 тысяч", first.session_id)
    dates_stay(r)
    assert r.plan.spec.budget_rub == 100_000
    assert "100 000" in changed(r, "бюджет").after
    assert "Было → стало" in r.reply
    asks_to_move(r)


def test_a_wish_in_a_question_about_the_month_is_applied(store):
    first = named(store)
    r = say(store, "а если в январе и подешевле?", first.session_id)
    dates_stay(r)
    assert "недорого" in r.plan.spec.wishes
    assert "недорого" in changed(r, "пожелания").after
    asks_to_move(r)


def test_a_length_in_a_contrast_is_applied(store):
    first = named(store)
    r = say(store, "не в январе, давай на полторы недели", first.session_id)
    s = r.plan.spec
    assert s.nights == 7 and s.date_from == date(2026, 12, 3) and s.date_to == date(2026, 12, 10)
    assert s.month == 12 and r.stage == "compare" and not r.cart.items
    assert "Было → стало" in r.reply
    assert "Даты 03.12–10.12 вы назвали сами. Они остаются." in r.reply  # the new period
    assert "Назовите нужные даты или месяц" not in r.reply  # something changed: no general ask
    assert "03.12–08.12 вы назвали" not in r.reply


def test_a_rejection_applies_the_other_fields_too(store):
    first = named(store)
    r = say(store, "январь не подходит, бюджет 100 тысяч", first.session_id)
    dates_stay(r)
    assert r.plan.spec.budget_rub == 100_000 and "Они остаются" in r.reply
    assert "Было → стало" in r.reply


def test_a_message_with_the_month_only_is_answered_as_before(store):
    first = named(store)
    r = say(store, "может в январе?", first.session_id)
    dates_stay(r)
    assert "Было → стало" not in r.reply
    asks_to_move(r)


def test_a_field_with_no_change_leaves_the_month_answer_alone(store):
    first = named(store)
    r = say(store, "может в январе? нас двое", first.session_id)
    dates_stay(r)
    assert "Было → стало" not in r.reply
    asks_to_move(r)


def test_a_field_only_the_model_reads_is_applied_with_the_question(store, model):
    first = named(store)
    model.patch = {"wishes": ["тихо"]}
    r = say(store, "может в январе? нам бы потише", first.session_id)
    dates_stay(r)
    assert "тихо" in r.plan.spec.wishes and r.llm is True
    asks_to_move(r)


def test_the_model_may_not_bring_the_month_back(store, model):
    first = named(store)
    model.patch = {"month": 1, "date_from": "2027-01-03", "date_to": "2027-01-08"}
    r = say(store, "может в январе? бюджет 100 тысяч", first.session_id)
    dates_stay(r)
    assert r.plan.spec.budget_rub == 100_000


@pytest.mark.parametrize(
    "text", ["давай в январе? бюджет 100 тысяч", "хорошо, а в январе? бюджет 100 тысяч"]
)
def test_such_a_message_is_no_consent(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    dates_stay(r)
    assert r.plan.spec.budget_rub == 100_000


def test_the_line_names_the_new_period_after_a_length_change(store):
    first = named(store)
    r = say(store, "может в январе? на 10 ночей", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.nights) == (date(2026, 12, 3), date(2026, 12, 13), 10)
    assert "Даты 03.12–13.12 вы назвали сами. Перенести на январь (тот же день и длина)" in r.reply
    assert "03.12–08.12 вы назвали" not in r.reply
    r = say(store, r.questions[0].options[0], r.session_id)  # the button moves the new dates
    assert (r.plan.spec.date_from, r.plan.spec.date_to) == (date(2027, 1, 3), date(2027, 1, 13))


def test_the_line_names_the_new_period_after_a_shift(store):
    first = named(store)
    r = say(store, "может в январе? бюджет 100 тысяч, на два дня позже", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.budget_rub) == (
        date(2026, 12, 5),
        date(2026, 12, 10),
        100_000,
    )
    assert "Даты 05.12–10.12 вы назвали сами." in r.reply
    assert "03.12–08.12 вы назвали" not in r.reply


def test_a_month_question_at_checkout_does_not_reach_the_model(store, model):
    first = named(store)
    r = say(store, "беру", first.session_id)
    r = say(store, "да", r.session_id)
    assert r.stage == "checkout" and r.cart.total_rub == 45_100
    model.patch = {"budget_rub": 50_000}
    r = say(store, "подтверждаю, может в январе?", r.session_id)
    assert r.stage == "checkout" and r.cart.total_rub == 45_100 and not r.voucher
    assert r.plan.spec.budget_rub is None and r.llm is False
    asks_to_move(r)


# #208: a shift or a rebuild pick (#184) in the same message is not lost, with or without a
# budget. The question about the month stays, and it is no consent.


@pytest.mark.parametrize(
    "text",
    [
        "может в январе? на два дня позже",
        "может в январе? бюджет 100 тысяч, на два дня позже",
        "не в январе, на два дня позже",
    ],
)
def test_a_shift_in_a_month_question_is_applied(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2026, 12, 5), date(2026, 12, 10), 12)
    assert r.stage == "compare" and not r.cart.items
    assert "Было → стало" in r.reply
    assert "05.12" in changed(r, "даты").after
    assert "Даты 05.12–10.12 вы назвали сами." in r.reply
    if text.startswith("может"):
        assert "Перенести на январь (тот же день и длина)" in r.reply
        r = say(store, r.questions[0].options[0], r.session_id)  # the button moves new dates
        assert (r.plan.spec.date_from, r.plan.spec.date_to) == (date(2027, 1, 5), date(2027, 1, 10))


@pytest.mark.parametrize(
    "text",
    ["может в январе? отель получше", "не в январе, отель получше", "может в январе? уже поездом"],
)
def test_a_rebuild_edit_in_a_month_question_is_applied(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2026, 12, 3), date(2026, 12, 8), 12)
    assert r.stage == "compare" and not r.cart.items
    assert "вы назвали сами" in r.reply
    assert "Было → стало" in r.reply and r.plan.selected != first.plan.selected
    if "поездом" in text:
        assert "транспорт: любой → поезд" in r.reply
    if text.startswith("может"):
        assert "Перенести на январь" in r.reply
        assert next(q.options for q in r.questions) == ["Перенести на январь"]


def test_a_shift_and_a_month_question_with_a_model(store, model):
    first = named(store)
    model.patch = {"wishes": ["тихо"]}
    r = say(store, "может в январе? нам бы потише, на два дня позже", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to) == (date(2026, 12, 5), date(2026, 12, 10))
    assert "тихо" in s.wishes and "Даты 05.12–10.12 вы назвали сами." in r.reply
    assert r.stage == "compare"


def test_the_reason_for_no_change_comes_with_the_month_question(store):
    first = named(store)
    r = say(store, "поездом", first.session_id)
    r = say(store, "может в январе? уже поездом", r.session_id)
    assert "Уже поездом" in r.reply and "План не меняю" in r.reply
    assert "Даты 03.12–08.12 вы назвали сами. Перенести на январь" in r.reply
    assert next(q.options for q in r.questions) == ["Перенести на январь"]
    assert r.plan.spec.date_from == date(2026, 12, 3) and r.stage == "compare"
