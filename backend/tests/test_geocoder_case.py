"""#121: a place outside the catalogue reaches the geocoder in the nominative (TRAP-3, S5).

«в Старую Ладогу» asked «Старую», «в Бледе» asked «Бледе»; Open-Meteo answered nothing and the
chat said the place is not in the catalogue. A fake `fetch` records what the geocoder is asked.
"""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from talk import say

from api import catalog
from api.extract import extract
from api.guards import name_candidates
from api.store import SessionStore

ANSWERS = Path(__file__).resolve().parents[2] / "eval" / "geocoder_answers.json"


@pytest.fixture
def spy(geo, monkeypatch):
    """Open-Meteo knows one place; `asked` lists every name the geocoder gets, in order."""

    def make(found: str):
        asked: list[str] = []
        inner = geo(found)

        def fetch(url: str, params: dict) -> dict:
            if url == catalog.GEOCODE:
                asked.append(params["name"])
            return inner(url, params)

        monkeypatch.setattr(catalog, "_get", fetch)
        return asked

    return make


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("Хочу в Старую Ладогу в августе", "Старая Ладога"),
        ("Едем в Старой Ладоге на 2 дня", "Старая Ладога"),
        ("Хочу в Бледе в июне", "Блед"),
        ("Хочу в Сиену в апреле", "Сиена"),
        ("Едем в Чешском Крумлове на 3 ночи", "Чешский Крумлов"),
    ],
)
def test_trap_3_geocoder_gets_the_nominative_not_the_case_form(spy, text, query):
    asked = spy(query)
    found, _ = extract(text)
    assert query in asked
    assert found["destinations"] == [query]  # the chat says «Старая Ладога», not «Старую»


@pytest.fixture
def knows(geo, monkeypatch):
    """Open-Meteo that knows the given names: {asked name: name in the answer}; logs the asked."""

    def make(answers: dict[str, str]):
        asked: list[str] = []
        inner = geo("-")

        def fetch(url: str, params: dict) -> dict:
            if url != catalog.GEOCODE:
                return inner(url, params)
            asked.append(params["name"])
            if params["name"] not in answers:
                return {}
            return geo(params["name"], answers[params["name"]])(url, params)

        monkeypatch.setattr(catalog, "_get", fetch)
        return asked

    return make


@pytest.mark.parametrize(
    ("written", "wrong", "wrong_answer"),
    [("Кале", "Кал", "Кали"), ("Кобе", "Коб", "Кобы"), ("Мальме", "Мальм", "Мальма")],
)
def test_trap_3_the_name_as_written_is_asked_before_any_guess(knows, written, wrong, wrong_answer):
    """«Кале» is Calais, not a case of the town «Кали»: the geocoder knows «Кале» as it is."""
    asked = knows({written: written, wrong: wrong_answer})
    place = catalog.lookup_online(written)
    assert place["name"] == written and asked == [written]
    assert set(catalog._online) == {catalog.norm(written)}  # no wrong entry under another key


def test_trap_3_an_answer_to_a_guess_must_be_the_guess_itself(knows):
    """The guess «Сиена» gets the answer «Сиенита»: another place, never taken."""
    asked = knows({"Сиена": "Сиенита"})
    assert catalog.lookup_online("Сиену") is None
    assert asked[0] == "Сиену" and "Сиена" in asked
    assert "сиену" not in catalog._online or catalog._online["сиену"] is None
    assert "сиенита" not in catalog._online


@pytest.mark.parametrize("name", ["Малибу", "Сан-Паулу", "Гонолулу", "Тарту"])
def test_trap_3_a_name_without_a_case_form_keeps_its_spelling(knows, name):
    """Names in -у that are no accusative («в Малибу») reach the geocoder as written."""
    asked = knows({name: name})
    found, _ = extract(f"Хочу в {name} в июле")
    assert found["destinations"] == [name] and asked == [name]


def test_trap_3_the_whole_name_stays_together_and_the_adjective_is_not_the_city(spy):
    asked = spy("Старая Ладога")
    found, _ = extract("Хочу в Старую Ладогу в августе")
    assert "Старую" not in asked and "Старая" not in asked
    assert found["destinations"] == ["Старая Ладога"]


def test_trap_3_a_case_form_is_tried_in_order_until_the_geocoder_answers(spy):
    """«Бледе» can be Блед, Бледа or Бледь: the geocoder is asked until one is known."""
    asked = spy("Бледа")
    place = catalog.lookup_online("Бледе")
    assert place["name"] == "Бледа" and asked == ["Бледе", "Блед", "Бледа"]


def test_trap_3_a_guess_that_is_another_place_is_not_taken(geo, monkeypatch):
    """«Старую» is not a case of «Стара-Загора»: no guess becomes a place of other letters."""
    monkeypatch.setattr(catalog, "_get", geo("Старая", "Стара-Загора"))
    assert catalog.lookup_online("Старую") is None


@pytest.mark.parametrize(
    ("name", "first"),
    [
        ("Старую Ладогу", "Старая Ладога"),
        ("Старой Ладоге", "Старая Ладога"),
        ("Красной Поляне", "Красная Поляна"),
        ("Чешском Крумлове", "Чешский Крумлов"),
        ("Бледе", "Блед"),
        ("Сиену", "Сиена"),
        ("Ухте", "Ухт"),
        ("Италии", "Италия"),
        ("Тихвин", "Тихвин"),
        ("Чески-Крумлов", "Чески-Крумлов"),
    ],
)
def test_trap_3_name_candidates_start_with_the_likeliest_nominative(name, first):
    assert name_candidates(name)[0] == first


def test_trap_3_name_candidates_end_with_the_name_as_written_and_do_not_repeat():
    assert name_candidates("Ухте") == ["Ухт", "Ухта", "Ухть", "Ухте"]
    assert name_candidates("Тихвин") == ["Тихвин"]
    assert len(name_candidates("Старой Ладоге")) <= 6


def test_trap_3_gender_must_agree_in_a_two_word_name():
    got = name_candidates("Старую Ладогу")
    assert got == ["Старая Ладога", "Старую Ладогу"]  # not «Старый Ладог»


def test_trap_3_chat_books_the_place_asked_in_a_case_form(tmp_path, spy):
    asked = spy("Старая Ладога")
    store = SessionStore(tmp_path / "s.db")
    r = say(store, "Хочу в Старую Ладогу в августе")
    assert r.plan.spec.destinations == ["Старая Ладога"]
    assert "нет в каталоге" not in r.reply and "Старую" not in r.reply
    assert asked[0] == "Старая Ладога"  # the name is already the nominative: one question


def test_trap_3_chat_says_the_nominative_when_the_geocoder_is_off(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    r = say(store, "Хочу в Старую Ладогу в августе")
    assert "«Старая Ладога» пока нет в каталоге" in r.reply


def test_trap_3_recorded_geocoder_answers_know_staraya_ladoga_and_bled():
    """eval/geocoder_answers.json: make eval replays these instead of the network (TRAP-6)."""
    answers = json.loads(ANSWERS.read_text(encoding="utf-8"))["answers"]
    asked = {
        parse_qs(urlparse(url).query)["name"][0]: body
        for url, body in answers.items()
        if urlparse(url).path.endswith("/search")
    }
    for name in ("Старая Ладога", "Блед"):
        assert asked[name].get("results"), name
