"""#544: the origin city in the nominative, and «а не из X» is a refusal (TRAP-3, TRAP-1).

Without a model. The origin outside the catalogue goes to the geocoder and to the reply in the
nominative: «Кинешмы» -> «Кинешма». «Из Москвы, а не из Питера» does not make Питер a destination.
"""

import pytest
from talk import say

from api import catalog
from api.extract import extract
from api.store import SessionStore


def parsed(text):
    found = extract(text)[0]
    return found.get("origin"), found.get("destinations")


@pytest.mark.parametrize(
    ("text", "origin"),
    [
        ("Из Кинешмы в Сочи в мае", "Кинешма"),
        ("Вылет от Кинешмы, хочу в Сочи", "Кинешма"),
        ("Из Ухты в Анталью", "Ухта"),
        ("Из Сызрани в Сочи", "Сызрань"),
        ("Из Бийска в Сочи", "Бийск"),
        ("Не из Москвы, а из Кинешмы в Сочи", "Кинешма"),
    ],
)
def test_trap_3_origin_outside_the_catalogue_is_nominative(text, origin):
    assert parsed(text) == (origin, ["Сочи"] if "Анталью" not in text else ["Анталия"])


@pytest.mark.parametrize(
    ("text", "origin"),
    [
        ("Из Москвы в Сочи в мае", "Москва"),
        ("Из Казани в Сочи", "Казань"),
        ("Из Перми в Сочи", "Пермь"),
        ("Из Твери в Сочи", "Тверь"),
        ("Из Нижнего Новгорода в Сочи", "Нижний Новгород"),
        ("Из Нового Уренгоя в Сочи", "Новый Уренгой"),
        ("Из Йошкар-Олы в Сочи", "Йошкар-Ола"),
        ("Из Алматы в Сочи", "Алматы"),
        ("Из Баку в Сочи", "Баку"),
        ("Из Тарту в Сочи", "Тарту"),
        ("Из Хельсинки в Сочи", "Хельсинки"),
    ],
)
def test_trap_3_catalogue_and_indeclinable_origins_stay(text, origin):
    assert parsed(text) == (origin, ["Сочи"])


def test_trap_3_the_reply_names_the_origin_in_the_nominative(tmp_path):
    spec = say(SessionStore(tmp_path / "s.db"), "Из Кинешмы в Сочи в ноябре").plan.spec
    assert spec.origin == "Москва"  # offline (TRAP-6): the fallback hub
    assert any("«Кинешма» не нашёл" in a for a in spec.assumptions), spec.assumptions
    assert catalog.origin_hub("Кинешма", fetch=lambda url, params: {})[1].startswith(
        "Город вылета «Кинешма» не нашёл"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Из Москвы, а не из Питера, в Сочи",
        "Из Москвы, а не из Санкт-Петербурга, в Сочи",
        "Из Москвы а не из Казани, в Сочи",
        "Из Москвы, не из Питера, в Сочи",
        "Из Москвы, а не из Нижнего Новгорода, в Сочи",
        "Из Москвы, а не из Кинешмы, в Сочи",
    ],
)
def test_trap_3_a_refused_second_origin_is_no_destination(text):
    assert parsed(text) == ("Москва", ["Сочи"])


def test_trap_3_a_refused_origin_alone_leaves_the_sea_request_open():
    assert parsed("Из Москвы, а не из Питера, на море в мае") == ("Москва", None)


@pytest.mark.parametrize(
    "text",
    [
        "Не из Санкт Петербурга, а из Москвы, в Сочи",
        "Не из Санкт-Петербурга, а из Москвы, в Сочи",
        "Не из Питера, а из Москвы, в Сочи",
    ],
)
def test_trap_3_refused_saint_petersburg_with_or_without_the_hyphen(text):
    assert parsed(text) == ("Москва", ["Сочи"])


def test_trap_3_saint_petersburg_without_the_hyphen_is_one_origin():
    assert parsed("Из Санкт Петербурга в Сочи") == ("Санкт-Петербург", ["Сочи"])


def test_trap_3_two_origins_without_a_refusal_keep_the_first():
    assert parsed("Из Москвы или из Питера в Сочи") == ("Москва", ["Сочи"])
