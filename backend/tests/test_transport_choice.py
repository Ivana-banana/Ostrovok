"""S6 (#31): the user switches transport by a phrase; the plan, the timeline and the cart follow.

The command only picks offer ids that are already in the plan (TRAP-4); it works without an
LLM key (TRAP-1): `chat` runs on rules when no model is configured.
"""

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore

SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def legs(r):
    by_id = {o.offer_id: o for o in r.plan.offers}
    return [by_id[i] for i in r.plan.selected if by_id[i].kind == "transport"]


def transport_rows(r):
    return [o for o in r.plan.offers if o.kind == "transport"]


def test_train_phrase_switches_both_legs_and_rebuilds_the_timeline(store):
    r = say(store, SOCHI)
    assert [x.mode for x in legs(r)] == ["flight", "train"]
    before = r.plan.selected
    r = say(store, "возьми поезд", r.session_id)
    assert r.stage == "compare"  # the user has not accepted the set yet
    assert [x.mode for x in legs(r)] == ["train", "train"]
    assert r.plan.selected != before and r.plan.spec.modes == []  # a pick, not a constraint
    assert r.plan.timeline and "Поезд" in r.reply
    ids = {o.offer_id for o in r.plan.offers}
    assert set(r.plan.selected) <= ids  # TRAP-4: only ids of the plan
    assert any("вокзал" in e.title for e in r.plan.timeline)


def test_transfers_follow_the_new_legs(store):
    r = say(store, SOCHI)
    r = say(store, "возьми поезд", r.session_id)
    by_id = {o.offer_id: o for o in r.plan.offers}
    transfers = [by_id[i].title for i in r.plan.selected if by_id[i].kind == "transfer"]
    assert len(transfers) == 2 and all("вокзал" in t for t in transfers)


def test_ordinal_picks_the_row_of_the_transport_table(store):
    r = say(store, SOCHI)
    rows = transport_rows(r)
    r = say(store, "второй рейс", r.session_id)
    assert rows[1].offer_id in r.plan.selected
    assert r.stage == "compare" and r.plan.spec.modes == []
    r = say(store, "возьми рейс 3", r.session_id)
    # row 3 is the other leg there; row 5 is a leg back and keeps the pick there
    assert rows[2].offer_id in r.plan.selected and rows[1].offer_id not in r.plan.selected
    r = say(store, "рейс 5", r.session_id)
    assert {rows[2].offer_id, rows[4].offer_id} <= set(r.plan.selected)


def test_direction_words_switch_one_leg_only(store):
    r = say(store, SOCHI)
    r = say(store, "поезд туда, самолёт обратно", r.session_id)
    assert [x.mode for x in legs(r)] == ["train", "flight"]


def test_switch_in_assemble_keeps_the_stage_and_rebuilds_the_timeline(store):
    r = say(store, SOCHI)
    r = say(store, "беру", r.session_id)
    assert r.stage == "assemble"
    r = say(store, "возьми поезд", r.session_id)
    assert r.stage == "assemble" and [x.mode for x in legs(r)] == ["train", "train"]
    assert any("вокзал" in e.title for e in r.plan.timeline)
    r = say(store, "да", r.session_id)
    assert r.stage == "checkout"
    assert {o.offer_id for o in r.cart.items} >= {x.offer_id for x in legs(r)}


def test_unknown_row_or_mode_changes_nothing(store):
    r = say(store, SOCHI)
    for text in ("рейс 9", "возьми автомобиль"):
        again = say(store, text, r.session_id)
        assert again.plan.selected == r.plan.selected and again.stage == "compare"
        assert again.reply != r.reply


def test_negation_is_not_a_pick(store):
    r = say(store, SOCHI)
    again = say(store, "не хочу поезд", r.session_id)
    assert again.plan.selected == r.plan.selected and again.plan.spec.modes == r.plan.spec.modes


def test_checkout_refuses_a_switch(store):
    r = say(store, SOCHI)
    r = say(store, "да", say(store, "беру", r.session_id).session_id)
    assert r.stage == "checkout"
    again = say(store, "возьми поезд", r.session_id)
    assert again.stage == "checkout" and again.plan.selected == r.plan.selected
    assert again.cart == r.cart
