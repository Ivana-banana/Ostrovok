"""#351: the tails of #302 (review of PR #335). No model (TRAP-1), rules only."""

from datetime import date

import pytest

from api.contracts import ChatRequest
from api.extract import extract
from api.model import chat
from api.store import SessionStore

ORIGIN_NOTE = "откуда: Москва (город вылета не назван)"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def fixed_today(monkeypatch, day: date) -> None:
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(day.year, day.month, day.day)

    monkeypatch.setattr("api.extract.date", FixedDate)


# ---------------------------------------------------------------- 1. fear of flying


@pytest.mark.parametrize(
    "text",
    [
        "У меня аэрофобия, хотим в Калининград",
        "Боюсь летать, поедем в Калининград",
        "Жена боится летать, Калининград",
        "Боимся самолётов, Калининград",
    ],
)
def test_fear_of_flying_bans_the_flight(text):
    assert extract(text)[0]["modes"] == ["train", "car"]


@pytest.mark.parametrize(
    "text",
    [
        "Не боюсь летать, хочу на Бали",
        "Мы не боимся летать, Бали",
        "Аэрофобии нет, Бали",
        "Боюсь летать ночью, Бали",  # a condition: not every flight
        "Боюсь летать с пересадками, Бали",
        "Боюсь летать на маленьких самолётах, Бали",
    ],
)
def test_no_fear_or_a_condition_is_no_ban(text):
    """«не боюсь летать» banned the flight: «Бали» had no plan."""
    assert extract(text)[0].get("modes") in (None, ["flight"])


def test_no_fear_bali_builds_a_plan(store):
    r = say(store, "Не боюсь летать, хочу на Бали в мае на 10 ночей вдвоём, из Москвы")
    assert r.stage == "compare" and r.plan.offers
    assert "flight" not in r.plan.spec.modes or r.plan.spec.modes == ["flight"]


# ---------------------------------------------------------------- 2. double negation


@pytest.mark.parametrize(
    "text",
    [
        "Анталия, без all inclusive не хочу",
        "Анталия, без всё включено не поеду",
        "Анталия, без всё включено не едем",
    ],
)
def test_double_negation_wants_the_meal(text):
    assert extract(text)[0].get("meal") == "AI"


@pytest.mark.parametrize(
    "text",
    [
        "Анталия, без all inclusive",
        "Анталия, all inclusive не хочу",
        "Анталия, всё включено не надо",
    ],
)
def test_single_negation_still_refuses(text):
    assert extract(text)[0].get("meal") != "AI"


# ---------------------------------------------------------------- 3. tickets are no hotel


@pytest.mark.parametrize(
    "text",
    [
        "Забронируй билеты на поезд на пятницу",
        "Купи билеты на самолёт на эту субботу",
        "Забронируй мне что-нибудь на пятницу",
    ],
)
def test_ticket_request_asks_where_not_the_hotel(store, text):
    r = say(store, text)
    assert r.stage == "clarify" and len(r.questions) == 1
    q = r.questions[0].text.lower()
    assert "гостиниц" not in q and "куда" in q


def test_tickets_in_a_budget_ask_nothing_new(store):
    """F09: «90к на двоих вместе с билетами» is a budget, not a booking request."""
    r = say(store, "Дагестан на майские, 90к на двоих вместе с билетами, вылет из Казани")
    assert not any("куда едете" in q.text.lower() for q in r.questions)


@pytest.mark.parametrize("text", ["Забронируй гостиницу на пятницу", "Забронируй отель на 2 ночи"])
def test_hotel_booking_asks_the_city(store, text):
    r = say(store, text)
    assert len(r.questions) == 1 and "гостиниц" in r.questions[0].text.lower()


# ---------------------------------------------------------------- 4. «эта пятница» on a Friday


def test_this_friday_on_a_friday_is_today(monkeypatch):
    fixed_today(monkeypatch, date(2026, 10, 30))  # Friday
    assert extract("отель в Казани на эту пятницу")[0]["date_from"] == date(2026, 10, 30)


def test_next_friday_is_a_week_later(monkeypatch):
    fixed_today(monkeypatch, date(2026, 10, 27))  # Tuesday
    assert extract("отель в Казани на эту пятницу")[0]["date_from"] == date(2026, 10, 30)
    assert extract("отель в Казани на следующую пятницу")[0]["date_from"] == date(2026, 11, 6)


def test_bare_friday_on_a_friday_stays_the_next_one(monkeypatch):
    """«на пятницу» said on a Friday: the next Friday, as before (#302)."""
    fixed_today(monkeypatch, date(2026, 10, 30))
    assert extract("отель в Казани на пятницу")[0]["date_from"] == date(2026, 11, 6)


# ---------------------------------------------------------------- 5. the origin by the road share


def test_road_over_half_of_the_budget_asks_the_origin(store):
    """Бали, 100 000 ₽ on all: the cheapest Москва round trip is over half of it (offers)."""
    r = say(store, "Бали на 12 ночей вдвоём, перелёт и отель до 100 тысяч")
    assert r.stage == "clarify" and len(r.questions) == 1
    assert r.questions[0].options[0] == "Из Москвы"


@pytest.mark.parametrize(
    "text",
    [
        "Хургада в ноябре на неделю вдвоём, 150 тысяч на всё",
        "Анталия в ноябре на неделю вдвоём, 150 тысяч на всё",
    ],
)
def test_egypt_and_turkey_are_alike(store, text):
    """3000 km asked for Хургада (3183 km) and not for Анталия (2159 km); the road share is
    about a third of the budget for both: the origin is an assumption the user sees."""
    r = say(store, text)
    assert r.questions == [] and ORIGIN_NOTE in r.plan.spec.assumptions


def test_without_a_budget_the_origin_is_assumed(store):
    r = say(store, "Бали на 12 ночей вдвоём")
    assert r.questions == [] and ORIGIN_NOTE in r.plan.spec.assumptions
