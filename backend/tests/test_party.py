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


# #179: a question about children at a built plan adds nobody and keeps the cart.
KIDS_QUESTIONS = [
    "А дети бесплатно?",
    "А с детьми можно?",
    "Детям есть скидка?",
    "дети бесплатно",
    "Можно ли с детьми?",
    "Детям до 12 лет скидка?",
    "Дети до 2 лет бесплатно?",
    "А двое детей бесплатно?",
    "А трое детей можно?",
    "Детям младше 5 лет есть скидка?",
]


def _to_checkout(store):
    r = say(store, "Сочи в ноябре на 5 ночей, вдвоём")
    for msg in ("беру", "да"):
        r = say(store, msg, r.session_id)
    assert r.stage == "checkout" and r.cart.items
    return r


@pytest.mark.parametrize("text", KIDS_QUESTIONS)
def test_kids_question_at_checkout_keeps_party_and_cart(store, text):
    before = _to_checkout(store)
    after = say(store, text, before.session_id)
    assert after.stage == "checkout"
    assert after.plan.spec.children_ages == [] and after.plan.spec.adults == 2
    assert after.cart == before.cart and after.cart.items
    assert "Состав не меняю" in after.reply and ".." not in after.reply


@pytest.mark.parametrize("text", KIDS_QUESTIONS)
def test_kids_question_at_compare_keeps_the_plan(store, text):
    before = say(store, "Сочи в ноябре на 5 ночей, вдвоём")
    assert before.stage == "compare"
    after = say(store, text, before.session_id)
    assert after.plan == before.plan and after.stage == "compare"


def test_kids_question_at_compare_asks_the_age(store):
    before = say(store, "Сочи в ноябре на 5 ночей, вдвоём")
    after = say(store, "Можно с детьми?", before.session_id)
    assert after.plan.spec.children_ages == [] and after.stage == "compare"
    assert "напишите возраст" in after.reply and after.cart == before.cart


def test_kids_question_keeps_existing_children(store):
    before = say(store, "Сочи в ноябре на 5 ночей, вдвоём с ребёнком 4 года")
    after = say(store, "А дети бесплатно?", before.session_id)
    assert after.plan.spec.children_ages == [4] and "дети 4 лет" in after.reply


def test_kids_question_with_an_age_still_adds_the_child(store):
    before = say(store, "Сочи в ноябре на 5 ночей, вдвоём")
    after = say(store, "А с ребёнком 5 лет можно?", before.session_id)
    assert after.plan.spec.children_ages == [5]


def test_kids_question_with_a_threshold_keeps_existing_children_at_checkout(store):
    before = say(store, "Сочи в ноябре на 5 ночей, вдвоём с ребёнком 4 года")
    for msg in ("беру", "да"):
        before = say(store, msg, before.session_id)
    after = say(store, "А двое детей до 12 лет бесплатно?", before.session_id)
    assert after.plan.spec.children_ages == [4] and after.cart == before.cart
    assert after.stage == "checkout"


def test_kids_question_with_other_fields_replans_without_children(store):
    before = say(store, "Сочи в ноябре на 5 ночей, вдвоём")
    after = say(store, "А на 7 ночей с детьми?", before.session_id)
    assert after.plan.spec.nights == 7 and after.plan.spec.children_ages == []


def test_children_statement_in_the_first_message_stays_a_party(store):
    r = say(store, "Тёплое море в ноябре, недорого, с ребёнком")
    assert r.stage == "inspire" and r.plan.spec.children_ages == [7]
    found, _ = extract("Куда поехать с детьми в ноябре?")
    assert found["children_ages"] == [7, 7]


def test_question_about_children_is_not_a_party_at_a_plan():
    found, notes = extract("А дети бесплатно?", True, asking=True)
    assert "children_ages" not in found and notes


# #81 and #179 together: a question at checkout keeps the cart; only a children question
# gets the party answer.
def test_other_question_at_checkout_keeps_the_cart(store):
    before = _to_checkout(store)
    after = say(store, "а отель с завтраком?", before.session_id)
    assert after.stage == "checkout" and after.cart == before.cart
    assert "Это вопрос" in after.reply


def test_kids_question_with_an_age_at_checkout_is_an_edit(store):
    before = _to_checkout(store)
    after = say(store, "А с ребёнком 5 лет можно?", before.session_id)
    assert after.plan.spec.children_ages == [5]  # a real change, as any edit of #81


def test_kids_reply_dots(store):
    before = _to_checkout(store)
    assert "взр. Отдельной" in say(store, "А дети бесплатно?", before.session_id).reply
    kids = say(store, "Сочи в ноябре на 5 ночей, вдвоём с ребёнком 4 года")
    assert "взр. + дети 4 лет. Отдельной" in say(store, "А дети бесплатно?", kids.session_id).reply


