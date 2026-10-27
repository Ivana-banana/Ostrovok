"""#142: a pair «X или Y» keeps both names, also a country and a city outside the catalogue.

Without a model (TRAP-1). A country becomes the city of the catalogue that stands for it
(TRAP-2: «Египет или Турция» is Хургада and Анталия). A name outside the catalogue stays
in `destinations` (TRAP-3 neighbours: the spelling is the user's). The plan covers the first
name; the second one is the chip «План: …» (#139).
"""

import pytest

from api.contracts import ChatRequest
from api.extract import excluded_places
from api.model import chat
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


@pytest.mark.parametrize(
    "text, dests, chips",
    [
        ("Египет или Турция", ["Хургада", "Анталия"], ["План: Анталия"]),
        ("Турция или Египет в ноябре", ["Анталия", "Хургада"], ["План: Хургада"]),
        ("Хочу в Египет или в Турцию", ["Хургада", "Анталия"], ["План: Анталия"]),
        ("Сочи или Ханой", ["Сочи", "Ханой"], []),
        ("Сочи, Анапа или Ханой", ["Сочи", "Анапа", "Ханой"], ["План: Анапа"]),
    ],
)
def test_pair_keeps_both_names_trap_2_trap_3(store, text, dests, chips):
    r = say(store, text)
    assert r.plan.spec.destinations == dests
    assert [o for q in r.questions for o in q.options] == chips


def test_first_name_outside_the_catalogue_gives_the_second_as_a_chip_trap_3(store):
    r = say(store, "Ханой или Сочи")
    assert r.plan.spec.destinations == ["Ханой", "Сочи"]
    assert r.stage == "select"
    assert r.questions[0].options == ["План: Сочи"]
    assert "Сочи — кнопкой ниже" in r.reply
    r = say(store, "План: Сочи", r.session_id)
    assert r.stage == "compare"
    assert r.plan.spec.destinations == ["Сочи"]
    assert "Ханой" in r.reply  # the lost name is told, not silent


def test_pair_chip_plans_the_country_city_trap_2(store):
    r = say(store, "Египет или Турция")
    r = say(store, r.questions[0].options[0], r.session_id)  # «План: Анталия»
    assert r.plan.spec.destinations == ["Анталия"]
    hotels = [o for o in r.plan.offers if o.kind == "hotel"]
    assert hotels and all("Анталия" in o.title for o in hotels)


@pytest.mark.parametrize(
    "text",
    [
        "Хочу в Сочи или что-нибудь потеплее",
        "Сочи или Новый год в горах",
        "Тепло или Дёшево, Сочи",
        "Привет или Здравствуйте",
    ],
)
def test_no_second_name_from_ordinary_words_trap_2(store, text):
    r = say(store, text)
    assert "Новый" not in (r.plan.spec.destinations if r.plan else [])
    assert len(r.plan.spec.destinations if r.plan else []) <= 1


@pytest.mark.parametrize(
    "text, dests",
    [
        ("Нижний Новгород или Казань в ноябре", ["Нижний Новгород", "Казань"]),
        ("Минеральные Воды или Сочи", ["Минеральные Воды", "Сочи"]),
        ("Великий Новгород или Псков", ["Великий Новгород", "Псков"]),
    ],
)
def test_word_inside_a_two_word_city_is_no_second_name_trap_3(store, text, dests):
    r = say(store, text)
    assert r.plan.spec.destinations == dests
    assert not any(o in ("План: Новгород", "План: Воды") for q in r.questions for o in q.options)


def test_origin_in_a_pair_is_no_destination_trap_2(store):
    r = say(store, "Вылет из Казани или Самары, хотим в Турцию в ноябре")
    assert r.plan.spec.destinations == ["Турция"]


# #151: the tails of the pair after the review of #142.
@pytest.mark.parametrize(
    "text, dests",
    [("Экскурсии или Казань", ["Казань"]), ("Сочи или Дети", ["Сочи"])],
)
def test_ordinary_word_in_a_pair_is_no_place_trap_2(store, text, dests):
    r = say(store, text)
    assert r.plan.spec.destinations == dests
    assert "нет в каталоге" not in r.reply


@pytest.mark.parametrize(
    "text, dests",
    [
        ("Роза Хутор или Сочи", ["Роза Хутор", "Сочи"]),
        ("Новый Афон или Сочи", ["Новый Афон", "Сочи"]),
        ("Сочи или Роза Хутор", ["Сочи", "Роза Хутор"]),
    ],
)
def test_two_word_name_outside_the_catalogue_is_whole_trap_3(store, text, dests):
    r = say(store, text)
    assert r.plan.spec.destinations == dests
    assert dests[0] in r.reply or dests[1] in r.reply  # the whole name, not «Хутор»


def test_refusal_does_not_take_the_alternatives_of_the_pair_trap_2(store):
    r = say(store, "Не хочу в Египет, Турция или Кипр")
    assert r.plan.spec.destinations == ["Анталия", "Ларнака"]


def test_refused_place_of_the_pair_gets_no_chip_trap_2(store):
    r = say(store, "Турция или Египет, в Египте уже были")
    assert r.plan.spec.destinations == ["Анталия"]
    assert "План: Хургада" not in [o for q in r.questions for o in q.options]


def test_refusal_in_the_case_of_the_preposition_takes_the_whole_list_trap_2():
    assert excluded_places("Не хочу в Египет, Турцию или Кипр") == ["Египет", "Турция", "Кипр"]
