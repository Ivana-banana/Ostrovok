"""#436: the origin is asked first, not assumed silently as Москва.

No model (TRAP-1): the rules read the messages. The answer spellings are TRAP-3.
"""

import pytest

from api.contracts import ChatRequest
from api.model import ORIGIN_ASK, SAME_PLACE, chat
from api.store import SessionStore

CHIPS = ["Из Москвы", "Из Санкт-Петербурга", "Из Екатеринбурга", "Из Новосибирска", "Из Казани"]
ORIGIN_NOTE = "откуда: Москва (город вылета не назван)"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def origin_q(r):
    return next((q for q in r.questions if q.text.startswith(ORIGIN_ASK)), None)


@pytest.mark.parametrize(
    "text",
    [
        "Казань на 3 ночи в декабре, двое, бюджет 50 тысяч",
        "Токио на 7 ночей в апреле, двое",
        "Тёплое море в ноябре, недорого, с ребёнком",
        "Хочу отдохнуть",
    ],
)
def test_unnamed_origin_is_the_first_question(store, text):
    """The bug: the plan came at once with «откуда: Москва» as an assumption."""
    r = say(store, text)
    assert r.stage == "clarify"
    assert r.questions[0].text.startswith(ORIGIN_ASK)
    assert r.questions[0].options[: len(CHIPS)] == CHIPS
    assert ORIGIN_NOTE not in r.plan.spec.assumptions
    assert len(r.questions) <= 2  # one turn, not an interrogation


@pytest.mark.parametrize(
    "answer, origin",
    [
        ("Из Санкт-Петербурга", "Санкт-Петербург"),
        ("из Москвы", "Москва"),
        ("Питер", "Санкт-Петербург"),
        ("мск", "Москва"),
        ("из мск", "Москва"),
        ("Масква", "Москва"),  # TRAP-3: a typo
        ("из Масквы", "Москва"),
        ("Новосибирк", "Новосибирск"),
        ("екб", "Екатеринбург"),
        ("Казань", "Казань"),
    ],
)
def test_answer_keeps_the_trip_and_asks_nothing_again(store, answer, origin):
    """TRAP-3: the spellings of the answer are normalised; the rest of the first message stays."""
    first = say(store, "Сочи на 4 ночи в июле, двое взрослых, бюджет 120 тысяч")
    assert origin_q(first)
    r = say(store, answer, first.session_id)
    assert r.stage == "compare" and r.questions == []
    s = r.plan.spec
    assert s.origin == origin and s.destinations == ["Сочи"]
    assert (s.nights, s.adults, s.budget_rub, s.month) == (4, 2, 120_000, 7)
    assert not any(a.startswith("откуда") for a in s.assumptions)


def test_origin_named_asks_nothing(store):
    r = say(store, "Сочи на 4 ночи в июле, двое взрослых, из Казани")
    assert r.stage == "compare" and not origin_q(r)


def test_own_tickets_ask_no_origin(store):
    """C5 (#408): own tickets need no departure city."""
    r = say(store, "Сочи с 14 ноября на 4 ночи, двое взрослых, билеты уже куплены")
    assert not origin_q(r) and r.stage != "clarify"


def test_origin_goes_first_next_to_the_children(store):
    """F25 class: the other question comes in the same turn, after the origin."""
    r = say(store, "Хотим с детьми в Таиланд на Новый год, бюджет 400 тысяч")
    assert [q.text.startswith(ORIGIN_ASK) for q in r.questions] == [True, False]
    assert "детей" in r.questions[1].text
    after = say(store, "Из Казани, двое детей 5 и 10 лет", r.session_id)
    assert after.plan.spec.origin == "Казань" and after.plan.spec.children_ages == [5, 10]
    assert after.stage != "clarify"


