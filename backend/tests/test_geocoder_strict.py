"""#244: the geocoder answer must be the name that was asked; one time budget per name.

Open-Meteo searches fuzzily: «Тарту» -> Тартус (Syria), «Сиене» -> Сиенегитас (Mexico),
«Кал» -> Кали. A stub `fetch` answers from a table and counts the calls; no network
(TRAP-3, TRAP-6).
"""

from types import SimpleNamespace

import pytest

from api import catalog
from api.extract import extract


def record(name: str, country: str = "Эстония") -> dict:
    return {"id": 9, "name": name, "latitude": 58.4, "longitude": 26.7,
            "timezone": "Europe/Tallinn", "country": country}  # fmt: skip


@pytest.fixture
def stub():
    """Open-Meteo stub: {asked name: [results]} (count is the length cut); `calls` logs the asks."""

    def make(table: dict[str, list[dict]]):
        calls: list[tuple[str, int]] = []
        days = [f"2025-{m:02d}-15" for m in range(1, 13)]

        def fetch(url: str, params: dict) -> dict:
            if url == catalog.GEOCODE:
                calls.append((params["name"], params["count"]))
                return (
                    {"results": table[params["name"]][: params["count"]]}
                    if params["name"] in table
                    else {}
                )
            if url == catalog.ARCHIVE:
                return {"daily": {"time": days, "temperature_2m_max": [10.0] * 12,
                                  "precipitation_sum": [1.0] * 12}}  # fmt: skip
            return {"latitude": 0.0, "longitude": 0.0, "daily": {}}

        return fetch, calls

    return make


@pytest.mark.parametrize(
    ("written", "fuzzy"),
    [("Тарту", "Тартус"), ("Сиене", "Сиенегитас"), ("Кобе", "Кобыла")],
)
def test_trap_3_a_fuzzy_answer_to_the_name_as_written_is_not_a_place(stub, written, fuzzy):
    fetch, _ = stub({written: [record(fuzzy, "Сирия")]})
    assert catalog.lookup_online(written, fetch) is None  # honest «нет данных», no wrong city
    assert fuzzy.lower() not in catalog._online


def test_trap_3_the_right_place_behind_the_fuzzy_first_answer_is_found(stub):
    """«Тарту»: the first answer is Тартус, the second is Тарту (Estonia) — that is the place."""
    fetch, calls = stub({"Тарту": [record("Тартус", "Сирия"), record("Тарту")]})
    place = catalog.lookup_online("Тарту", fetch)
    assert place and place["name"] == "Тарту" and place["country"] == "Эстония"
    assert calls[0] == ("Тарту", 1) and calls[-1] == (
        "Тарту",
        catalog.WIDE_COUNT,
    )  # guesses between


def test_trap_3_a_case_form_answered_in_the_nominative_is_the_same_place(stub):
    fetch, calls = stub({"Бледе": [record("Блед", "Словения")]})
    assert catalog.lookup_online("Бледе", fetch)["name"] == "Блед" and calls == [("Бледе", 1)]


def test_trap_3_a_guess_is_taken_only_when_the_answer_is_the_guess_exactly(stub):
    """«Кал» -> «Кали» is another place; «Ухт» -> «Ухта» too: only «Ухта» is the guess itself."""
    fetch, calls = stub(
        {
            "Ухте": [record("Ухтенино", "Россия")],
            "Ухт": [record("Ухта", "Россия")],
            "Ухта": [record("Ухта", "Россия")],
        }
    )
    place = catalog.lookup_online("Ухте", fetch)
    assert place["name"] == "Ухта" and place["country"] == "Россия"
    assert [c[0] for c in calls][:3] == ["Ухте", "Ухт", "Ухта"]


def test_trap_3_a_guess_answered_by_a_longer_name_is_no_place(stub):
    fetch, _ = stub({"Кал": [record("Кали", "Колумбия")],
                     "Кала": [record("Калама", "Чили")], "Каль": [record("Кальяо", "Перу")]})  # fmt: skip
    assert catalog.lookup_online("Кале", fetch) is None


def test_trap_3_a_name_the_user_wrote_exactly_still_wins(stub):
    """«Кале» is Calais when the geocoder knows it as written: one question, as in #121."""
    fetch, calls = stub({"Кале": [record("Кале", "Франция")], "Кал": [record("Кали", "Колумбия")]})
    assert catalog.lookup_online("Кале", fetch)["country"] == "Франция" and len(calls) == 1


def test_trap_3_chat_does_not_book_a_city_the_user_did_not_name(stub, monkeypatch):
    fetch, _ = stub({"Тарту": [record("Тартус", "Сирия")]})
    monkeypatch.setattr(catalog, "_get", fetch)
    found, _ = extract("Хочу в Тарту в июле")
    assert found["destinations"] == ["Тарту"]
    assert catalog.lookup_online("Тарту") is None  # the plan says «нет данных», not Tartus


@pytest.fixture
def clock(monkeypatch):
    """A fake clock: each geocoder call the stub gets takes `step` seconds."""
    now = [1000.0]
    monkeypatch.setattr(catalog, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


def test_trap_6_one_name_has_one_time_budget_for_all_its_guesses(clock):
    asked: list[str] = []

    def slow(url: str, params: dict) -> dict:
        asked.append(params["name"])
        clock[0] += 4.0  # a slow network: every call takes 4 s
        return {}

    assert catalog.lookup_online("Ухте", slow) is None
    assert len(asked) <= -(-catalog.GEOCODE_BUDGET_S // 4)  # not 4 guesses x 4 s
    assert catalog.GEOCODE_BUDGET_S <= 10


def test_trap_6_after_the_budget_the_name_is_not_asked_again_for_a_while(clock):
    asked: list[str] = []

    def slow(url: str, params: dict) -> dict:
        asked.append(params["name"])
        clock[0] += catalog.GEOCODE_BUDGET_S
        return {}

    catalog.lookup_online("Ухте", slow)
    n = len(asked)
    assert catalog.lookup_online("Ухте", slow) is None and len(asked) == n  # FAILED_S, as offline


def test_trap_6_the_first_network_error_stops_the_search(stub):
    asked: list[str] = []

    def down(url: str, params: dict) -> dict:
        asked.append(params["name"])
        raise OSError("timed out")

    assert catalog.lookup_online("Бледе", down) is None
    assert asked == ["Бледе"]


@pytest.mark.parametrize(
    ("text", "dests"),
    [
        ("Хочу в Горный Алтай в августе", ["Горный Алтай"]),
        ("Хочу на Горный Алтай в июле", ["Горный Алтай"]),
        ("Хочу в Алтай в августе", ["Алтай"]),
        ("Хочу в Горный Алтай или в Сочи", ["Горный Алтай", "Сочи"]),
    ],
)
def test_trap_3_a_two_word_name_with_a_known_word_is_one_destination(text, dests):
    """#244: «Горный Алтай» gave «Горный Алтай» and «Алтай»; the place inside the span is no 2nd."""
    found, _ = extract(text)
    assert found["destinations"] == dests
