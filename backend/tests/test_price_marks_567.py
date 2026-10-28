"""TRAP-7 (#567): the label «синтетика» stands only at a synthetic price. A real price names its
source; a sum of real and synthetic prices says «часть цен — синтетика»."""

import pytest
from talk import say

from api.answers import price_mark
from api.model import _text_assemble, _text_compare
from api.store import SessionStore

SOCHI = "Сочи из Москвы с 3 по 8 декабря, двое взрослых"


@pytest.fixture
def compare(tmp_path):
    r = say(SessionStore(tmp_path / "sessions.db"), SOCHI)
    assert r.stage == "compare"
    return r


def real(plan, *kinds):
    """The plan with the offers of these kinds as real data (101hotels, Авиасейлс, tutu)."""
    offers = [o.model_copy(update={"synthetic": o.kind not in kinds}) for o in plan.offers]
    return plan.model_copy(update={"offers": offers})


def test_price_mark_TRAP_7(compare):
    hotel = next(o for o in compare.plan.offers if o.kind == "hotel")
    leg = next(o for o in compare.plan.offers if o.kind == "transport")
    live = hotel.model_copy(update={"synthetic": False})
    assert price_mark([]) == "синтетика"  # no data is never shown as real
    assert price_mark([hotel, leg]) == "синтетика"
    assert price_mark([live]) == "101hotels"
    assert price_mark([live, leg]) == "часть цен — синтетика"
    train = leg.model_copy(update={"synthetic": False, "mode": "train"})
    assert price_mark([live, train]) == "101hotels + tutu"


def test_a_real_hotel_is_not_marked_synthetic_in_the_reply_TRAP_7(compare):
    plan = real(compare.plan, "hotel")
    text = _text_compare(plan)
    hotel = next(line for line in text.split("\n") if line.startswith("Отель: "))
    assert hotel.endswith(" ₽ (101hotels)"), hotel
    assert "Цены — синтетика." not in text
    assert "Цены: отели — 101hotels; остальные — синтетика." in text
    assert any(
        line.startswith("Итого ") and "(часть цен — синтетика)" in line for line in text.split("\n")
    )
    assert "(часть цен — синтетика). Оформляем?" in _text_assemble(plan)


def test_all_synthetic_keeps_the_label_TRAP_7(compare):
    text = _text_compare(compare.plan)
    assert "Цены — синтетика." in text
    assert all("(синтетика)" in x for x in text.split("\n") if x.startswith(("Отель: ", "Итого ")))


def test_each_sum_of_the_explanation_has_its_own_mark_TRAP_7(compare):
    from api.optimizer import optimize

    offers = [o.model_copy(update={"synthetic": o.kind != "hotel"}) for o in compare.plan.offers]
    ranked = optimize(compare.plan.spec, offers)
    why = ranked.explanations[0].why
    assert "отель " in why and "(101hotels)" in why, why
    hotel_sum = why.split("отель ", 1)[1].split(" + ")[0]
    assert hotel_sum.endswith("(101hotels)"), why
    assert "(часть цен — синтетика)" in why  # the whole trip: a real hotel and synthetic legs
    for text in [
        why,
        *ranked.explanations[0].tradeoffs,
        *(t for b in ranked.bundles for t in b.tradeoffs),
    ]:
        assert all(seg[:1] == "(" for seg in text.split(" ₽ ")[1:]), (
            text
        )  # every sum has a mark after it
