"""#113: a place after a negation is no destination and is not offered (TRAP-3: any case form).

«В Турции и Египте уже были, не хочется» used to book Анталия, «Турцию и Египет муж
отвергает» used to book Хургада. Without a model (TRAP-1). The refusal lives in the
assumption «исключено: …» (no TripSpec field), so it holds for the next turns too.
"""

import pytest
from talk import ask, say

from api.extract import excluded_places, extract, is_excluded
from api.inspire import suggest_destinations
from api.store import SessionStore

REPRO_1 = "Едем с ребёнком в ноябре, хотим к морю. В Турции и Египте уже были, не хочется."
REPRO_2 = (
    "Нужно море в ноябре, на троих. Турцию и Египет муж категорически отвергает. Хочется моря."
)


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def names(reply):
    return [d.name for d in reply.plan.destinations]


# (text, excluded): the named places are refused and none of them is a destination.
REFUSED = [
    (REPRO_1, ["Турция", "Египет"]),
    (REPRO_2, ["Турция", "Египет"]),
    ("Хочу куда-нибудь, кроме Турции", ["Турция"]),
    ("Хочу на море, кроме Турции и Египта", ["Турция", "Египет"]),
    ("Хочу на море, только не Египет", ["Египет"]),
    ("Хочу на море, не в Турцию", ["Турция"]),
    ("Хочу на море, Турция надоела", ["Турция"]),
    ("Хочу на море, не хочу в Сочи", ["Сочи"]),
    ("Хочу на море, не надо Сочи", ["Сочи"]),
    ("Хочу на море, без Турции", ["Турция"]),
    ("Хочу на море, Египет не вариант", ["Египет"]),
    ("Хочу на море, мы устали от Турции", ["Турция"]),
    ("Хочу на море, ни в Турцию, ни в Египет", ["Турция", "Египет"]),
    ("В Египте мы уже ездили, не хочется", ["Египет"]),
    ("Хочу на море, в Египте надоело, уже были", ["Египет"]),
    ("Кроме Сочи ничего не надо", ["Сочи"]),
    ("Мы уже были в Турции и в Египте, хотим куда-то новое", ["Турция", "Египет"]),
    ("Мы уже были в Турции, в Египте, хотим куда-то новое", ["Турция", "Египет"]),
    ("Хотим на море в ноябре вдвоём, кроме Турции, ещё хотим бассейн", ["Турция"]),
    ("Хотим на море, кроме Турции, тоже недорого", ["Турция"]),
    ("Кроме Турции, Египет тоже не хочу", ["Турция", "Египет"]),
    ("Хочу на море, исключите Анталию", ["Анталия"]),
]


@pytest.mark.parametrize(("text", "excluded"), REFUSED)
def test_trap_3_negated_place_is_no_destination(text, excluded):
    found, _ = extract(text)
    assert "destinations" not in found
    assert excluded_places(text) == excluded


# (text, destinations, excluded): the other half of the sentence still counts.
KEPT = [
    ("Хочу в Турцию", ["Турция"], []),
    ("Турция или Египет", ["Анталия", "Хургада"], []),  # both names (#142)
    ("Хочу в Египет, мы уже были в Турции", ["Хургада"], ["Турция"]),
    ("В Турции уже были, хочу в Египет", ["Хургада"], ["Турция"]),
    ("Не хочу в Сочи, хочу в Турцию", ["Турция"], ["Сочи"]),
    ("Хочу в Турцию, но не в Сочи", ["Турция"], ["Сочи"]),
    ("Не дорого, в Турцию", ["Турция"], []),
    ("Недорого, в Турцию", ["Турция"], []),
    ("Хочу в Египет с детьми, не хочется долго лететь", ["Хургада"], []),
    # «уже были» alone is no refusal: they liked it and want it again.
    ("Уже были в Сочи, понравилось, хотим ещё раз в ноябре вдвоём", ["Сочи"], []),
    ("Мы уже были в Турции и хотим туда снова, ноябрь, вдвоём", ["Турция"], []),
    ("В Хургаде мы уже ездили", ["Хургада"], []),
    # «кроме X ещё Y» adds Y to X.
    ("Кроме Сочи хотим ещё в Казань", ["Сочи", "Казань"], []),
    ("Кроме Стамбула хотим заехать в Анталию", ["Стамбул", "Анталия"], []),
    # The country is named, the city is refused: the agent takes another city of the country.
    ("Хочу в Египет, но не в Хургаду", ["Шарм-эль-Шейх"], ["Хургада"]),
]


@pytest.mark.parametrize(("text", "dests", "excluded"), KEPT)
def test_trap_3_positive_place_is_kept(text, dests, excluded):
    assert extract(text)[0]["destinations"] == dests
    assert excluded_places(text) == excluded


