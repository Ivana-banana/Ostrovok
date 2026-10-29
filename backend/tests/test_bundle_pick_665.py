"""#665: «вариант N» takes the offers of the N-th whole-trip card; a number out of range changes nothing."""

import pytest
from talk import say

from api.store import SessionStore

SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def test_bundle_pick_selects_its_offers_and_goes_to_assemble(store):
    r = say(store, SOCHI)
    assert r.stage == "compare" and len(r.plan.bundles) >= 2
    want = set(r.plan.bundles[1].offer_ids)
    r = say(store, "вариант 2", r.session_id)
    assert r.stage == "assemble"
    assert want <= set(r.plan.selected)
    assert {o.offer_id for o in r.plan.offers} >= set(r.plan.selected)  # TRAP-4: ids of the plan
    assert r.plan.timeline


def test_bundle_pick_out_of_range_keeps_the_stage(store):
    r = say(store, SOCHI)
    before = r.plan.selected
    r = say(store, "вариант 99", r.session_id)
    assert r.stage == "compare" and r.plan.selected == before
    assert "99" in r.reply
