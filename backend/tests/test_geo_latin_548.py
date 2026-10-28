"""#548: «Ушуайя» is unknown to the geocoder in Russian, «Ushuaia» is known (TRAP-3).

The Latin spelling is the last candidate. The answer counts only when its Russian name has
the same Latin skeleton as the name asked. A stub `fetch` answers from a table recorded from
the Open-Meteo geocoder (language=ru); no network.
"""

from types import SimpleNamespace

import pytest

from api import catalog


def record(name: str, country: str = "Аргентина") -> dict:
    # the real answer for «Ushuaia», language=ru, 2026-10-28
    return {"id": 3833367, "name": name, "latitude": -54.81084, "longitude": -68.31591,
            "timezone": "America/Argentina/Ushuaia", "country": country, "country_code": "AR",
            "population": 56825}  # fmt: skip


@pytest.fixture
def stub():
    def make(table: dict[str, list[dict]]):
        calls: list[tuple[str, int]] = []
        days = [f"2025-{m:02d}-15" for m in range(1, 13)]

        def fetch(url: str, params: dict) -> dict:
            if url == catalog.GEOCODE:
                calls.append((params["name"], params["count"]))
                return {"results": table.get(params["name"], [])[: params["count"]]}
            if url == catalog.ARCHIVE:
                return {"daily": {"time": days, "temperature_2m_max": [10.0] * 12,
                                  "precipitation_sum": [1.0] * 12}}  # fmt: skip
            return {"latitude": 0.0, "longitude": 0.0, "daily": {}}

        return fetch, calls

    return make


def test_trap_3_latin_variants_of_a_russian_name():
    assert catalog.latin_variants("Ушуайя") == ["Ushuaia", "Ushuayya"]
    assert catalog.latin_variants("Ялта") == ["Yalta"]
    assert catalog.latin_variants("Ushuaia") == []  # no Cyrillic: nothing to transliterate


def test_trap_3_ushuaia_is_found_by_its_latin_name(stub):
    fetch, calls = stub({"Ushuaia": [record("Ушуая"), record("Ushuaia Aerodrome")]})
    place = catalog.lookup_online("Ушуайя", fetch)
    assert place and place["country"] == "Аргентина" and place["name"] == "Ушуая"
    assert "ушуайя" in place["aliases"]  # asked again by the user's spelling: the same place
    assert len(calls) <= 5 and calls[-1][0] == "Ushuaia"


def test_trap_3_a_case_form_of_the_name_is_found_too(stub):
    fetch, _ = stub({"Ushuaia": [record("Ушуая")]})
    assert catalog.lookup_online("Ушуайю", fetch)["name"] == "Ушуая"


@pytest.mark.parametrize(
    ("written", "latin", "fuzzy"),
    [("Тарту", "Tartu", "Тартус"), ("Ушуайя", "Ushuaia", "Ушуаил"),
     ("Ушуайя", "Ushuaia", "Ushuaia Aerodrome")],
)  # fmt: skip
def test_trap_3_a_fuzzy_latin_answer_is_no_place(stub, written, latin, fuzzy):
    fetch, _ = stub({latin: [record(fuzzy, "Сирия")], "Ushuayya": [record(fuzzy, "Сирия")]})
    assert catalog.lookup_online(written, fetch) is None


def test_trap_3_the_right_record_behind_a_fuzzy_first_one_is_taken(stub):
    fetch, _ = stub({"Ushuaia": [record("Ушуаил", "Сирия"), record("Ушуая")]})
    assert catalog.lookup_online("Ушуайя", fetch)["country"] == "Аргентина"


def test_trap_3_at_most_two_latin_asks_after_the_russian_ones(stub):
    fetch, calls = stub({})
    assert catalog.lookup_online("Ушуайя", fetch) is None
    assert [c[0] for c in calls if c[0].isascii()] == ["Ushuaia", "Ushuayya"]


def test_trap_6_the_latin_asks_fit_the_budget(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(catalog, "time", SimpleNamespace(monotonic=lambda: now[0]))
    asked: list[str] = []

    def slow(url: str, params: dict) -> dict:
        asked.append(params["name"])
        now[0] += 4.0
        return {}

    assert catalog.lookup_online("Ушуайя", slow) is None
    assert len(asked) <= -(-catalog.GEOCODE_BUDGET_S // 4)


def test_trap_6_offline_is_none_after_the_first_failed_ask():
    asked: list[str] = []

    def down(url: str, params: dict) -> dict:
        asked.append(params["name"])
        raise OSError("no network")

    assert catalog.lookup_online("Ушуайя", down) is None
    assert len(asked) == 1
