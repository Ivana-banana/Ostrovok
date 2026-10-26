"""#112: who travels. «Одна», «один», «на троих» used to give adults=2 and «состав не назван».

Without a model (TRAP-1). A wrong guest count is a wrong price and a wrong booking.
"""

import pytest

from api.contracts import ChatRequest
from api.extract import extract
from api.model import chat
from api.store import SessionStore

NOT_NAMED = "взрослых: 2 (состав не назван)"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


# (text, adults, children): the repro of the task and every form of the party.
PARTY = [
    ("Первый раз еду на море с ребёнком, воспитываю одна. Есть 40 тысяч.", 1, 1),
    ("Куда можно поехать девушке одной в сентябре? Обязательно чтоб море было.", 1, 0),
    ("Стамбул на 3 ночи в феврале, один, бюджет 40 тысяч", 1, 0),
    ("Пхукет в марте, 12 ночей, бюджет 250 тысяч на троих взрослых", 3, 0),
    ("Поеду один в Сочи в ноябре", 1, 0),
    ("Хочу в Сочи в ноябре одна", 1, 0),
    ("Поеду одна отдыхать в Сочи", 1, 0),
    ("Один хочу в Сочи в ноябре", 1, 0),
    ("Сочи в ноябре, еду в одиночку", 1, 0),
    ("Сочи в ноябре, вдвоём", 2, 0),
    ("Сочи в ноябре, втроём", 3, 0),
    ("Сочи в ноябре, вчетвером", 4, 0),
    ("Сочи в ноябре, на троих", 3, 0),
    ("Сочи в ноябре, номер на двоих", 2, 0),
    ("Сочи в ноябре, на четверых", 4, 0),
    ("Сочи в ноябре, нас пятеро", 5, 0),
    ("Сочи в ноябре, нас будет трое", 3, 0),
    ("Сочи в ноябре, нас 3", 3, 0),
    ("Сочи в ноябре, нас 6 человек", 6, 0),
    ("Сочи в ноябре, на 4 человека", 4, 0),
    ("Сочи в ноябре, один взрослый", 1, 0),
    ("Сочи в ноябре, нас трое, с ребёнком 4 года", 2, 1),
    ("Сочи в ноябре, с мамой и дочкой 5 лет", 2, 1),
    ("Сочи в ноябре, с подругой и сыном 5 лет", 2, 1),
    ("Сочи в ноябре, одна с ребёнком 4 года", 1, 1),
    ("Сочи в ноябре, нас двое с дочкой 5 лет", 1, 1),
    ("Сочи в ноябре, втроём с собакой", 3, 0),
]


@pytest.mark.parametrize("text,adults,children", PARTY)
def test_party_is_read_from_the_message(text, adults, children):
    found, _ = extract(text)
    assert found["adults"] == adults and len(found.get("children_ages", [])) == children


def test_dog_does_not_hide_the_group():
    found, _ = extract("Сочи в ноябре, втроём с собакой")
    assert found["adults"] == 3 and found["pets"] is True


# A number word that is no party: nights, options, parts of a sentence.
NOT_A_PARTY = [
    "Сочи на одну ночь",
    "Сочи, одна ночь",
    "Сочи на одну неделю",
    "Сочи, одна неделя в ноябре",
    "Один из вариантов — Сочи в ноябре",
    "Одна из причин — море. Сочи в ноябре",
    "Сочи в ноябре, на троих суток",
    "Сочи в ноябре, на двоих суток",
    "Сочи в ноябре, с одной пересадкой",
    "Сочи в ноябре, один день на экскурсии",
    "Сочи в ноябре, один и тот же отель",
    "Сочи в ноябре, хочу один отель на всё время",
    "Сочи в ноябре, один раз в жизни",
    "Сочи в ноябре, в одну сторону",
    "Сочи в ноябре, бюджет одна тысяча",
]


@pytest.mark.parametrize("text", NOT_A_PARTY)
def test_number_word_without_a_party_keeps_the_default(text):
    found, _ = extract(text)
    assert "adults" not in found


def test_nights_stay_nights():
    found, _ = extract("Сочи на одну ночь, один")
    assert found["nights"] == 1 and found["adults"] == 1
    found, _ = extract("Сочи, на троих суток")
    assert "adults" not in found


