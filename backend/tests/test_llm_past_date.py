"""#164: dates only the model read pass the same past-date check as the rules' dates (#159)."""

import json
from datetime import date

import pytest

from api import llm
from api.contracts import ChatRequest
from api.extract import extract, llm_past_dates
from api.model import chat
from api.store import SessionStore

# No rule reads these words as a place or a date: the model alone brings both.
TEXT = "Махнём куда-нибудь, где тепло, на двоих"
NULLS = dict.fromkeys(llm.SPEC_FIELDS)


@pytest.fixture
def today(monkeypatch):
    """Fix «today» for the extractor and the reply: Tuesday 2026-10-27."""

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 27)

    monkeypatch.setattr("api.extract.date", FixedDate)
    monkeypatch.setattr("api.dag.date", FixedDate)


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


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


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def past_notes(spec):
    return [a for a in spec.assumptions if "уже прошл" in a]


def test_the_rules_read_neither_place_nor_date():
    found, _ = extract(TEXT)
    assert "date_from" not in found and "destinations" not in found


def test_model_dates_of_the_last_month_go_to_next_year_with_a_note(today, model, store):
    model.patch = {"destinations": ["Сочи"], "date_from": "2026-10-20", "date_to": "2026-10-25"}
    r = say(store, TEXT)
    assert r.llm is True
    assert r.plan.spec.date_from == date(2027, 10, 20)
    assert r.plan.spec.date_to == date(2027, 10, 25) and r.plan.spec.nights == 5
    notes = past_notes(r.plan.spec)
    assert len(notes) == 1 and "20.10" in notes[0] and "2027" in notes[0]
    assert "уже прошло" in r.reply


def test_model_dates_far_in_the_past_are_not_applied_and_the_note_says_so(today, model, store):
    model.patch = {"destinations": ["Сочи"], "date_from": "2026-08-20", "date_to": "2026-08-25"}
    r = say(store, TEXT)
    spec = r.plan.spec
    assert spec.destinations == ["Сочи"]  # the rest of the model answer is taken
    assert spec.date_from is not None and spec.date_from >= date(2026, 10, 27)
    assert spec.date_from != date(2027, 8, 20)
    notes = past_notes(spec)
    assert len(notes) == 1 and "от модели 20.08.2026" in notes[0] and "не применены" in notes[0]
    assert "не применены" in r.reply


def test_far_past_note_does_not_reset_the_dates_on_the_next_turn(today, model, store):
    # Dates the user said (not a guess) stay when the model brings dates from the past.
    r = say(store, "Сочи из Москвы с 3 по 8 декабря, двое взрослых")
    assert r.plan.spec.date_from == date(2026, 12, 3)
    model.patch = {"date_from": "2026-08-20", "date_to": "2026-08-25"}
    r = say(store, "Хочу потеплее, чтобы было спокойно", r.session_id)
    assert r.llm is True and len(past_notes(r.plan.spec)) == 1
    assert r.plan.spec.date_from == date(2026, 12, 3)
    model.patch = {}
    r = say(store, "на 4 ночи", r.session_id)
    assert r.plan.spec.date_from == date(2026, 12, 3) and r.plan.spec.nights == 4


def test_far_past_note_goes_when_the_user_names_dates(today, model, store):
    model.patch = {"destinations": ["Сочи"], "date_from": "2026-08-20", "date_to": "2026-08-25"}
    r = say(store, TEXT)
    assert len(past_notes(r.plan.spec)) == 1
    model.patch = {}
    r = say(store, "с 10 по 15 декабря", r.session_id)
    assert r.plan.spec.date_from == date(2026, 12, 10)
    assert not any("не применены" in a for a in r.plan.spec.assumptions)


@pytest.mark.parametrize(
    "text,month", [("давай в декабре", 12), ("лучше в январе", 1), ("давай летом", 6)]
)
def test_month_change_after_far_past_model_dates_moves_the_plan(today, model, store, text, month):
    model.patch = {"destinations": ["Сочи"], "date_from": "2026-08-20", "date_to": "2026-08-25"}
    r = say(store, TEXT)
    assert len(past_notes(r.plan.spec)) == 1
    guess = r.plan.spec.date_from
    assert any("точных дат не было" in a for a in r.plan.spec.assumptions)  # the dates are a guess
    model.patch = {}
    r = say(store, text, r.session_id)
    spec = r.plan.spec
    assert spec.month == month and spec.date_from.month == month != guess.month
    assert not past_notes(spec)  # the old note about the model dates goes with the old dates


def test_far_past_dates_alone_still_mark_the_reply_as_model_made(today, model, store):
    model.patch = {"destinations": ["Сочи"]}
    r = say(store, TEXT)
    model.patch = {"date_from": "2026-08-20", "date_to": "2026-08-25"}
    r = say(store, "Хочу потеплее, чтобы было спокойно", r.session_id)
    assert r.llm is True and len(past_notes(r.plan.spec)) == 1


def test_model_dates_in_the_future_stay_as_they_are_without_a_note(today, model, store):
    model.patch = {"destinations": ["Сочи"], "date_from": "2026-11-20", "date_to": "2026-11-25"}
    r = say(store, TEXT)
    assert r.plan.spec.date_from == date(2026, 11, 20)
    assert r.plan.spec.date_to == date(2026, 11, 25)
    assert not past_notes(r.plan.spec)


def test_dates_of_the_rules_are_not_checked_twice(today, model, store):
    model.patch = {"destinations": ["Сочи"], "date_from": "2026-08-20", "date_to": "2026-08-25"}
    r = say(store, "Сочи из Москвы с 20 по 25 октября, двое взрослых")
    assert r.plan.spec.date_from == date(2027, 10, 20)
    assert len(past_notes(r.plan.spec)) == 1  # the rules' note only


def test_day_boundaries_of_the_window(today):
    for day, moved in ((date(2026, 9, 26), True), (date(2026, 9, 25), False)):
        found = {"date_from": day, "date_to": date(day.year, day.month, day.day + 2)}
        note = llm_past_dates(found)
        assert note and ("взят 2027" in note) == moved
        assert (found.get("date_from") == day.replace(year=2027)) == moved
    today_found = {"date_from": date(2026, 10, 27)}
    assert llm_past_dates(today_found) is None and today_found == {"date_from": date(2026, 10, 27)}