def test_vague_request_asks_origin_with_the_month(store):
    """Two questions at most: the origin and the month; the budget becomes an assumption."""
    r = say(store, "Хочу отдохнуть")
    assert [q.text for q in r.questions][1:] == ["Когда хотите поехать?"]
    after = say(store, "Питер, декабрь", r.session_id)
    assert after.stage == "inspire" and after.plan.spec.origin == "Санкт-Петербург"
    assert after.plan.spec.month == 12
    assert "Санкт-Петербург" not in after.plan.spec.destinations


def test_destination_answer_is_not_the_origin(store):
    """«в Сочи» answers no origin question: the place keeps its preposition."""
    r = say(store, "Хочу отдохнуть")
    after = say(store, "в Сочи", r.session_id)
    assert after.plan.spec.destinations == ["Сочи"]
    assert after.plan.spec.origin == "Москва" and ORIGIN_NOTE in after.plan.spec.assumptions


def test_unknown_answer_falls_back_aloud(store):
    """«не знаю» names no city: Москва stays an assumption the user sees, no second question."""
    first = say(store, "Сочи на 4 ночи в июле, двое взрослых")
    r = say(store, "не знаю", first.session_id)
    assert r.stage == "compare" and ORIGIN_NOTE in r.plan.spec.assumptions


def test_model_cannot_turn_the_origin_answer_into_a_destination(monkeypatch, store):
    """The model reads «Питер» as a place to go: the pending question makes it the origin."""
    from api import llm

    def stub(message, spec, until=None, today=None):
        return {"destinations": ["Санкт-Петербург"]} if message == "Питер" else {}

    monkeypatch.setattr(llm, "extract_spec", stub)
    first = say(store, "Сочи на 4 ночи в июле, двое взрослых")
    r = say(store, "Питер", first.session_id)
    assert r.plan.spec.origin == "Санкт-Петербург" and r.plan.spec.destinations == ["Сочи"]


def test_restart_keeps_the_named_origin(store):
    first = say(store, "Сочи на 4 ночи в июле, двое взрослых")
    r = say(store, "из Казани", first.session_id)
    r = say(store, "Начнём сначала: Анталия, 4 ночи, двое взрослых", r.session_id)
    assert r.stage == "compare" and r.plan.spec.origin == "Казань"
    assert not origin_q(r)


def test_city_off_the_catalogue_answers_by_the_geocoder(geo, monkeypatch, store):
    """TRAP-3, TRAP-6: «Орск» is no hub; the geocoder finds it, the nearest hub is said aloud."""
    from api import catalog

    monkeypatch.setattr(catalog, "_get", geo("Орск", country="Россия"))
    first = say(store, "Сочи на 4 ночи в июле, двое взрослых")
    r = say(store, "Орск", first.session_id)
    assert r.stage == "compare" and r.plan.spec.destinations == ["Сочи"]
    assert any(a.startswith("откуда") and "Орск" in a for a in r.plan.spec.assumptions)


def test_city_off_the_catalogue_offline_is_no_destination(store):
    """TRAP-6: no network, «Орск» is not found: the trip stays Сочи, Москва is said aloud."""
    first = say(store, "Сочи на 4 ночи в июле, двое взрослых")
    r = say(store, "Орск", first.session_id)
    assert r.plan.spec.destinations == ["Сочи"] and ORIGIN_NOTE in r.plan.spec.assumptions


# ------------------------------------------------------------- #442: unexpected answers


@pytest.mark.parametrize("answer", ["Турция", "Анталья", "Анталия"])
def test_place_abroad_answers_where_to_go_not_where_from(store, answer):
    """#442: «Турция» or «Анталья» to «Откуда?» named the origin; the nearest hub (Сочи) became
    the departure city and the trip lost its place. A place abroad is where the user goes."""
    from api import catalog

    r = say(store, "Хочу отдохнуть")
    after = say(store, answer, r.session_id)
    s = after.plan.spec
    assert s.origin == "Москва" and ORIGIN_NOTE in s.assumptions
    assert s.destinations and catalog.resolve(s.destinations[0])["country"] == "Турция"