def test_the_alone_message_prices_for_one_guest(store):
    r = say(store, "Стамбул на 3 ночи в феврале, один, бюджет 40 тысяч")
    spec = r.plan.spec
    assert spec.adults == 1
    assert not any(a.startswith("взрослых") for a in spec.assumptions)


def test_the_mother_alone_keeps_her_child(store):
    r = say(store, "Первый раз еду на море с ребёнком, воспитываю одна. Есть 40 тысяч.")
    spec = r.plan.spec
    assert spec.adults == 1 and len(spec.children_ages) == 1
    assert not r.questions  # the party is known: a question would be a repeat


# An unknown party with a child: the guest count changes the price, so the agent asks.
def test_child_without_a_party_gets_a_question_not_an_assumption(store):
    r = say(store, "С 6 июня в Турцию с дочкой 7 лет")
    assert r.stage == "clarify" and len(r.questions) <= 2
    assert any("едет" in q.text for q in r.questions)
    spec = r.plan.spec
    assert spec.children_ages == [7] and spec.destinations == ["Турция"]


def test_the_answer_to_the_party_question_moves_on(store):
    first = say(store, "С 6 июня в Турцию с дочкой 7 лет")
    option = next(q.options for q in first.questions if "едет" in q.text)[1]
    second = say(store, option, first.session_id)
    assert second.stage != "clarify"
    spec = second.plan.spec
    assert spec.adults == 2 and spec.children_ages == [7]  # the age is not reset to the default
    assert not any(a.startswith("взрослых") for a in spec.assumptions)


@pytest.mark.parametrize(
    "text",
    [
        "Хотим на море в ноябре с ребёнком 5 лет",
        "Едем с ребёнком 3 года в Сочи",
        "Летим с сыном 6 лет в Анталию, я с мужем",
        "Мы с дочкой 7 лет хотим в Турцию",
        "Поедем в Сочи с женой и дочкой 7 лет",
        "Сочи в ноябре с мужем и ребёнком 2 года",
        "Нас трое: мы и ребёнок 5 лет, Сочи",
    ],
)
def test_child_with_a_signal_of_two_adults_is_not_asked(store, text):
    r = say(store, text)
    assert not any("едет" in q.text for q in r.questions)
    assert r.plan.spec.adults == 2


def test_no_child_no_party_question(store):
    r = say(store, "Сочи в ноябре, бюджет 50 тысяч")
    assert not any("едет" in q.text for q in r.questions)
    assert NOT_NAMED in r.plan.spec.assumptions


def test_na_dvoih_with_a_child_is_unknown():
    # «на двоих с ребёнком» may count the child or not: not adults=1, the agent asks.
    found, _ = extract("Сочи в ноябре, на двоих с ребёнком 3 года")
    assert "adults" not in found


def test_i_with_a_child_is_unknown():
    for text in ["Я с ребёнком в Сочи", "Я и двое детей в Сочи"]:
        assert "adults" not in extract(text)[0]


# The headline request has no place yet: the party question must not replace the options.
@pytest.mark.parametrize(
    "text",
    [
        "Тёплое море в ноябре, недорого, с ребёнком",
        "Хочу на море с ребёнком 3 лет в июле",
        "Куда поехать с ребёнком на майские, бюджет 100 тысяч",
    ],
)
def test_child_without_a_place_still_gets_destinations(store, text):
    r = say(store, text)
    assert r.stage == "inspire" and len(r.plan.destinations) > 0
    assert NOT_NAMED in r.plan.spec.assumptions  # the default stays visible
    assert any("едет" in q.text for q in r.questions)  # asked next to the options


def test_party_question_follows_to_the_comparison(store):
    first = say(store, "Тёплое море в ноябре, недорого, с ребёнком")
    second = say(store, first.plan.destinations[0].name, first.session_id)
    assert second.stage == "compare"
    assert any("едет" in q.text for q in second.questions)
    third = say(store, "Двое взрослых", first.session_id)
    assert third.plan.spec.adults == 2 and third.plan.spec.children_ages == [7]
    assert not any("едет" in q.text for q in third.questions)
