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