# --- #275: a roster in words («я, муж, двое детей, бабушка с дедушкой») is 4 adults, not 2 ---

C1 = (
    "Едем я, муж, двое детей 6 и 9 лет и бабушка с дедушкой, в Анапу на 7 ночей с 1 июля, "
    "поездом из Москвы"
)
SAME_BY_NUMBERS = (
    "Едем 4 взрослых и двое детей 6 и 9 лет в Анапу на 7 ночей с 1 июля, поездом из Москвы"
)


def to_checkout(store, text):
    r = say(store, text)
    r = say(store, "беру", r.session_id)
    r = say(store, "да", r.session_id)
    assert r.stage == "checkout"
    return r


def train_prices(r):
    return [o.price_rub for o in r.cart.items if o.kind == "transport"]


def test_c1_grandparents_are_counted_and_the_train_is_priced_for_the_party(store):
    r = to_checkout(store, C1)
    spec = r.plan.spec
    assert spec.adults == 4 and spec.children_ages == [6, 9]
    assert not any("2 взросл" in a for a in spec.assumptions)
    same = to_checkout(store, SAME_BY_NUMBERS)
    assert train_prices(r) == train_prices(same) and train_prices(r)
    two = to_checkout(store, "Едем вдвоём в Анапу на 7 ночей с 1 июля, поездом из Москвы")
    assert min(train_prices(r)) > 2 * min(train_prices(two))


@pytest.mark.parametrize(
    "text, adults, kids",
    [
        ("Едем я с мамой в Сочи", 2, []),
        ("Мы с женой и тёщей едем в Сочи", 3, []),
        ("Летим в Сочи с мамой 70 лет", 2, []),
        ("Бабушка, дедушка и внук 5 лет едут в Сочи", 2, [5]),
        ("Нас трое взрослых, едем в Сочи", 3, []),
    ],
)
def test_roster_words(text, adults, kids):
    found, _ = extract(text)
    assert found["adults"] == adults
    assert found.get("children_ages", []) == kids


@pytest.mark.parametrize(
    "text",
    [
        "Друг посоветовал Сочи в ноябре",
        "Я хочу в Сочи, подруга говорит, там тепло",
        "С 1 июля в Сочи на неделю",
        "Отправить маму с папой в Кисловодск",
    ],
)
def test_one_role_word_that_is_no_companion_adds_nobody(text):
    found, _ = extract(text)
    assert "adults" not in found


@pytest.mark.parametrize(
    "text",
    ["Хочу в Сочи одна, мама и папа подарили путёвку", "Еду один, без жены и тёщи, в Сочи"],
)
def test_alone_wins_over_role_words(text):
    found, _ = extract(text)
    assert found["adults"] == 1


# Roles after «без», «к», «навестить», as a subject: not companions (#275 B3).
@pytest.mark.parametrize(
    "text",
    [
        "Едем с мамой, без мужа и детей, в Сочи",
        "Едем к бабушке и дедушке в Сочи с мужем",
        "Навестить родителей в Сочи, летим с мужем",
        "Едем с мужем в Сочи, брат посоветовал отель",
    ],
)
def test_roles_outside_the_travel_chain_are_not_counted(text):
    found, _ = extract(text)
    assert found["adults"] == 2


def test_grown_son_with_a_wife_is_no_child():
    found, _ = extract("Сын с женой и внуком едут в Сочи")
    assert found.get("children_ages") == [7]


def test_c1_question_about_grandparents_keeps_the_party(store):
    r = say(store, C1)
    r = say(store, "А бабушке с дедушкой скидка есть?", r.session_id)
    assert r.plan.spec.adults == 4 and r.plan.spec.children_ages == [6, 9]


def test_next_to_grandparents_is_a_hotel_wish_not_a_party(store):
    r = say(store, "Едем 4 взрослых в Сочи на 5 ночей с 1 июля")
    r = say(store, "Хочу отель рядом с бабушкой и дедушкой", r.session_id)
    assert r.plan.spec.adults == 4


# --- #322: «его/её/их» inside the chain; ages without «лет» ---


@pytest.mark.parametrize(
    "text, adults",
    [
        ("Мы с женой и с её родителями едем в Сочи", 4),
        ("Едем с другом и его женой в Сочи", 3),
        ("Я с подругой и её мамой летим в Сочи", 3),
        ("Едем с сестрой и её мужем в Сочи", 3),
    ],
)
def test_possessive_does_not_break_the_chain(text, adults):
    found, _ = extract(text)
    assert found["adults"] == adults


