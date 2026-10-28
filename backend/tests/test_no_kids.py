"""#195: «без детей» says there are no children. It never adds two children of 7 years.

Without a model (TRAP-1). A wrong guest count is a wrong price and a wrong booking.
"""

import pytest
from talk import say

from api import llm
from api.extract import extract
from api.store import SessionStore

FIRST = "Сочи из Москвы с 3 по 8 декабря, двое взрослых"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


@pytest.mark.parametrize(
    "text",
    [
        "в январе, без детей",
        "без детей в январе",
        "едем без детей",
        "Сочи в ноябре, без маленьких детей",
        "Сочи в ноябре, без ребёнка",
        "Сочи в ноябре, без мужа и детей",
        "Сочи в ноябре, без жены и детей",
        "Сочи в ноябре, без двоих детей",
        "Сочи в ноябре, без своих детей",
        "Сочи в ноябре, без наших детей",
    ],
)
def test_no_kids_extracts_zero_children(text):
    found, notes = extract(text)
    assert found.get("children_ages", []) == []
    assert not any("возраст ребёнка" in n for n in notes)


@pytest.mark.parametrize("text", ["в январе, без детей", "без детей в январе"])
def test_no_kids_keeps_the_party_and_the_total(store, text):
    first = say(store, FIRST)
    again = say(store, text, first.session_id)
    assert first.plan.spec.children_ages == []
    assert again.plan.spec.children_ages == []
    assert again.plan.spec.adults == 2
    assert not any("возраст ребёнка" in a for a in again.plan.spec.assumptions)


def test_no_kids_in_the_first_message(store):
    reply = say(store, "Сочи в ноябре, едем без детей")
    assert reply.plan.spec.children_ages == []


def test_no_kids_removes_the_children_of_the_plan(store):
    first = say(store, "Сочи из Москвы с 3 по 8 декабря, двое взрослых и дочка 5 лет")
    assert first.plan.spec.children_ages == [5]
    again = say(store, "без детей", first.session_id)
    assert again.plan.spec.children_ages == []
    assert again.plan.spec.adults == 2
    assert "состав: 2 взр. + дети 5 лет → 2 взр." in again.reply  # the usual «было → стало»


def test_with_kids_still_adds_children():
    found, _ = extract("Сочи в ноябре, с детьми")
    assert found["children_ages"] == [7, 7]
    found, _ = extract("Сочи в ноябре, с ребёнком 4 года")
    assert found["children_ages"] == [4]


def test_a_question_with_no_kids_changes_nothing():
    found, _ = extract("а без детей можно?", asking=True)
    assert "children_ages" not in found


@pytest.mark.parametrize(
    ("text", "ages"),
    [
        ("Сочи в ноябре, дети без бабушки", [7, 7]),
        ("Сочи в ноябре, без пересадок с детьми", [7, 7]),
        ("Сочи в ноябре, без мужа, с ребёнком 4 года", [4]),
        ("Сочи в ноябре, без доплат ребёнок 5 лет", [5]),
        ("Сочи без пересадок двое детей 5 и 8 лет", [5, 8]),
        ("Сочи без трансфера двое детей 3 и 6 лет", [3, 6]),
        ("Сочи без ночных перелетов двое детей 2 и 5 лет", [2, 5]),
        ("Сочи без доплат двоих детей 4 и 7 лет", [4, 7]),
        ("Сочи без пересадок трое детей", [7, 7, 7]),
        ("Сочи без бассейна детей не повезём", [7, 7]),
        ("Сочи без очередей сына 8 лет и дочь 5 лет", [8, 5]),
        ("Сочи без моря дочери скучно, ей 9", [7]),
        ("Турция без визы ребенка 6 лет возьмём", [6]),
        ("Сочи без мужа и двоих детей 4 и 7 лет", [4, 7]),
    ],
)
def test_other_words_after_bez_keep_the_children(text, ages):
    found, _ = extract(text)
    assert found["children_ages"] == ages


@pytest.mark.parametrize("stage", ["compare", "assemble"])
@pytest.mark.parametrize(
    "text", ["а без детей можно?", "можно без детей?", "беру. а без детей можно?"]
)
@pytest.mark.parametrize("model", [False, True])
def test_question_with_no_kids_at_a_built_plan_changes_nothing(
    monkeypatch, store, stage, text, model
):
    r = say(store, FIRST)
    if stage == "assemble":
        r = say(store, "беру", r.session_id)
    if model:
        monkeypatch.setattr(
            llm,
            "extract_spec",
            lambda m, s, until=None, today=None: {"children_ages": [7, 7]},
        )
    r2 = say(store, text, r.session_id)
    assert "Состав не меняю" in r2.reply
    assert r2.plan.spec == r.plan.spec and r2.cart == r.cart and r2.llm is False


