"""#114: a constraint the user states is not lost on the way to the booking.

Four repros from the outside pool: «на поезде» became a flight without a word, «дней 10» became
7 nights «не названа», «где тепло» was no wish, an English request was not read. Rules only,
no model (TRAP-1).
"""

import pytest

from api import catalog
from api.contracts import ChatRequest
from api.extract import extract
from api.model import chat
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


# --- 1. a mode the data cannot serve is named before the booking ---------------------------


def test_train_to_station_less_city_is_named_not_replaced_silently(store):
    r = say(store, "Едем в Геленджик на поезде в купе, в конце августа, дней 10")
    assert r.stage == "compare"
    assert r.plan.spec.modes == ["train"]
    legs = [o for o in r.plan.offers if o.kind == "transport"]
    assert legs and {o.mode for o in legs} == {"flight"}  # the data has no train there
    line = next(x for x in r.reply.splitlines() if "в данных нет" in x)
    assert "Поезд" in line and "Геленджик" in line and "перелёт" in line
    assert r.reply.index(line) < r.reply.index("Отели:")  # before the offers and the booking


def test_car_to_another_continent_is_named(store):
    r = say(store, "Хотим в Дубай на машине в ноябре на 7 ночей")
    line = next(x for x in r.reply.splitlines() if "в данных нет" in x)
    assert "машин" in line and "Дубай" in line


def test_possible_mode_has_no_such_line(store):
    r = say(store, "Хотим в Казань на поезде в ноябре на 4 ночи")
    assert {o.mode for o in r.plan.offers if o.kind == "transport"} == {"train"}
    assert "в данных нет" not in r.reply


def test_no_mode_asked_has_no_such_line(store):
    assert "в данных нет" not in say(store, "Геленджик в августе на 5 ночей").reply


# --- 2. the length named as «дней 10» -------------------------------------------------------

LENGTHS = [
    ("в конце августа, дней 10", 9),
    ("Сочи на 10 дней", 9),
    ("Сочи на десять дней", 9),
    ("Сочи, 10 дней", 9),
    ("Сочи на 14 дней", 13),
    ("Сочи на четырнадцать дней", 13),
    ("Сочи на 5 суток", 5),
    ("Сочи на 10 ночей", 10),
    ("Сочи на неделю", 7),
    ("Сочи, дней на 10", None),  # «дней на 10» is read by no rule: the model may
]


@pytest.mark.parametrize(("text", "nights"), LENGTHS)
def test_length_forms(text, nights):
    found, _ = extract(text)
    assert found.get("nights") == nights


@pytest.mark.parametrize(
    "text", ["Сочи дней 10 августа", "Сочи, дней 5 тысяч", "Сочи 2 дня 10 августа"]
)
def test_days_before_a_number_is_not_always_a_length(text):
    found, _ = extract(text)
    assert found.get("nights") != 9


def test_days_10_leaves_no_default_length_note(store):
    r = say(store, "Едем в Геленджик в конце августа, дней 10")
    assert r.plan.spec.nights == 9
    assert not any(
        "не названа" in a and a.startswith("длительность") for a in r.plan.spec.assumptions
    )


# --- 3. «тепло» is a wish and the destination follows it -------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Куда-нибудь где тепло в январе, до 6 часов лёту, из Екатеринбурга",
        "Хочу в тёплые края",
        "Хочу тепло зимой",
        "Где-нибудь жарко в феврале",
        "Нужен тёплый климат",
    ],
)
def test_warm_is_a_wish(text):
    assert "тепло" in extract(text)[0].get("wishes", [])


@pytest.mark.parametrize(
    "text",
    [
        "Хочу в Сочи, но не тепло, а прохладно",
        "Сочи в ноябре, нужна тёплая куртка",
        "Тёплое море в ноябре, недорого, с ребёнком",  # the sea wish already carries it
        "Не люблю жару, хочу в горы",
    ],
)
def test_warm_is_not_a_wish_here(text):
    assert "тепло" not in extract(text)[0].get("wishes", [])


def test_demo_example_keeps_its_wishes():
    found, _ = extract("Тёплое море в ноябре, недорого, с ребёнком")
    assert found["wishes"] == ["море", "недорого"]


