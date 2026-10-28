"""#451: «ещё» at inspire gives the next destinations of the same pool, not the same four.

The shown places live in the session state (an assumption of the spec), not in the LLM: the
tests run without a key (TRAP-1). The spec of the trip stays, only the shown cards change.
"""

import pytest
from talk import say

from api import inspire
from api.model import MORE_RE
from api.store import SessionStore

FIELDS = ("origin", "month", "date_from", "date_to", "adults", "children_ages", "budget_rub")


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "s.db")


def names(r) -> list[str]:
    return [d.name for d in r.plan.destinations]


def same_trip(a, b) -> bool:
    return all(getattr(a.plan.spec, f) == getattr(b.plan.spec, f) for f in FIELDS)


@pytest.mark.parametrize(
    "text",
    ["ещё", "еще", "Ещё!", "а ещё?", "другие", "покажи ещё варианты", "другие направления",
     "покажи другие варианты", "есть ещё?"],
)  # fmt: skip
def test_more_gives_new_destinations_without_llm(store, text):
    """TRAP-1: the rules read «ещё»; the reply has no LLM part."""
    first = say(store, "тёплое море в ноябре, двое взрослых")
    assert first.stage == "inspire" and len(names(first)) == 4
    more = say(store, text, first.session_id)
    assert more.stage == "inspire" and not more.llm
    assert names(more) and not set(names(more)) & set(names(first))
    assert same_trip(first, more)  # DAG: the spec is the same, only the selection changes
    assert more.reply.count("\n1. ") == 1  # the new batch is numbered from 1


def test_more_twice_skips_every_shown_batch(store):
    r = say(store, "тёплое море в ноябре, двое взрослых")
    seen = set(names(r))
    for _ in range(2):
        r = say(store, "ещё", r.session_id)
        assert names(r) and not set(names(r)) & seen
        seen |= set(names(r))


def test_number_after_more_picks_from_the_new_batch(store):
    r = say(store, "тёплое море в ноябре, двое взрослых")
    r = say(store, "ещё", r.session_id)
    second = names(r)[1]
    r = say(store, "2", r.session_id)
    assert r.plan.spec.destinations == [second]


def test_more_is_not_a_part_of_another_edit():
    """«и ещё ребёнок 5 лет» adds a child, it is no request for more places."""
    for text in ("и ещё ребёнок 5 лет", "ещё на 2 ночи", "ещё дешевле", "давай ещё раз Сочи"):
        assert not MORE_RE.match(text.lower().replace("ё", "е"))


def test_exhausted_pool_is_said_honestly(store, monkeypatch):
    """No place is left: the reply says so and names a condition to relax; the cards stay."""
    r = say(store, "тёплое море в ноябре, двое взрослых, бюджет 150000")
    first = names(r)
    pool = inspire._pool

    def small(spec, hub, exclude):
        return [p for p in pool(spec, hub, exclude) if p["name"] in first]

    monkeypatch.setattr(inspire, "_pool", small)
    r = say(store, "ещё", r.session_id)
    assert "Других направлений под эти условия нет" in r.reply
    assert "бюджет" in r.reply
    assert names(r) == first  # «номер» still picks from the shown cards


def hotel_cities(r) -> set[str]:
    return {o.city for o in r.plan.offers if o.kind == "hotel"}


def test_458_name_beats_number_after_more(store):
    """#458, TRAP-1: «третий: X» with X from the first batch, after «ещё», picks X (sim4)."""
    r = say(store, "тёплое море в ноябре, недорого, из Москвы, двое взрослых, 7 ночей")
    first = names(r)
    r = say(store, "ещё", r.session_id)
    assert names(r)[2] != first[2]
    r = say(store, f"третий: {first[2]}", r.session_id)
    assert r.plan.spec.destinations == [first[2]] and not r.llm
    assert hotel_cities(r) == {first[2]}


def test_458_number_with_matching_name_and_unknown_name(store):
    r = say(store, "тёплое море в ноябре, двое взрослых")
    cur = names(r)
    ask = say(store, "второй: Сочи", r.session_id)
    assert ask.stage == "inspire" and not ask.plan.spec.destinations and ask.questions
    assert [q.options for q in ask.questions] == [cur]
    ok = say(store, f"второй: {cur[1]}", r.session_id)
    assert ok.plan.spec.destinations == [cur[1]]


def test_458_bare_name_from_old_batch_and_no_state_in_text(store):
    """#458, TRAP-1: after two «ещё» any shown place is picked by its bare name; the internal
    note «уже показаны» is not in the reply text."""
    r = say(store, "тёплое море в ноябре, двое взрослых")
    first = names(r)
    r = say(store, "ещё", r.session_id)
    r = say(store, "ещё", r.session_id)
    assert "уже показаны" not in r.reply
    r = say(store, first[0], r.session_id)
    assert r.plan.spec.destinations == [first[0]] and r.stage == "compare"
    assert hotel_cities(r) == {first[0]}
    assert "уже показаны" not in r.reply


def test_458_named_in_prefers_the_longest_name():
    """#458: «Венеция» is not «Вена»: at one position the longer name wins."""
    from api.model import named_in

    assert named_in("третий: Венеция", ["Вена", "Венеция"]) == "Венеция"
    assert named_in("третий: Вена", ["Венеция", "Вена"]) == "Вена"
    assert named_in("второй: Кировск", ["Киров", "Кировск"]) == "Кировск"


def test_458_hotel_name_beats_number_at_compare(store):
    """#458: «беру первый — <название второго отеля>» takes the named hotel, no LLM."""
    from api.optimizer import hotel_rows

    r = say(store, "Казань на выходные, из Москвы, двое взрослых")
    while r.stage != "compare":
        r = say(store, "да", r.session_id)
    rows = hotel_rows(r.plan.offers, r.plan.selected)
    assert len(rows) >= 2
    r = say(store, f"беру первый — {rows[1].title}", r.session_id)
    chosen = {o.title for o in r.plan.offers if o.offer_id in r.plan.selected and o.kind == "hotel"}
    assert chosen == {rows[1].title} and not r.llm
