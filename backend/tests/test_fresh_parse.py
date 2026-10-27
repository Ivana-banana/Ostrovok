"""#302: the classes of parse errors from the 25 fresh requests (eval/cases_fresh25.json).

No model (TRAP-1): the rules read the message. Each class has its F-case in the docstring of a
test and its negatives: the fix is for a class of phrases, not for one phrase.
"""

from datetime import date, timedelta

import pytest

from api.contracts import ChatRequest
from api.extract import extract
from api.model import chat
from api.store import SessionStore

F14 = "Забронируй мне гостиницу на эту пятницу, две ночи"
F15 = "Байкал, лёд, нерпы. Я и жена, 10 дней в марте, тысяч 200 на всё."
F19 = (
    "нужен hotel в Dubai near Marina, 5 nights from Dec 10, 2 adults, all inclusive не надо, "
    "бюджет $3000"
)
F22 = (
    "У меня аэрофобия, самолёт ни за что. Хотим из Перми в Калининград дней на 5 в начале "
    "декабря, вдвоём."
)
F25 = "Хотим с детьми в Таиланд на Новый год, бюджет 400 тысяч, летим из Москвы"
ORIGIN_NOTE = "откуда: Москва (город вылета не назван)"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


# ---------------------------------------------------------------- meal refused after its name


@pytest.mark.parametrize(
    "text",
    [
        "Сочи на 5 ночей, all inclusive не надо",
        "Анталия, всё включено нам не нужно",
        "Анталия, всё включено ни за что",
        "Анталия, олл инклюзив исключён",
        "Анталия без all inclusive",
        "Анталия, не обязательно всё включено",
    ],
)
def test_refused_all_inclusive_is_no_meal_wish(text):
    """F19: «all inclusive не надо» used to give meal AI."""
    found, _ = extract(text)
    assert found.get("meal") != "AI"


def test_f19_refused_all_inclusive_in_chat(store):
    """F19 whole: the plan has no AI meal."""
    assert say(store, F19).plan.spec.meal != "AI"


def test_refused_breakfast_is_room_only():
    found, _ = extract("Казань, 2 ночи, завтрак не нужен")
    assert found.get("meal") == "RO"


@pytest.mark.parametrize(
    "text, meal",
    [
        ("Анталия, хотим всё включено", "AI"),
        ("Анталия, all inclusive, не надо пересадок", "AI"),  # the refusal is of the changes
        ("Казань, завтрак обязателен", "BB"),
        ("Казань без завтрака", "RO"),
        ("Анталия, all inclusive не надо, но завтрак нужен", "BB"),
    ],
)
def test_wanted_meal_stays(text, meal):
    found, _ = extract(text)
    assert found.get("meal") == meal


# ---------------------------------------------------------------- mode refused after its name


@pytest.mark.parametrize(
    "text",
    [
        "Из Перми в Калининград, самолёт ни за что",
        "Из Перми в Калининград, самолёт исключён",
        "Из Перми в Калининград, самолёт нам не нужен",
        "Из Перми в Калининград, самолёт — не вариант",
        "Из Перми в Калининград, лететь не хочу",
        "У меня аэрофобия, из Перми в Калининград",
        "Боюсь летать, из Перми в Калининград",
    ],
)
def test_refused_flight_is_no_flight(text):
    """F22: «самолёт ни за что» used to give the flight mode."""
    found, _ = extract(text)
    assert found["modes"] == ["train", "car"]


def test_f22_no_flight_in_chat(store):
    """F22 whole: the spec bans the flight, the other modes stay. With no train in the data the
    offers say so aloud (offers.py, not this parse)."""
    r = say(store, F22)
    assert r.plan.spec.modes == ["train", "car"]


@pytest.mark.parametrize(
    "text, modes",
    [
        ("Из Москвы в Казань, поезд не хочу, лучше самолёт", ["flight"]),
        ("Из Москвы в Казань на машине не надо", None),  # no pick: the modes stay open
        ("Из Москвы в Казань, поезд, не надо пересадок", ["train"]),
        ("Из Москвы в Казань, летим самолётом", ["flight"]),
    ],
)
def test_other_modes_stay(text, modes):
    found, _ = extract(text)
    assert found.get("modes") == modes


# ---------------------------------------------------------------- the origin is not named


# #351: the question comes from the road share, not from the distance. Бали for 100 000 ₽ on
# all: the cheapest Москва round trip of the offers is over half of it.
BALI = "Бали на 12 ночей вдвоём, перелёт и отель до 100 тысяч"


def test_far_trip_with_a_whole_budget_asks_the_origin(store):
    """A whole budget the road from Москва eats over half of, and no origin: one question about
    the origin, not a silent «Москва»."""
    r = say(store, BALI)
    assert r.stage == "clarify" and len(r.questions) == 1
    q = r.questions[0]
    assert "откуда" in q.text.lower() and q.options[0] == "Из Москвы"
    after = say(store, "из Новосибирска", r.session_id)
    assert after.stage == "compare" and after.plan.spec.origin == "Новосибирск"
    assert after.plan.spec.destinations == r.plan.spec.destinations
    assert after.plan.spec.budget_rub == 100_000 and after.questions == []