def test_children_named_after_a_bez_phrase_stay_in_the_plan(store):
    r = say(store, f"{FIRST}, без ночных перелетов двое детей 2 и 5 лет")
    assert r.plan.spec.children_ages == [2, 5]


@pytest.mark.parametrize(
    ("text", "ages"),
    [
        ("Сочи в ноябре, без детей не поедем", [7, 7]),
        ("Сочи в ноябре, без ребенка не можем", [7]),
        ("Сочи в ноябре, без сына не поеду", [7]),
        ("Сочи в ноябре, мы без детей не ездим", [7, 7]),
        ("Сочи в ноябре, без детей никуда", [7, 7]),
        ("Сочи в ноябре, без дочери и сына не едем", [7, 7]),
    ],
)
def test_double_negation_keeps_the_children(text, ages):
    found, _ = extract(text)
    assert found["children_ages"] == ages


def test_double_negation_does_not_remove_the_daughter(store):
    first = say(store, "Сочи из Москвы с 3 по 8 декабря, двое взрослых и дочка 5 лет")
    again = say(store, "без дочери не поедем", first.session_id)
    assert len(again.plan.spec.children_ages) >= 1


def test_double_negation_in_the_first_message_books_the_children(store):
    r = say(store, f"{FIRST}, без детей не поедем")
    assert r.plan.spec.children_ages == [7, 7]


# #202: «отель без детей» is a wish for the hotel, not the party. «детей нет» is no children.
HOTEL_WISH = [
    "отель без детей",
    "хочу отель без детей",
    "Сочи в ноябре, хочу отель без детей",
    "нужна гостиница без детей",
    "ищу отель без маленьких детей",
]


@pytest.mark.parametrize("text", HOTEL_WISH)
def test_hotel_without_kids_is_no_change_of_the_party(text):
    found, notes = extract(text)
    assert "children_ages" not in found
    assert not any("возраст ребёнка" in n for n in notes)


@pytest.mark.parametrize("text", HOTEL_WISH)
def test_hotel_without_kids_keeps_the_daughter(store, text):
    first = say(store, "Сочи из Москвы с 3 по 8 декабря, двое взрослых и дочка 5 лет")
    assert first.plan.spec.children_ages == [5]
    again = say(store, text, first.session_id)
    assert again.plan.spec.children_ages == [5]
    assert again.plan.spec.adults == 2


def test_hotel_without_kids_is_named_unsupported():
    _, notes = extract("Сочи в ноябре, хочу отель без детей, дочка 5 лет")
    assert any("без детей" in n and "не учитыва" in n for n in notes)


def test_hotel_wish_with_the_children_named_keeps_them():
    found, _ = extract("Сочи в ноябре, дочка 5 лет, хочу отель без детей")
    assert found["children_ages"] == [5]


@pytest.mark.parametrize(
    "text",
    [
        "Сочи в ноябре, детей нет",
        "Сочи в ноябре, у нас нет детей",
        "Сочи в ноябре, дети не едут",
        "Сочи в ноябре, ребенка нет",
        "Сочи в ноябре, дети с нами не едут",
        "Сочи в ноябре, детей у нас нет",
    ],
)
def test_no_children_phrases_give_zero_children(text):
    found, notes = extract(text)
    assert found.get("children_ages") == []
    assert not any("возраст ребёнка" in n for n in notes)


def test_no_children_phrase_removes_the_children_of_the_plan(store):
    first = say(store, "Сочи из Москвы с 3 по 8 декабря, двое взрослых и дочка 5 лет")
    again = say(store, "детей нет", first.session_id)
    assert again.plan.spec.children_ages == []
    assert again.plan.spec.adults == 2


@pytest.mark.parametrize(
    ("text", "ages"),
    [
        ("Сочи в ноябре, дети без бабушки", [7, 7]),
        ("Сочи в ноябре, с детьми", [7, 7]),
        ("Сочи в ноябре, у нас нет бабушки, двое детей 4 и 6 лет", [4, 6]),
        ("Сочи в ноябре, нет мужа, с ребёнком 4 года", [4]),
        ("Сочи в ноябре, дети едут", [7, 7]),
        ("Сочи в ноябре, дети не против, едем", [7, 7]),
    ],
)
def test_no_children_phrases_do_not_catch_other_words(text, ages):
    found, _ = extract(text)
    assert found["children_ages"] == ages


def test_a_question_about_no_children_changes_nothing():
    found, _ = extract("а если детей нет?", asking=True)
    assert "children_ages" not in found
