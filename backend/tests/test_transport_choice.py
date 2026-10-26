"""S6 (#31): the user switches transport by a phrase; the plan, the timeline and the cart follow.

The command only picks offer ids that are already in the plan (TRAP-4); it works without an
LLM key (TRAP-1): `chat` runs on rules when no model is configured.
"""

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.offers import transfer_id
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
    r = say(store, "возьми самолёт", r.session_id)  # the default pick does not matter
    assert [x.mode for x in legs(r)] == ["flight", "flight"]
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


def rub(n):
    return f"{n:,} ₽".replace(",", " ")


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("давай в Казань поездом", "destinations"),
        ("давай на неделю позже, полетим самолётом", "date_from"),
        ("лучше с завтраком и поездом", "meal"),
    ],
)
def test_mixed_phrase_is_a_replan_not_a_pick(store, text, field):
    r = say(store, SOCHI)
    again = say(store, text, r.session_id)
    assert getattr(again.plan.spec, field) != getattr(r.plan.spec, field)
    assert "уже выбран" not in again.reply and "Транспорт заменён" not in again.reply


def test_mode_filtered_out_by_the_spec_sets_the_constraint(store):
    r = say(store, "Казань самолётом, 3 ночи с 20 ноября, двое взрослых")
    assert r.plan.spec.modes == ["flight"]
    assert {x.mode for x in transport_rows(r)} == {"flight"}
    again = say(store, "давай поездом", r.session_id)
    assert again.plan.spec.modes == ["train"] and "нет" not in again.reply.split("\n")[0]
    assert [x.mode for x in legs(again)] == ["train", "train"]
    third = say(store, "возьми самолёт", again.session_id)
    assert [x.mode for x in legs(third)] == ["flight", "flight"]


def test_ordinal_counts_among_offers_of_the_named_kind(store):
    r = say(store, SOCHI)
    r = say(store, "возьми самолёт", r.session_id)
    trains = [x for x in transport_rows(r) if x.mode == "train"]
    r = say(store, "второй поезд", r.session_id)
    assert trains[1].offer_id in r.plan.selected  # the train back, not row 2 of the table
    assert [x.mode for x in legs(r)] == ["flight", "train"]
    again = say(store, "третий поезд", r.session_id)
    assert again.plan.selected == r.plan.selected and "нет" in again.reply.split("\n")[0]


@pytest.mark.parametrize("text", ["рейс 0", "перелёт 2 часа", "рейс 20 ноября"])
def test_not_a_row(store, text):
    r = say(store, SOCHI)
    again = say(store, text, r.session_id)
    assert again.plan.selected == r.plan.selected
    assert "Транспорт заменён" not in again.reply


def test_why_after_a_switch_matches_the_new_total(store):
    r = say(store, SOCHI)
    r = say(store, "возьми самолёт", r.session_id)
    other = say(store, "возьми поезд", r.session_id)
    total = sum(o.price_rub for o in other.plan.offers if o.offer_id in other.plan.selected)
    assert total != sum(o.price_rub for o in r.plan.offers if o.offer_id in r.plan.selected)
    assert f"Итого {rub(total)}" in other.reply
    assert f"поездка целиком {rub(total)}" in other.reply  # the old totals are gone
    first = other.plan.explanations[0]
    assert first.offer_id in other.plan.selected
    assert {e.offer_id for e in other.plan.explanations[1:]} == {x.offer_id for x in legs(other)}


KAZAN = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё."


def test_initial_plan_at_compare_has_a_timeline(store):
    """#66: the jury saw an empty timeline until the first transport switch."""
    r = say(store, SOCHI)
    assert r.stage == "compare" and r.plan.timeline
    assert [e.at for e in r.plan.timeline] == sorted(e.at for e in r.plan.timeline)
    r = say(store, "беру", r.session_id)
    assert r.stage == "assemble" and r.plan.timeline


def test_bare_train_phrase_on_the_default_pick_keeps_the_plan_and_the_note(store):
    """#66: the default pick is the night train. «возьми поезд» changes nothing; «беру» keeps 07:10."""
    r = say(store, KAZAN)
    assert r.stage == "compare" and [x.mode for x in legs(r)] == ["train", "train"]
    first = r
    r = say(store, "возьми поезд", r.session_id)
    assert r.stage == "compare" and "уже выбран" in r.reply
    assert r.plan.selected == first.plan.selected and r.plan.timeline == first.plan.timeline
    r = say(store, "беру", r.session_id)
    assert r.stage == "assemble"
    assert any("07:10" in i.text and "14:00" in i.text for i in r.plan.issues)