def test_ages_without_a_unit_after_a_counted_child():
    found, _ = extract("Трое детей 3, 5 и 8, едем в Сочи")
    assert found["children_ages"] == [3, 5, 8]


@pytest.mark.parametrize(
    "text",
    [
        "Трое детей, 5 ночей в Сочи",
        "Двое детей, бюджет 100 тысяч",
        "Дети 2 и 3 класса, Сочи",
        "Двое детей 5, 8 ночей в Сочи",
        "Трое детей 5 и 8, Сочи",
        "Сочи с детьми 3 и 4 ноября, на 5 ночей",
        "Едем в Анапу с детьми 5 и 8 июля на неделю",
        "Казань с двумя детьми 1 и 2 мая",
        "Сочи с детьми 2 и 4 звезды",
        "Двое детей 1 и 2 этаж",
        "Сочи с детьми 10 и 12 часов в пути",
        "Сочи с детьми 10 и 12 числа",
        "Сочи с детьми 1, 2 и 3 мая",
        "Сочи с детьми 5, 6 и 7 июля",
        "Сочи с детьми 3, 5 и 8 ночей",
        "Сочи с детьми 3 и 4 ноя",
        "Сочи с детьми 5 и 8 (июль)",
    ],
)
def test_numbers_that_are_not_ages_stay_default(text):
    found, _ = extract(text)
    assert set(found.get("children_ages", [])) <= {7}


def test_grown_son_with_his_wife_is_an_adult_party():
    found, _ = extract("Я с сыном и с его женой едем в Сочи")
    assert found["adults"] == 3 and "children_ages" not in found


def test_ages_in_brackets():
    found, _ = extract("Двое детей (5 и 8), едем в Сочи")
    assert found["children_ages"] == [5, 8]


def test_bracketed_ages_do_not_break_the_kin_chain():
    found, _ = extract("Едем я, муж, двое детей (3 и 11) и бабушка на море в Анапу в июле")
    assert found["adults"] == 3 and found["children_ages"] == [3, 11]


# --- #337: units per age, age adjectives, twins ---


@pytest.mark.parametrize(
    "text, ages",
    [
        ("Дети 1 год и 4 года, едем в Сочи", [1, 4]),
        ("Дети 4 года и 1 год, едем в Сочи", [4, 1]),
        ("С детьми 1 года и 4 лет в Сочи", [1, 4]),
        ("Дети 6 месяцев и 3 года, Сочи", [0, 3]),
        ("С годовалым ребёнком в Сочи", [1]),
        ("С годовалой дочкой в Сочи", [1]),
        ("С полуторагодовалым ребёнком в Сочи", [1]),
        ("Близнецы по 2 года, едем в Сочи", [2, 2]),
        ("Двойняшки 5 лет, Сочи", [5, 5]),
        ("Сын 10, дочь 3 года, Сочи", [10, 3]),
        (
            "Мы с подругой и наши дети: у меня близнецы по 2 года, у неё сын 10. Хотим в Сочи",
            [2, 2, 10],
        ),
    ],
)
def test_337_ages(text, ages):
    found, _ = extract(text)
    assert found["children_ages"] == ages


def test_337_triplets_without_age_are_three_children_with_the_note():
    found, notes = extract("Тройняшки едут в Сочи")
    assert found["children_ages"] == [7, 7, 7]
    assert "возраст ребёнка: 7 лет (возраст не назван)" in notes


@pytest.mark.parametrize(
    "text", ["С сыном 10 июля в Сочи", "С сыном 10.07 в Сочи", "С сыном 10 ночей в Сочи"]
)
def test_337_a_bare_number_after_a_child_that_is_no_age(text):
    found, _ = extract(text)
    assert found["children_ages"] == [7]


@pytest.mark.parametrize(
    "text",
    [
        "Номер с двойной кроватью, едем в Сочи вдвоём",
        "Двойной номер в Сочи, нас двое",
        "Хочу тройной номер в Сочи, нас трое",
        "Двойная кровать нужна, Сочи вдвоём",
    ],
)
def test_337_double_and_triple_adjectives_are_no_children(text):
    found, _ = extract(text)
    assert not found.get("children_ages")


def test_337_double_bed_in_a_dialog_keeps_the_party(store):
    r = say(store, "Едем с женой в Сочи на 5 ночей с 1 июля")
    r = say(store, "Нужен номер с двойной кроватью", r.session_id)
    assert r.plan.spec.adults == 2 and not r.plan.spec.children_ages


@pytest.mark.parametrize(
    "text, ages",
    [
        ("Наши дети: сын 5 лет и дочь 8 лет, едем в Сочи", [5, 8]),
        ("С детьми 5 лет и 8, едем в Сочи", [5, 8]),
    ],
)
def test_337_list_after_children(text, ages):
    found, _ = extract(text)
    assert found["children_ages"] == ages