def test_swapped_city_is_told_in_the_notes():
    _, notes = extract("Хочу в Египет, но не в Хургаду")
    assert any("Хургад" in n and "Шарм-эль-Шейх" in n for n in notes)


SEA_NOV = {"origin": "Москва", "month": 11, "wishes": ["море"]}


@pytest.mark.parametrize(
    ("spec", "exclude"),
    [
        (SEA_NOV, ["Турция"]),
        (SEA_NOV, ["Египет"]),
        (SEA_NOV, ["Анталия"]),
        ({}, ["Россия"]),
        ({}, ["Нижний Новгород"]),
    ],
)
def test_suggestions_skip_excluded_places_and_their_country(spec, exclude):
    from api.contracts import TripSpec

    spec = TripSpec(**spec)
    every = suggest_destinations(spec, 10)
    gone = [d.name for d in every if d.name in exclude or d.country in exclude]
    shown = suggest_destinations(spec, 10, exclude)
    assert gone and shown  # without the refusal the place is offered
    assert not any(d.name in exclude or d.country in exclude for d in shown)


@pytest.mark.parametrize("text", [REPRO_1, REPRO_2])
def test_refused_places_are_not_booked_or_offered(store, text):
    r = say(store, text)
    assert r.stage == "inspire" and r.plan.spec.destinations == []
    assert names(r) and not {"Анталия", "Хургада"} & set(names(r))
    assert "исключено: Турция, Египет" in r.plan.spec.assumptions
    # Every next turn keeps the refusal: «1» picks from the shown list.
    cheaper = say(store, "подешевле, до 100 тысяч", r.session_id)
    assert not {"Анталия", "Хургада"} & set(names(cheaper))
    picked = say(store, "1", r.session_id)
    assert picked.stage == "compare"
    assert picked.plan.spec.destinations[0] not in ("Анталия", "Хургада")


def test_clarify_first_keeps_the_refusal(store):
    r = ask(store, "Едем в ноябре. В Турции и Египте уже были, не хочется.")
    assert r.stage == "clarify"
    r = ask(store, "Из Москвы, 7 ночей", r.session_id)
    assert r.stage == "clarify"  # #630: the party and the budget
    r = say(store, "Двое взрослых, до 100 000 ₽", r.session_id)
    assert r.stage == "inspire" and not {"Анталия", "Хургада"} & set(names(r))


def test_named_city_after_a_refusal_is_booked(store):
    r = say(store, REPRO_2)
    r = say(store, "ладно, поедем в Хургаду", r.session_id)
    assert r.stage == "compare" and r.plan.spec.destinations == ["Хургада"]
    assert "исключено: Турция" in r.plan.spec.assumptions  # only the named country is free again


def test_refusal_of_the_chosen_country_drops_the_plan_city(store):
    r = say(store, "Анталия на неделю в ноябре, двое взрослых")
    assert r.stage == "compare" and r.plan.spec.destinations == ["Анталия"]
    r = say(store, "Турция надоела", r.session_id)
    assert r.stage == "inspire" and r.plan.spec.destinations == []
    assert "Анталия" not in names(r)


def test_model_destination_in_a_refused_country_is_dropped():
    from api.extract import with_llm

    out = with_llm({}, {"destinations": ["Турция"], "month": 11}, ["Турция"])
    assert "destinations" not in out and out["month"] == 11
    assert with_llm({}, {"destinations": ["Сочи"]}, ["Турция"])["destinations"] == ["Сочи"]


# The jury table of #113, through the chat: (text, refused, kept). Refused: in «исключено: …», not
# in destinations, not in inspire. Kept: in destinations, not in «исключено».
JURY_TABLE = [
    (REPRO_1, ["Турция", "Египет"], []),
    (REPRO_2, ["Турция", "Египет"], []),
    ("Мы уже были в Турции и в Египте, хотим куда-то новое", ["Турция", "Египет"], []),
    ("Хочу на море, кроме Турции, ещё хотим бассейн", ["Турция"], []),
    ("Хочу на море, кроме Турции и Египта", ["Турция", "Египет"], []),
    ("Хочу на море, ни в Турцию, ни в Египет", ["Турция", "Египет"], []),
    ("Хочу на море, Турция надоела", ["Турция"], []),
    ("Уже были в Сочи, понравилось, хотим ещё раз", [], ["Сочи"]),
    ("Кроме Сочи хотим ещё в Казань", [], ["Сочи", "Казань"]),
    ("Хотим в Сочи, не дорого", [], ["Сочи"]),
    ("Тёплое море в ноябре, недорого, с ребёнком", [], []),
]