def test_f15_road_under_half_is_an_assumption(store):
    """F15 (#351): the Москва round trip is about 28% of 200 000 ₽: the origin is assumed aloud.
    It was a question by the 3000 km rule of #302."""
    r = say(store, F15)
    assert r.stage == "compare" and r.questions == []
    assert ORIGIN_NOTE in r.plan.spec.assumptions


def test_far_trip_origin_option_keeps_the_trip(store):
    """The first option is the old default: the session goes on as before (make eval)."""
    r = say(store, BALI)
    after = say(store, r.questions[0].options[0], r.session_id)
    assert after.stage == "compare" and after.plan.spec.origin == "Москва"
    assert ORIGIN_NOTE not in after.plan.spec.assumptions


@pytest.mark.parametrize(
    "text",
    [
        "Казань на 3 ночи в декабре, двое, бюджет 50 тысяч",  # near: the road is small
        "Токио на 7 ночей в апреле, двое",  # far, but no budget to break
        "Минск 14 ноября на 2 ночи, еду одна",  # F07 class
    ],
)
def test_default_origin_is_said_not_asked(store, text):
    """F07, F12, F16, F24: the default origin is an assumption the user sees."""
    r = say(store, text)
    assert r.stage == "compare" and r.questions == []
    assert ORIGIN_NOTE in r.plan.spec.assumptions
    assert "откуда: Москва (город вылета не назван)" in r.reply


def test_named_origin_far_trip_asks_nothing(store):
    """F08 class: the origin is named, the far trip goes on."""
    r = say(store, "Токио на 7 ночей в апреле, двое, бюджет 600 тысяч, из Москвы")
    assert r.stage == "compare" and r.questions == []


# ---------------------------------------------------------------- the city of a hotel request


def test_hotel_request_without_a_place_asks_the_city(store):
    """F14: «гостиницу на эту пятницу» used to ask the month and the budget."""
    r = say(store, F14)
    assert r.stage == "clarify" and len(r.questions) == 1
    assert "город" in r.questions[0].text.lower()
    assert "бюджет" not in r.reply.lower()
    after = say(store, "Казань", r.session_id)
    assert after.stage == "compare" and after.plan.spec.destinations == ["Казань"]
    assert after.plan.spec.nights == 2


@pytest.mark.parametrize("text", ["Нужен отель на выходные", "Найди жильё на 3 ночи в декабре"])
def test_stay_request_asks_the_city(store, text):
    r = say(store, text)
    assert r.stage == "clarify" and len(r.questions) == 1
    assert "город" in r.questions[0].text.lower()


@pytest.mark.parametrize("text", ["привет", "хочу куда-нибудь в ноябре"])
def test_vague_request_keeps_its_questions(store, text):
    r = say(store, text)
    assert not any("город" in q.text.lower() for q in r.questions)


def test_hotel_by_the_sea_is_a_wish_not_a_city_question(store):
    r = say(store, "Нужен отель у моря в ноябре")
    assert r.stage == "inspire"


def test_this_friday_is_a_date():
    """F14: «на эту пятницу» is the nearest Friday."""
    found, _ = extract(F14)
    today = date.today()
    friday = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    assert found["date_from"] == friday and found["nights"] == 2


# ---------------------------------------------------------------- the children are not counted


def test_children_without_a_count_ask_the_party(store):
    """F25: «с детьми», no number, no ages: one question, not two children of 7."""
    r = say(store, F25)
    assert r.stage == "clarify" and len(r.questions) == 1
    assert "дет" in r.questions[0].text.lower()
    after = say(store, "двое детей 5 и 10 лет", r.session_id)
    assert after.plan.spec.children_ages == [5, 10] and after.stage == "compare"


@pytest.mark.parametrize(
    "text",
    [
        "Сочи в июле, двое взрослых и дети 4 и 7 лет, из Москвы",
        "Сочи в июле с ребёнком 5 лет, двое взрослых, из Москвы",
        "Сочи в июле без детей, двое, из Москвы",
    ],
)
def test_counted_children_ask_nothing(store, text):
    r = say(store, text)
    assert r.stage == "compare" and r.questions == []


def test_one_question_per_turn_the_origin_first(store):
    """The road over half of the whole budget, no origin and «с детьми»: the origin comes first, then nothing
    more is asked (the plan goes on with the children as an assumption)."""
    r = say(store, "Бали на 12 ночей, двое взрослых с детьми, перелёт и отель до 100 тысяч")
    assert len(r.questions) == 1 and "откуда" in r.questions[0].text.lower()