@pytest.mark.parametrize(
    "text",
    [
        "Едем с ребенком 5 лет и 2 собаки в Сочи",
        "Едем с ребенком 5 лет и 2 бабушки в Сочи",
        "Едем с детьми 5 лет и 2 номера в Сочи",
        "Едем с ребенком 5 лет и 3 чемодана в Сочи",
    ],
)
def test_337_tail_after_a_unit_is_not_a_second_age(text):
    found, _ = extract(text)
    assert found["children_ages"] == [5]


@pytest.mark.parametrize(
    "text, ages",
    [
        ("Дети: 5 и 8 лет, сын любит море, едем в Сочи", [5, 8, 7]),
        ("Дети: 5 и 8 лет. Ребёнок 5 лет плохо переносит перелёт", [5, 8, 5]),
        ("Наши дети: сын 5 лет и дочь 8 лет, едем в Сочи", [5, 8]),
    ],
)
def test_337_children_header_keeps_its_own_ages(text, ages):
    found, _ = extract(text)
    assert found["children_ages"] == ages


@pytest.mark.parametrize(
    "text, ages",
    [
        ("Едем с малышом 1 год и 8 месяцев в Сочи", [1]),
        ("Едем с дочкой 2 года и 3 месяца, Анапа", [2]),
        ("Ребёнок 1 год и 2 месяца, Сочи на 5 ночей", [1]),
        ("Едем с сыном 3 года и 2 месяца в Сочи", [3]),
        ("Дети 6 месяцев и 3 года, Сочи", [0, 3]),
    ],
)
def test_337_years_and_months_of_one_child_are_one_age(text, ages):
    found, _ = extract(text)
    assert found["children_ages"] == ages


def test_337_months_added_in_a_dialog_do_not_add_a_child(store):
    r = say(store, "Едем с женой и сыном 6 лет в Сочи на 5 ночей с 1 июля")
    r = say(store, "сын 6 лет и 3 месяца, это важно?", r.session_id)
    assert r.plan.spec.children_ages == [6]


# --- #341: a head count in words: «с друзьями, 4 человека», «4 чел.», «на 4 персоны» ---


@pytest.mark.parametrize(
    "text, adults, ages",
    [
        ("Поеду в Питербург из Казани на 3 дня с друзьями, 4 человека", 4, None),
        ("Нас 4 человека, едем в Сочи", 4, None),
        ("Сочи, компания из 5 человек", 5, None),
        ("Сочи вчетвером", 4, None),
        ("Сочи впятером", 5, None),
        ("Сочи, 4 чел.", 4, None),
        ("Сочи на 4 персоны", 4, None),
        ("Сочи, 4 гостя", 4, None),
        ("Едем компанией в 4 человека в Сочи", 4, None),
        ("Сочи, 4 человека, из них двое детей 5 и 8", 2, [5, 8]),
        ("Сочи, 2 человека", 2, None),
        ("Сочи, 1 человек", 1, None),
    ],
)
def test_341_head_count(text, adults, ages):
    found, _ = extract(text)
    assert found["adults"] == adults and found.get("children_ages") == ages


@pytest.mark.parametrize(
    "text",
    [
        "4 человека в номере максимум?",
        "Рейтинг 4 человека из 5",
        "Сочи на 4 ночи",
        "Отель 4 звезды в Сочи",
        "Номер до 4 человек, Сочи",
    ],
)
def test_341_numbers_that_are_no_head_count(text):
    found, _ = extract(text)
    assert "adults" not in found


@pytest.mark.parametrize(
    "text, adults, ages",
    [
        ("Сочи, 2 человека и ребёнок 5 лет, на 5 ночей с 1 июля", 2, [5]),
        ("Сочи с ребёнком 5 лет, номер на 2 человек", None, [5]),
        ("Номер на 2 человека, едем с мужем и ребёнком 5 лет в Сочи", 2, [5]),
        ("Сочи, 4 человека с детьми", 2, [7, 7]),
        ("Сочи, 3 человека + ребёнок 4 года", 3, [4]),
    ],
)
def test_341_children_are_inside_the_count_only_when_the_text_says_so(text, adults, ages):
    found, notes = extract(text)
    assert found.get("adults") == adults and found["children_ages"] == ages
    if text.endswith("с детьми"):
        assert "взрослых: 2 (из «4 человека», дети в счёте)" in notes


def test_341_six_guests_note():
    found, notes = extract("Нужен номер на 6 гостей в Сочи")
    assert found["adults"] == 6 and "взрослых: 6 (по «6 гостей»)" in notes