@pytest.mark.parametrize(("text", "refused", "kept"), JURY_TABLE)
def test_trap_3_jury_table(store, text, refused, kept):
    r = say(store, text)
    marks = [a for a in r.plan.spec.assumptions if a.startswith("исключено")]
    assert marks == ([f"исключено: {', '.join(refused)}"] if refused else [])
    assert not set(refused) & {d for d in r.plan.spec.destinations}
    assert not any(is_excluded(n, refused) for n in names(r) + r.plan.spec.destinations)
    assert r.plan.spec.destinations == kept or not kept
    assert not set(kept) & set(refused)


# #127: the tails of #113.
def test_trap_3_refusal_by_verb_ellipsis():
    """«Турцию не хочу, Египет тоже»: «тоже» without a verb goes on the refusal."""
    text = "Турцию не хочу, Египет тоже"
    assert "destinations" not in extract(text)[0]
    assert excluded_places(text) == ["Турция", "Египет"]
    assert excluded_places("Не хочу в Турцию, а в Египет тоже") == ["Турция", "Египет"]
    # With a wish verb or without a refusal before, «тоже» adds a wish.
    assert extract("Хочу в Турцию, Египет тоже")[0]["destinations"] == ["Турция", "Хургада"]
    assert excluded_places("Хочу в Турцию, Египет тоже") == []
    assert extract("Турцию не хочу, Египет тоже хочу")[0]["destinations"] == ["Хургада"]
    assert excluded_places("Турцию не хочу, Египет тоже хочу") == ["Турция"]


def test_trap_3_except_then_also_refused():
    text = "Кроме Турции, Египет тоже не хочу"
    assert "destinations" not in extract(text)[0]
    assert excluded_places(text) == ["Турция", "Египет"]


def test_trap_3_marker_stands_for_its_own_mention():
    # The wish «опять в Сочи» is no wish for Анапа; Сочи is wanted again.
    text = "Уже были в Сочи и в Анапе, хотим опять в Сочи"
    assert extract(text)[0]["destinations"] == ["Сочи"]
    assert excluded_places(text) == ["Анапа"]
    # «в Анапу не хотим» is no dislike of Сочи.
    text = "Уже были в Сочи, в Анапу не хотим"
    assert extract(text)[0]["destinations"] == ["Сочи"]
    assert excluded_places(text) == ["Анапа"]


def test_trap_3_country_in_a_case_is_the_country():
    """«Египте» is the country Египет, not a new city named «Египте»."""
    from api.extract import _capital

    assert _capital("Египте", 0) == "Египет"
    assert _capital("Таиланде", 0) == "Таиланд"
    assert _capital("Грузию", 0) == "Грузия"
    for text in ("Хочу в Египте", "Хочу отдохнуть в Египте"):
        assert extract(text)[0]["destinations"] == ["Египет"]
    assert extract("Хочу в Таиланде")[0]["destinations"] == ["Таиланд"]


# #172: the tails of #127.
def test_trap_3_not_x_but_y_too_keeps_y_wanted():
    """«Не Турция, а Египет тоже»: «а» turns to Египет, «тоже» is no refusal."""
    text = "Не Турция, а Египет тоже"
    assert excluded_places(text) == ["Турция"]
    assert extract(text)[0]["destinations"] == ["Хургада"]
    # With a verb the refusal goes on («тоже» stands for the verb).
    assert excluded_places("Не хочу в Турцию, а в Египет тоже") == ["Турция", "Египет"]


def test_trap_3_did_not_like_is_a_refusal():
    """«Сочи не понравился, Анапа тоже»: both are refused (the pinned reading of «тоже»)."""
    text = "Сочи не понравился, Анапа тоже"
    assert excluded_places(text) == ["Сочи", "Анапа"]
    assert "destinations" not in extract(text)[0]
    assert excluded_places("Сочи понравился, Анапа тоже") == []


def test_trap_3_origin_is_nominative():
    """`_capital` brings the origin to the nominative: «из Беларуси» -> «Беларусь»."""
    assert extract("Хотим на море из Беларуси")[0]["origin"] == "Беларусь"


@pytest.mark.parametrize(
    ("case", "name"),
    [("Кубе", "Куба"), ("Мексике", "Мексика"), ("Омане", "Оман"), ("Катаре", "Катар"),
     ("Тунисе", "Тунис")],
)  # fmt: skip
def test_trap_3_country_case_is_nominative(case, name):
    assert extract(f"Хочу на море в {case}")[0]["destinations"] == [name]
    assert excluded_places(f"Не хочу в {case}") == [name]
    assert extract(f"Хочу из {case} в Сочи")[0]["origin"] == name
