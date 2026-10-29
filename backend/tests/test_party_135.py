"""#135: the party question for several children, and a booking after it was skipped."""

import pytest
from talk import say

from api.extract import extract
from api.model import PARTY_ASK, PARTY_ASK_KIDS, RECOMMENDED
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def party_asked(r):
    return next((q for q in r.questions if q.text in (PARTY_ASK, PARTY_ASK_KIDS)), None)


@pytest.mark.parametrize(
    "text,ask",
    [
        ("Сочи, 3 ночи с 20 ноября, с ребёнком 5 лет", PARTY_ASK),
        ("Сочи, 3 ночи с 20 ноября, с детьми 5 и 8 лет", PARTY_ASK_KIDS),
    ],
)
def test_the_question_names_one_or_many_children(store, text, ask):
    q = party_asked(say(store, text))
    assert q and q.text == ask
    assert "Двое взрослых" + RECOMMENDED in q.options  # the buttons of #596 stay


def test_skipped_party_question_still_reaches_a_booking(store):
    r = say(store, "Сочи, 3 ночи с 20 ноября, с детьми 5 и 8 лет, отель до 40 тысяч")
    assert party_asked(r) and r.stage == "clarify"
    for text, stage in (("да", "compare"), ("да", "assemble"), ("да", "checkout")):
        r = say(store, text, r.session_id)
        assert r.stage == stage
    assert r.plan.spec.adults == 2  # the skipped question keeps the assumed party
    r = say(store, "подтверждаю", r.session_id)
    assert r.stage == "booked" and r.voucher.total_rub > 0


def test_family_of_four_people_counts_four_adults():
    found, _ = extract("Сочи в ноябре, семья из четырёх человек")
    assert found["adults"] == 4