def hotel_ids(r):
    return [
        o.offer_id for o in r.plan.offers if o.kind == "hotel" and o.offer_id in r.plan.selected
    ]


def test_second_hotel_and_train_changes_both(store):
    """#58: «беру второй отель и поезд» keeps the hotel pick; the 2nd row is a hotel, not a tariff."""
    from api.optimizer import hotel_rows

    r = say(store, SOCHI)
    r = say(store, "возьми самолёт", r.session_id)
    rows = hotel_rows(r.plan.offers, r.plan.selected)
    assert hotel_ids(r) != [rows[1].offer_id]
    r = say(store, "беру второй отель и поезд", r.session_id)
    assert r.stage == "compare"  # the user has not accepted the set yet
    assert hotel_ids(r) == [rows[1].offer_id]
    assert [x.mode for x in legs(r)] == ["train", "train"]
    assert "Отель заменён" in r.reply and "Транспорт заменён" in r.reply
    by_id = {o.offer_id: o for o in r.plan.offers}
    mine = {transfer_id(rows[1].offer_id, x.offer_id) for x in legs(r)}
    assert mine <= set(r.plan.selected)  # the transfers of the new hotel, not of the old one
    assert {i for i in r.plan.selected if by_id[i].kind == "transfer"} == mine
    assert r.plan.explanations[0].offer_id == rows[1].offer_id  # «Почему» names the new hotel
    assert any(set(b.offer_ids) == set(r.plan.selected) for b in r.plan.bundles)


def test_hotel_and_train_in_the_other_order_and_with_a_digit(store):
    from api.optimizer import hotel_rows

    r = say(store, SOCHI)
    rows = hotel_rows(r.plan.offers, r.plan.selected)
    r = say(store, "возьми поезд и отель 3", r.session_id)
    assert hotel_ids(r) == [rows[2].offer_id]
    assert [x.mode for x in legs(r)] == ["train", "train"]


def test_hotel_pick_with_the_transport_already_chosen_still_changes_the_hotel(store):
    from api.optimizer import hotel_rows

    r = say(store, SOCHI)
    r = say(store, "возьми поезд", r.session_id)
    rows = hotel_rows(r.plan.offers, r.plan.selected)
    sel = [i for i in r.plan.selected if i not in hotel_ids(r)]
    r = say(store, "беру второй отель и поезд", r.session_id)
    assert hotel_ids(r) == [rows[1].offer_id]
    assert "Отель заменён" in r.reply and "уже выбран" not in r.reply
    assert [x.mode for x in legs(r)] == ["train", "train"] and sel


def test_unknown_hotel_row_changes_nothing(store):
    r = say(store, SOCHI)
    again = say(store, "беру отель 9 и поезд", r.session_id)
    assert again.plan.selected == r.plan.selected and "Отеля 9 нет" in again.reply


def test_why_and_bundles_follow_a_plain_switch(store):
    """#58: a «ваш выбор» bundle is the selected trip; «Почему» names the same legs."""
    r = say(store, SOCHI)
    r = say(store, "возьми самолёт", r.session_id)
    r = say(store, "возьми поезд", r.session_id)
    assert r.plan.bundles[0].label.startswith(("лучший", "ближе"))  # the ranking stays first
    top = next(b for b in r.plan.bundles if b.label.startswith("ваш выбор"))
    assert set(top.offer_ids) == set(r.plan.selected)
    legs_why = {e.offer_id for e in r.plan.explanations[1:]}
    assert legs_why == {x.offer_id for x in legs(r)} <= set(top.offer_ids)


def test_plain_hotel_pick_updates_the_why_and_keeps_the_ranked_bundle_first(store):
    """#58: «второй» (compare -> assemble): «Почему» names the picked hotel.

    bundles[0] stays the optimizer's best trip (dag._keep_pick of #37 reads it to find a manual
    pick); the picked trip is a «ваш выбор» bundle after it.
    """
    from api.optimizer import hotel_rows

    r = say(store, SOCHI)
    rows = hotel_rows(r.plan.offers, r.plan.selected)
    best = r.plan.bundles[0]
    r = say(store, "второй", r.session_id)
    assert r.stage == "assemble" and hotel_ids(r) == [rows[1].offer_id]
    assert r.plan.explanations[0].offer_id == rows[1].offer_id
    assert {e.offer_id for e in r.plan.explanations} <= set(r.plan.selected)
    assert r.plan.bundles[0] == best and best.label.startswith(("лучший", "ближе"))
    mine = [b for b in r.plan.bundles if b.label.startswith("ваш выбор")]
    assert len(mine) == 1 and set(mine[0].offer_ids) == set(r.plan.selected)