def test_country_off_the_snapshot_from_the_geocoder_is_no_origin(geo, monkeypatch, store):
    """#442, TRAP-6: the geocoder finds «Бодрум» in Турция. The trip has no place yet: a place
    abroad is where to go, not the origin. With a place it is the origin (the Минск test)."""
    from api import catalog

    monkeypatch.setattr(catalog, "_get", geo("Бодрум", country="Турция"))
    first = say(store, "Хочу отдохнуть")
    r = say(store, "Бодрум", first.session_id)
    assert r.plan.spec.origin == "Москва" and ORIGIN_NOTE in r.plan.spec.assumptions


KAZAN_TRIP = "Казань на 3 ночи в декабре, двое, бюджет 50 тысяч"


def test_origin_equal_to_the_place_is_asked_again(store):
    """#442: «Казань на 3 ночи» + «Казань» set origin = destination and went to compare."""
    first = say(store, KAZAN_TRIP)
    r = say(store, "Казань", first.session_id)
    assert r.stage == "clarify" and r.plan.spec.origin is None
    q = origin_q(r)
    assert q and "Казань" in q.text and "место поездки" in q.text
    assert q.options[0] == "Из Казани" and "Из Москвы" in q.options
    assert r.questions[0].text == q.text  # the origin goes first again


@pytest.mark.parametrize("answer", ["Казань", "Из Казани"])
def test_origin_equal_to_the_place_confirmed_goes_on(store, answer):
    """The second answer is the user's choice: the trip goes on without a road."""
    first = say(store, KAZAN_TRIP)
    asked = say(store, "Казань", first.session_id)
    r = say(store, answer, asked.session_id)
    assert r.stage == "compare" and r.plan.spec.origin == "Казань"
    assert r.plan.spec.destinations == ["Казань"]


def test_origin_equal_to_the_place_other_city_goes_on(store):
    first = say(store, KAZAN_TRIP)
    asked = say(store, "Казань", first.session_id)
    r = say(store, "Питер", asked.session_id)
    assert r.stage == "compare" and r.plan.spec.origin == "Санкт-Петербург"
    assert r.plan.spec.destinations == ["Казань"]


def test_far_origin_chips_keep_the_five_in_a_row(monkeypatch):
    """#442: the cheapest big hub went second («Из Омска»): the five chips of #436 broke."""
    from api import model
    from api.contracts import TripSpec

    sums = {"Москва": 60_000, "Омск": 10_000}
    monkeypatch.setattr(model, "_round_trip", lambda probe, origin: sums.get(origin))
    spec = TripSpec(destinations=["Сочи"], month=7, adults=2, budget_rub=100_000)
    (q,) = model.far_origin_question(spec)
    assert q.options == [*CHIPS, "Из Омска"]


SOCHI_TRIP = "Сочи на 4 ночи в июле, двое взрослых"


@pytest.mark.parametrize("answer", ["Минск", "Алматы"])
def test_place_abroad_with_a_trip_place_is_the_origin(store, answer):
    """#442 review: the trip has its place; a city abroad is where the user flies from (the
    nearest hub is said aloud, as before), not a new destination."""
    first = say(store, SOCHI_TRIP)
    r = say(store, answer, first.session_id)
    s = r.plan.spec
    assert r.stage == "compare" and s.destinations == ["Сочи"]
    assert any(a.startswith("откуда") and answer in a for a in s.assumptions)


def test_country_with_a_trip_place_keeps_the_place(store):
    """#442 review: «Турция» to a Сочи trip silently replaced Сочи."""
    first = say(store, SOCHI_TRIP)
    r = say(store, "Турция", first.session_id)
    assert r.plan.spec.destinations == ["Сочи"]
    assert r.plan.spec.origin == "Москва" and ORIGIN_NOTE in r.plan.spec.assumptions


