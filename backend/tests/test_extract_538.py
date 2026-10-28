"""#538: names of places in the rule extractor (TRAP-3, TRAP-2). Without a model (TRAP-1).

«Бакуриани» is a resort, not «Баку». «в Лазаревском» is «Лазаревское». «Новый Афон» and
«Новый Уренгой» are names, «Новый год» is not. The origin outside the catalogue is nominative.
"""

import pytest

from api.extract import extract


def dests(text):
    return extract(text)[0].get("destinations")


@pytest.mark.parametrize(
    ("text", "city"),
    [
        ("Хочу в Бакуриани в феврале", "Бакуриани"),
        ("Бакуриани на 3 дня", "Бакуриани"),
        ("Поеду в Байкальск в июле", "Байкальск"),
        ("Горно-Алтайск в мае", "Горно-Алтайск"),
        ("Хочу в Баку в мае", "Баку"),
        ("Баку в мае", "Баку"),
        ("Отдых в Лазаревском в июле", "Лазаревское"),
        ("Лазаревское на 3 дня", "Лазаревское"),
        ("Хочу в Лазаревское", "Лазаревское"),
        ("Хочу в Новый Афон в мае", "Новый Афон"),
        ("Новый Афон на 3 дня", "Новый Афон"),
        ("Хочу в Новом Афоне в мае", "Новый Афон"),
        ("Новый Уренгой на 3 дня", "Новый Уренгой"),
        ("Поеду в Новый Уренгой в январе", "Новый Уренгой"),
        ("Сочи или Новый Афон", None),
    ],
)
def test_trap_3_name_of_a_place(text, city):
    if city is None:
        assert "Новый Афон" in dests(text)
    else:
        assert dests(text) == [city]


@pytest.mark.parametrize(
    "text",
    [
        "Новый год на 3 дня",
        "Новый Год на 3 дня",
        "Новые места на 3 дня",
        "Новый отель на 3 дня",
        "Хочу в новый отель на 3 дня",
        "Хочу на Новый год в мае",
        "Хочу на Новый Год на 5 дней",
        "Новая жизнь на 3 дня",
        "Сухой на 3 дня",
    ],
)
def test_trap_3_new_is_no_place(text):
    assert "destinations" not in extract(text)[0]


@pytest.mark.parametrize(
    "text",
    ["Новый год в Сочи", "Хочу на Новый год в Сочи", "На Новый Год в Сочи", "Новый год: Сочи"],
)
def test_trap_3_new_year_keeps_the_city(text):
    assert dests(text) == ["Сочи"]


@pytest.mark.parametrize(
    ("text", "origin"),
    [
        ("Из Нижнего Тагила в Сочи", "Нижний Тагил"),
        ("Из Старого Оскола в Сочи", "Старый Оскол"),
        ("Вылет из Нового Уренгоя в Сочи", "Новый Уренгой"),
        ("Из Лазаревского в Сочи", "Лазаревское"),
        ("Из Москвы в Сочи", "Москва"),
        ("Из Нижнего Новгорода в Сочи", "Нижний Новгород"),
    ],
)
def test_trap_3_origin_is_nominative(text, origin):
    found = extract(text)[0]
    assert found["origin"] == origin
    assert found["destinations"] == ["Сочи"]
