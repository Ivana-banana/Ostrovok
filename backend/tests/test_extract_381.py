"""#381: a place at the end of a clause after a comma («…, Бали») and after «ради» is the destination.

Only a place of the catalogue counts (its name in any case, its alias, a country with a main
place): an ordinary word in these slots names no place."""

import pytest

from api.extract import extract


@pytest.mark.parametrize(
    "text",
    [
        "Не боюсь летать, Бали",
        "Боюсь летать, но ради Бали готов",
        "Тёплое море в ноябре, Бали.",
        "Не боюсь летать, Бали. Вылет из Москвы",
        "боюсь летать, но ради бали готов",
    ],
)
def test_bali_alias_at_tail_and_after_radi(text):
    assert extract(text)[0].get("destinations") == ["Бали"]


def test_known_city_in_any_case_after_radi():
    assert extract("Боюсь летать, но ради Иркутска готов")[0]["destinations"] == ["Иркутск"]


def test_tail_place_does_not_repeat_a_found_place():
    assert extract("Хочу на море, Сочи")[0]["destinations"] == ["Сочи"]


def test_tail_place_after_origin_is_the_destination():
    found, _ = extract("Летим из Москвы, Бали")
    assert found["origin"] == "Москва" and found["destinations"] == ["Бали"]


@pytest.mark.parametrize(
    "text",
    [
        "Хочу на море в ноябре, пожалуйста",
        "Подберите отель, Спасибо",
        "Хочу к морю, Анна",
        "ради детей готов потерпеть перелёт",
        "ради моря готовы лететь",
        "Хочу тепло, Россия",  # no main place: no destination by a bare country
        "Отель у моря, бар",  # a lower case word is no place at the tail
        # a person name that is also a city: an address, not a place
        "Помогите спланировать поездку на море, Владимир",
        "Подберите отдых на море в ноябре, спасибо, София",
        "Хочу на море с ребёнком, Виктория",
        "Хочу на море, Милана",  # a case form of «Милан» at the tail is a person
        "ради Владимира готов лететь",
        "не ради Турции, а ради моря",
    ],
)
def test_ordinary_word_at_tail_or_after_radi_is_no_place(text):
    assert extract(text)[0].get("destinations") is None


def test_refused_tail_place_is_no_destination():
    assert extract("Хочу на море, но не Бали")[0].get("destinations") is None
