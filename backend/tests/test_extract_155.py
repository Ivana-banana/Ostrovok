"""#155: tails of the «X или Y» pairs (#142, #151) — a two-word origin, a season, a refusal clause.

Without a model (TRAP-1). The spelling of a place is the user's (TRAP-3): a word that only
continues a name («Нижнего Новгорода», «Москва Сити», «Шри Ланка») is no place of its own.
"""

import pytest
from talk import say

from api.extract import extract
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def test_trap_3_two_word_origin_with_or_leaves_no_novgorod(store):
    r = say(store, "Из Нижнего Новгорода или Казани в Сочи в мае")
    spec = r.plan.spec
    assert spec.origin == "Нижний Новгород"
    assert spec.destinations == ["Сочи"]  # «Казани» is the other origin, not a destination
    assert "Казань — второй вариант вылета" in r.reply  # the second origin is told, not silent


@pytest.mark.parametrize(
    "text, origin, dests",
    [
        ("Из Нижнего Новгорода или Казани в Сочи", "Нижний Новгород", ["Сочи"]),
        ("Из Нижнего Новгорода или Москвы", "Нижний Новгород", []),
        ("Из Великого Новгорода или Казани в Сочи", "Великий Новгород", ["Сочи"]),
        ("Из Казани или Самары в Сочи", "Казань", ["Сочи"]),
        ("Из Москвы или Питера в Сочи", "Москва", ["Сочи"]),
        ("Из Нижнего Новгорода в Сочи", "Нижний Новгород", ["Сочи"]),
    ],
)
def test_trap_3_origin_pair_is_no_destination(text, origin, dests):
    found, _ = extract(text)
    assert found["origin"] == origin
    assert found.get("destinations", []) == dests


def test_destination_pair_of_two_word_city_stays():
    found, _ = extract("Хочу в Нижний Новгород или Казань")
    assert found["destinations"] == ["Нижний Новгород", "Казань"]


def test_trap_3_season_after_a_place_is_no_destination(store):
    r = say(store, "Хочу в Сочи Летом или Анапу")
    assert r.plan.spec.destinations == ["Сочи", "Анапа"]


@pytest.mark.parametrize("word", ["Летом", "Лето", "Зимой", "Осенью", "Весной", "Выходные"])
def test_trap_3_season_word_in_a_pair(word):
    found, _ = extract(f"Хочу в Сочи {word} или Анапу")
    assert found["destinations"] == ["Сочи", "Анапа"]


@pytest.mark.parametrize(
    "text, dests",
    [
        ("Москва Сити или Сочи", ["Москва", "Сочи"]),
        ("Сочи или Москва Сити", ["Сочи", "Москва"]),
    ],
)
def test_trap_3_tail_of_a_name_is_no_place(text, dests):
    assert extract(text)[0]["destinations"] == dests


@pytest.mark.parametrize(
    "text, dests",
    [
        ("Хочу в Турцию Аланья или Кемер", ["Аланья", "Кемер"]),  # #345: the country yields
        ("Хочу в Италию Сиена или Флоренция", ["Сиена", "Флоренция"]),
        ("Сочи Адлер или Анапа", ["Сочи", "Адлер", "Анапа"]),
    ],
)
def test_trap_3_place_after_a_place_stays_a_destination(text, dests):
    """Only a known tail of a name («Сити») is cut off; a resort after a country or a city stays."""
    assert extract(text)[0]["destinations"] == dests


@pytest.mark.parametrize(
    "text, dests",
    [
        ("Шри Ланка или Мальдивы", ["Шри-Ланка", "Мале"]),
        ("Шри Ланка или Сочи", ["Хиккадува", "Сочи"]),  # the city of the catalogue for the country
        ("Сочи или Шри Ланка", ["Сочи", "Хиккадува"]),
    ],
)
def test_trap_3_shri_lanka_is_one_name(text, dests):
    assert extract(text)[0]["destinations"] == dests


@pytest.mark.parametrize(
    "tail",
    [
        "но не дороже 150 тысяч",
        "бюджет не больше 150 тысяч",
        "не люблю жару",
        "и не дороже 150 тысяч",
    ],
)
def test_refusal_stops_at_the_comma_of_the_pair(tail):
    """Only Египет is refused; the pair after the comma is the wish (#151)."""
    found, _ = extract(f"Не хочу в Египет, Турция или Кипр, {tail}")
    assert found["destinations"] == ["Анталия", "Ларнака"]


@pytest.mark.parametrize(
    "tail", ["тоже нет", "не надо", "не интересно", "и Кипр тоже", "и Турция тоже", "не нравится"]
)
def test_refusal_goes_on_after_the_comma(tail):
    found, _ = extract(f"Не хочу в Египет, Турция или Кипр, {tail}")
    assert not found.get("destinations")


def test_refusal_in_the_same_clause_still_refuses_the_pair():
    found, _ = extract("Не хочу в Египет, Турция или Кипр тоже")
    assert not found.get("destinations")


def test_wished_pair_with_a_budget_clause_after():
    found, _ = extract("Хочу в Египет, Турция или Кипр, бюджет не больше 100 тысяч")
    assert found["destinations"] == ["Хургада", "Анталия", "Ларнака"]


def test_trap_3_origin_pair_gives_a_note_with_the_second_origin():
    _, notes = extract("Из Нижнего Новгорода или Казани в Сочи в мае")
    assert any("вылет: Нижний Новгород; Казань — второй вариант вылета" in n for n in notes)


@pytest.mark.parametrize("text", ["Хочу в Шри Ланку", "Хочу в Шри Ланка", "Хочу на Шри Ланку"])
def test_trap_3_shri_lanka_spelling_is_the_country(text):
    assert extract(text)[0]["destinations"] == ["Шри-Ланка"]


@pytest.mark.parametrize(
    "text, dests",
    [
        ("Нижний Тагил или Екатеринбург", ["Нижний Тагил", "Екатеринбург"]),
        ("Хочу в Нижний Тагил или Екатеринбург", ["Нижний Тагил", "Екатеринбург"]),
        ("Екатеринбург или Нижний Тагил", ["Екатеринбург", "Нижний Тагил"]),
        ("Нижний Новгород или Казань", ["Нижний Новгород", "Казань"]),
    ],
)
def test_trap_3_two_word_name_outside_the_catalogue_in_a_pair(text, dests):
    """«Нижний Тагил» is no «Тагилы» of Azerbaijan, the geocoder guess of the bare second word."""
    assert extract(text)[0]["destinations"] == dests


def test_trap_3_origin_of_two_words_in_genitive_is_no_destination():
    found, notes = extract("Из Нижнего Тагила или Екатеринбурга в Сочи")
    assert found["destinations"] == ["Сочи"]
    assert any("Екатеринбург — второй вариант вылета" in n for n in notes)