def test_warm_in_winter_picks_warm_destinations_not_kazan(store):
    r = say(store, "Куда-нибудь где тепло в январе, до 6 часов лёту, из Екатеринбурга")
    assert r.stage == "inspire"  # «тепло» is a wish: no question about the budget first
    names = [d.name for d in r.plan.destinations]
    assert names and not {"Казань", "Санкт-Петербург", "Калининград"} & set(names)


def test_warm_wish_with_one_warm_city_left_still_answers(store):
    r = say(store, "Где тепло в январе, кроме Турции, Египта и ОАЭ")
    assert r.stage == "inspire" and r.plan.destinations  # nothing warm is left: all shown, marked


# --- 4. an English request (TRAP-3: another spelling and language of the same trip) ----------


def test_trap3_english_weekend_in_december():
    found, _ = extract("Weekend in Saint Petersburg from Moscow for two in December")
    assert found["destinations"] == ["Санкт-Петербург"]
    assert found["origin"] == "Москва"
    assert found["month"] == 12
    assert found["nights"] == 2
    assert found["adults"] == 2
    assert found["date_from"].month == 12 and found["date_from"].weekday() == 4  # a Friday


def test_trap3_english_request_is_not_asked_and_not_booked_in_november(store):
    r = say(store, "Weekend in Saint Petersburg from Moscow for two in December")
    assert r.stage == "compare" and not r.questions
    spec = r.plan.spec
    assert spec.destinations == ["Санкт-Петербург"]
    assert (spec.month, spec.nights, spec.date_from.month) == (12, 2, 12)
    assert "ноябр" not in " ".join(spec.assumptions)


@pytest.mark.parametrize(
    ("text", "month", "nights"),
    [
        ("3 nights in Sochi in March", 3, 3),
        ("A week in Dubai in January", 1, 7),
        ("10 days in Istanbul, December", 12, 9),
        ("Trip to Kazan in may for 4 nights", 5, 4),
    ],
)
def test_trap3_english_month_and_length(text, month, nights):
    found, _ = extract(text)
    assert (found["month"], found["nights"]) == (month, nights)
    assert found["destinations"]


def test_english_words_do_not_make_dates():
    found, _ = extract("It may be nice, we will see this week")
    assert "month" not in found and "nights" not in found


# --- review of #138 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "nights"),
    [
        ("Куда поехать в ноябре на неделю, дня 3 на море", 7),  # a named length wins
        ("Сочи, день 3 экскурсия", None),  # the day of a programme, not a length
        ("Сочи, день 3", None),
        ("Калининград на сутки", 1),  # main's rules stay (#132)
        ("Калининград на пару дней", 2),
        ("Сочи на 5 суток", 5),
    ],
)
def test_length_rules_do_not_clash(text, nights):
    assert extract(text)[0].get("nights") == nights


def test_expense_cut_is_read_as_main_reads_it():
    assert extract("Сочи, сократить расходы на 3 дня")[0]["nights"] == 2  # test_duration_words


@pytest.mark.parametrize(
    "text",
    [
        "Куда поехать в декабре? Жару не люблю",  # TRAP-2: the refusal stands after the word
        "Хочу куда-нибудь в декабре, жарко не надо",
        "Тепло не нужно, хочу в горы",
        "Куда поехать в декабре? Не люблю жару",
        "Будет ли тепло в Сочи в ноябре?",
        "Круиз на теплоходе по Волге в июне",
    ],
)
def test_warm_refusal_or_question_is_no_wish(text):
    assert "тепло" not in extract(text)[0].get("wishes", [])


def test_warm_refusal_does_not_pick_warm_places(store):
    r = say(store, "Куда поехать в декабре? Жару не люблю")
    assert "тепло" not in r.plan.spec.wishes
    assert "климату ноября" not in r.reply


def test_warm_reply_shows_the_air_of_the_month(store):
    """«тепло» in January: only places with the January air of catalog.json ≥ 15 °C, number shown."""
    r = say(store, "Куда-нибудь где тепло в январе, до 6 часов лёту, из Екатеринбурга")
    assert len(r.plan.destinations) >= 2
    for d in r.plan.destinations:
        assert not d.violates
        assert d.air_c >= 15 and d.air_c == catalog.month_climate(catalog.resolve(d.name), 1)[0]
        assert f"{d.name} ({d.country})" in r.reply and f"воздух {d.air_c:g} °C" in r.reply