@pytest.mark.parametrize("first_text", [SOCHI_TRIP, "Хочу отдохнуть"])
def test_home_country_is_no_destination(store, first_text):
    """#442 review: «Россия» became a destination «нет в каталоге»: the departure country names
    neither the place nor the city of departure."""
    first = say(store, first_text)
    r = say(store, "Россия", first.session_id)
    assert "Россия" not in r.plan.spec.destinations
    assert r.stage in ("compare", "inspire")
    if first_text == SOCHI_TRIP:
        assert r.plan.spec.destinations == ["Сочи"]


def test_origin_equal_to_the_place_with_more_fields_is_asked_again(store):
    """#442 review: «Казань, двое взрослых» to a Kazan trip skipped the second question; the
    other fields of the answer stay in the trip."""
    first = say(store, "Казань на 3 ночи в декабре, бюджет 50 тысяч, с ребёнком 5 лет")
    r = say(store, "Казань, трое взрослых", first.session_id)
    assert r.stage == "clarify" and r.plan.spec.origin is None
    q = origin_q(r)
    assert q and SAME_PLACE in q.text
    assert r.plan.spec.adults == 3
    after = say(store, "Из Казани", r.session_id)
    assert after.plan.spec.origin == "Казань" and after.plan.spec.adults == 3


ANTALYA_TRIP = "Анталья на 5 ночей в июле, двое"


@pytest.mark.parametrize("answer", ["Анталья", "из Антальи"])
def test_place_abroad_as_the_trip_place_asks_the_origin_again(store, answer):
    """#442 review: no «уже там» for a place abroad: the plan always flies from a hub in Россия,
    so «дорога не нужна» would be a promise the plan does not keep. The origin is asked again."""
    first = say(store, ANTALYA_TRIP)
    r = say(store, answer, first.session_id)
    q = origin_q(r)
    assert r.stage == "clarify" and r.plan.spec.origin is None
    assert q and SAME_PLACE in q.text and "дорога не нужна" not in q.text
    assert q.options == CHIPS


@pytest.mark.parametrize("again", ["Анталья", "из Антальи"])
def test_place_abroad_named_twice_is_no_origin(store, again):
    """The second «Анталья» is no departure city either (it gave «из Сочи»): Москва is said."""
    first = say(store, ANTALYA_TRIP)
    asked = say(store, "Анталья", first.session_id)
    r = say(store, again, asked.session_id)
    s = r.plan.spec
    assert r.stage == "compare" and s.destinations == ["Анталия"]
    assert s.origin == "Москва" and ORIGIN_NOTE in s.assumptions


def test_place_abroad_reasked_then_a_city_goes_on(store):
    first = say(store, ANTALYA_TRIP)
    asked = say(store, "Анталья", first.session_id)
    r = say(store, origin_q(asked).options[1], asked.session_id)
    assert r.stage == "compare" and r.plan.spec.origin == "Санкт-Петербург"
    assert r.plan.spec.destinations == ["Анталия"]


def test_already_there_button_needs_no_road(store):
    """The «уже там» button of a Russian place: origin = the place, and no road in the plan."""
    first = say(store, KAZAN_TRIP)
    asked = say(store, "Казань", first.session_id)
    q = origin_q(asked)
    assert "дорога не нужна" in q.text and q.options[0] == "Из Казани"
    r = say(store, q.options[0], asked.session_id)
    s = r.plan.spec
    assert r.stage == "compare" and s.origin == "Казань" and s.destinations == ["Казань"]
    assert not any(a.startswith("откуда") for a in s.assumptions)
    assert not [o for o in r.plan.offers if o.kind == "transport"]


def test_origin_compared_with_the_new_destination(store):
    """«Казань, едем в Сочи» to a Kazan trip: the new place is Сочи, so Казань is the origin."""
    first = say(store, KAZAN_TRIP)
    r = say(store, "Казань, едем в Сочи", first.session_id)
    assert not any(SAME_PLACE in q.text for q in r.questions)
    assert r.plan.spec.origin == "Казань" and r.plan.spec.destinations == ["Сочи"]
