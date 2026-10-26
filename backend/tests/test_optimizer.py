"""Optimizer (T7, #13): whole-trip bundles, hard filters, weights, "what was sacrificed"."""

import random
import re
from datetime import date, datetime, timedelta
from itertools import combinations
from zoneinfo import ZoneInfo

import pytest

from api import dag
from api.contracts import (
    Cancellation,
    Cart,
    HotelOffer,
    Plan,
    TransferOffer,
    TransportOffer,
    TripSpec,
)
from api.offers import find_offers, transfer_id
from api.optimizer import DEFAULT_WEIGHTS, bundles, optimize, rank, weights

TZ = ZoneInfo("Europe/Moscow")
DAY_IN, DAY_OUT = date(2030, 11, 20), date(2030, 11, 23)


def hotel(key, price, *, meal="RO", refundable=False, rating=8.0, stars=3, km=1.0, **kw):
    return HotelOffer(
        offer_id=f"h-{key}",
        title=f"Отель {key}",
        city="Сочи",
        tz="Europe/Moscow",
        date_in=DAY_IN,
        date_out=DAY_OUT,
        adults=2,
        price_rub=price,
        stars=stars,
        rating=rating,
        meal=meal,
        cancellation=Cancellation(refundable=refundable),
        distance_center_km=km,
        lat=43.6,
        lon=39.7,
        **kw,
    )


def leg(key, price, *, there=True, mode="flight", hours=2.5, **kw):
    day = DAY_IN if there else DAY_OUT
    depart = datetime.combine(day, datetime.min.time(), TZ) + timedelta(hours=8)
    a, b = ("Москва", "Сочи") if there else ("Сочи", "Москва")
    return TransportOffer(
        offer_id=f"t-{key}",
        title=f"{mode} {a} → {b} {key}",
        mode=mode,
        origin=a,
        destination=b,
        depart_at=depart,
        arrive_at=depart + timedelta(hours=hours),
        price_rub=price,
        **kw,
    )


def transfer(h, t, price, minutes=40):
    start = t.arrive_at if t.destination == h.city else t.depart_at - timedelta(hours=2)
    return TransferOffer(
        offer_id=transfer_id(h.offer_id, t.offer_id),
        title=f"Трансфер {h.title}",
        depart_at=start,
        arrive_at=start + timedelta(minutes=minutes),
        price_rub=price,
    )


def offers(hotels, legs, fares=None):
    """Hotels, legs and a transfer per hotel and non-car leg; fares: hotel key -> price."""
    fares = fares or {}
    xs = [
        transfer(h, t, fares.get(h.offer_id[2:], 500))
        for h in hotels
        for t in legs
        if t.mode != "car"
    ]
    return [*hotels, *legs, *xs]


def spec(**kw):
    base = {
        "destinations": ["Сочи"],
        "origin": "Москва",
        "date_from": DAY_IN,
        "date_to": DAY_OUT,
        "nights": 3,
    }
    return TripSpec.model_validate({**base, **kw})


FLIGHTS = [leg("out", 8000), leg("back", 8000, there=False)]


def picked(s, items):
    selected, _ = rank(s, items)
    by_id = {o.offer_id: o for o in items}
    return [by_id[i] for i in selected]


def hotel_of(s, items):
    return next(o for o in picked(s, items) if o.kind == "hotel")


def total(ids, items):
    by_id = {o.offer_id: o for o in items}
    return sum(by_id[i].price_rub for i in ids)


def test_bundle_is_the_whole_trip_and_its_total_is_the_sum():
    s = spec(meal="BB")
    items = find_offers(s, "Сочи")
    out = bundles(s, items)
    assert len(out) >= 2
    by_id = {o.offer_id: o for o in items}
    for b in out:
        kinds = [by_id[i].kind for i in b.offer_ids]
        assert kinds.count("hotel") == 1 and kinds.count("transport") == 2
        assert b.total_rub == total(b.offer_ids, items)  # all guests, all nights
        h = next(by_id[i] for i in b.offer_ids if by_id[i].kind == "hotel")
        legs = [by_id[i] for i in b.offer_ids if by_id[i].kind == "transport"]
        want = {transfer_id(h.offer_id, t.offer_id) for t in legs if t.mode != "car"}
        assert {i for i in b.offer_ids if by_id[i].kind == "transfer"} == want
        assert b.travel_hours > 0
    assert _no_transfers(out[0].offer_ids, items) == rank(s, items)[0]


def _no_transfers(ids, items):
    """The selection is the best bundle without its transfers (they follow the hotel)."""
    kinds = {o.offer_id: o.kind for o in items}
    return [i for i in ids if kinds[i] != "transfer"]


def test_s7_transfer_eats_the_hotel_saving():
    """S7: hotel A is 5 500 ₽ cheaper, its transfers cost 6 000 ₽ more; trip B wins."""
    a, b = hotel("A", 32_000), hotel("B", 37_500)
    items = offers([a, b], FLIGHTS, {"A": 3600, "B": 600})
    s = spec(wishes=["недорого"])
    out = bundles(s, items)
    first = next(i for i, x in enumerate(out) if "h-B" in x.offer_ids)
    second = next(i for i, x in enumerate(out) if "h-A" in x.offer_ids)
    assert first < second
    assert out[first].total_rub == 37_500 + 16_000 + 1_200
    assert out[second].total_rub == 32_000 + 16_000 + 7_200
    assert any("трансфер" in t for t in out[second].tradeoffs)
    assert "съедают экономию 5 500 ₽ на отеле" in " ".join(out[second].tradeoffs)


def test_budget_filter_drops_a_better_trip_over_the_budget():
    good, cheap = hotel("good", 40_000, rating=9.5, stars=5), hotel("cheap", 15_000, rating=7.0)
    items = offers([good, cheap], FLIGHTS)
    assert hotel_of(spec(priorities=["rating"]), items) == good  # without a budget
    s = spec(priorities=["rating"], budget_rub=35_000)
    assert hotel_of(s, items) == cheap
    _, why = rank(s, items)
    assert why[0].risks == []


def test_budget_fallback_names_the_overrun_and_picks_the_cheapest():
    """Nothing fits: the smallest overrun is picked and the risk says by how much."""
    items = offers([hotel("a", 20_000), hotel("b", 25_000, rating=9.5)], FLIGHTS)
    s = spec(budget_rub=30_000)
    r = optimize(s, items)
    assert r.selected[0] == "h-a"
    assert r.bundles[0].total_rub == total(r.bundles[0].offer_ids, items) == 37_000
    over = r.bundles[0].total_rub - 30_000
    risks = " ".join(r.explanations[0].risks)
    assert "не укладывается в бюджет 30 000 ₽" in risks
    assert f"на {over:,} ₽ больше".replace(",", " ") in risks
    assert "самая дешёвая поездка из найденных" in risks
    assert r.bundles[0].label == "ближе всего к условиям"


def test_budget_risk_looks_at_every_hotel_not_only_the_meal_ones():
    """PR #35: the breakfast hotel is over the budget, a hotel without meals fits."""
    bb, ro = hotel("bb", 30_000, meal="BB"), hotel("ro", 10_000, meal="RO")
    items = offers([bb, ro], FLIGHTS)
    r = optimize(spec(meal="BB", budget_rub=40_000), items)
    assert r.selected[0] == "h-bb"  # the stated meal is kept, the budget gives way
    risks = " ".join(r.explanations[0].risks)
    assert "не укладывается в бюджет 40 000 ₽" in risks
    assert "в бюджет укладывается Отель ro" in risks and "без питания" in risks
    assert "отель 30 000 ₽ укладывается" in risks  # the road makes the overrun
    alt = next(b for b in r.bundles if b.label == "в бюджете")
    assert "h-ro" in alt.offer_ids and any(t.startswith("нарушает") for t in alt.tradeoffs)


def test_meal_includes_the_asked_one():
    items = offers([hotel("ro", 10_000), hotel("ai", 30_000, meal="AI")], FLIGHTS)
    assert hotel_of(spec(meal="BB"), items).offer_id == "h-ai"  # all inclusive has breakfast
    assert hotel_of(spec(meal="RO"), items).offer_id == "h-ro"


def test_meal_fallback_says_what_is_missing():
    items = offers([hotel("ro", 10_000), hotel("bb", 12_000, meal="BB")], FLIGHTS)
    r = optimize(spec(meal="AI"), items)
    risks = r.explanations[0].risks
    assert risks[0].startswith("ни один вариант не подходит")
    assert any("а нужно всё включено" in x for x in risks)


def test_refundable_true_is_a_filter_false_is_not():
    items = offers([hotel("n", 10_000), hotel("r", 14_000, refundable=True)], FLIGHTS)
    assert hotel_of(spec(refundable=True), items).offer_id == "h-r"
    assert hotel_of(spec(refundable=False, priorities=["price"]), items).offer_id == "h-n"


def test_modes_filter_keeps_the_asked_transport():
    legs = [
        *FLIGHTS,
        leg("tr1", 9000, mode="train", hours=20),
        leg("tr2", 9000, there=False, mode="train", hours=20),
    ]
    items = offers([hotel("a", 10_000)], legs)
    assert {o.mode for o in picked(spec(), items) if o.kind == "transport"} == {"flight"}
    assert {o.mode for o in picked(spec(modes=["train"]), items) if o.kind == "transport"} == {
        "train"
    }


def test_max_travel_hours_drops_a_long_leg():
    legs = [
        leg("long", 5000, hours=7),
        leg("short", 12_000, hours=3),
        leg("back", 8000, there=False, hours=3),
    ]
    items = offers([hotel("a", 10_000)], legs)
    ids = rank(spec(max_travel_hours=5, priorities=["price"]), items)[0]
    assert "t-short" in ids and "t-long" not in ids
    assert "t-long" in rank(spec(priorities=["price"]), items)[0]


def test_pets_need_the_hotel_and_every_leg():
    legs = [
        *FLIGHTS,
        leg("tr1", 12_000, mode="train", hours=20, pets_allowed=True),
        leg("tr2", 12_000, there=False, mode="train", hours=20, pets_allowed=True),
    ]
    items = offers([hotel("no", 9000), hotel("dog", 11_000, pets_allowed=True)], legs)
    chosen = picked(spec(pets=True), items)
    assert all(o.pets_allowed for o in chosen if o.kind in ("hotel", "transport"))
    assert {o.offer_id for o in chosen} >= {"h-dog", "t-tr1", "t-tr2"}


@pytest.mark.parametrize(
    ("update", "amenities"),
    [
        ({"children_ages": [5]}, ["family"]),
        ({"children_ages": [5]}, ["kids_club"]),
        ({"wishes": ["тяжело ходить"]}, ["elevator"]),
        ({"wishes": ["тяжело ходить"]}, ["wheelchair"]),
    ],
)
def test_guests_need_the_hotel_amenities(update, amenities):
    items = offers([hotel("plain", 9000), hotel("fit", 12_000, amenities=amenities)], FLIGHTS)
    assert hotel_of(spec(**update), items).offer_id == "h-fit"
    assert hotel_of(spec(), items).offer_id == "h-plain"


def test_car_goes_both_ways_without_transfers_and_needs_parking():
    legs = [
        *FLIGHTS,
        leg("car1", 6000, mode="car", hours=20, pets_allowed=True),
        leg("car2", 6000, there=False, mode="car", hours=20, pets_allowed=True),
    ]
    items = offers([hotel("p", 10_000, amenities=["parking"]), hotel("np", 9000)], legs)
    by_id = {o.offer_id: o for o in items}
    s = spec(modes=["car"])
    for b in optimize(s, items).bundles:
        modes = {by_id[i].mode for i in b.offer_ids if by_id[i].kind == "transport"}
        assert len(modes) == 1
        if modes == {"car"}:
            assert not any(by_id[i].kind == "transfer" for i in b.offer_ids)
    assert set(rank(s, items)[0]) == {"h-p", "t-car1", "t-car2"}


def test_priority_order_changes_the_pick():
    """S9: priorities[0] = cancellation lifts the refundable tariff over the cheapest one."""
    cheap = hotel("cheap", 10_000, rating=7.0)
    refund = hotel("refund", 11_800, refundable=True, rating=7.5)
    star = hotel("star", 25_000, rating=9.6, stars=5)
    items = offers([cheap, refund, star], FLIGHTS)
    assert hotel_of(spec(priorities=["price"]), items) == cheap
    assert hotel_of(spec(priorities=["rating"]), items) == star
    s = spec(priorities=["cancellation", "price"])
    assert hotel_of(s, items) == refund
    first = rank(s, items)[1][0]
    assert first.offer_id == "h-refund" and "отмен" in first.why
    assert "отмена → цена" in first.why
    assert "дороже самого дешёвого варианта на 1 800 ₽" in " ".join(first.tradeoffs)


def test_weights_follow_the_order_and_default_when_empty():
    assert weights(spec()) == DEFAULT_WEIGHTS
    w = weights(spec(priorities=["cancellation", "rating"]))
    assert abs(sum(w.values()) - 1) < 1e-9
    rest = [v for k, v in w.items() if k not in ("cancellation", "rating")]
    assert w["cancellation"] > w["rating"] > max(rest)
    cheap = weights(spec(wishes=["недорого"]))
    assert max(cheap, key=cheap.get) == "price"


def test_alternatives_differ_in_kind():
    s = spec(destinations=["Казань"], date_from=date(2030, 12, 11), date_to=date(2030, 12, 13))
    s = s.model_copy(update={"nights": 2})
    items = find_offers(s, "Казань")
    out = bundles(s, items)
    assert 2 <= len(out) <= 4
    assert len({b.label for b in out}) == len(out)
    assert len({tuple(b.offer_ids) for b in out}) == len(out)
    assert len({b.bundle_id for b in out}) == len(out)
    assert _no_transfers(out[0].offer_ids, items) == rank(s, items)[0]
    cheapest = next((b for b in out if b.label == "самый дешёвый"), out[0])
    assert cheapest.total_rub == min(b.total_rub for b in out)
    fastest = next((b for b in out if b.label == "самый быстрый"), None)
    assert fastest is None or fastest.travel_hours < out[0].travel_hours


def test_tie_break_does_not_depend_on_the_offer_order():
    twins = [hotel("x", 10_000), hotel("y", 10_000)]
    items = offers(twins, FLIGHTS)
    first = optimize(spec(), items)
    for seed in range(5):
        shuffled = items[:]
        random.Random(seed).shuffle(shuffled)
        assert optimize(spec(), shuffled) == first
    assert first.selected[0] == "h-x"  # equal trips: the lower offer_id


def test_return_leg_is_the_cheapest_when_price_comes_first():
    """PR #35: the return leg was back[:1] without sorting, the text said «самый дешёвый»."""
    legs = [
        FLIGHTS[0],
        leg("b1", 12_400, there=False),
        leg("b2", 10_900, there=False),
        leg("b3", 10_400, there=False),
    ]
    items = offers([hotel("a", 10_000)], legs)
    s = spec(priorities=["price"])
    assert "t-b3" in rank(s, items)[0]


@pytest.mark.parametrize(
    "update",
    [{}, {"meal": "BB", "budget_rub": 40_000}, {"priorities": ["rating"]}, {"pets": True}],
)
def test_leg_is_called_cheapest_only_when_it_is(update):
    s = spec(**update)
    items = find_offers(s, "Сочи")
    by_id = {o.offer_id: o for o in items}
    for e in rank(s, items)[1]:
        o = by_id[e.offer_id]
        if o.kind == "transport" and "самый дешёвый" in e.why:
            same = [
                x.price_rub
                for x in items
                if x.kind == "transport" and (x.origin, x.destination) == (o.origin, o.destination)
            ]
            assert o.price_rub == min(same)


RUB_RE = re.compile(r"(\d[\d ]*) ₽")


def _allowed_amounts(s, items, out):
    """Prices, trip totals and parts, their differences, the budget and the overrun."""
    by_id = {o.offer_id: o for o in items}
    base = {o.price_rub for o in items}
    for b in out:
        parts = [by_id[i] for i in b.offer_ids]
        base.add(b.total_rub)
        base.add(sum(o.price_rub for o in parts if o.kind == "transport"))
        base.add(sum(o.price_rub for o in parts if o.kind == "transfer"))
    if s.budget_rub is not None:
        base |= {s.budget_rub} | {t - s.budget_rub for t in base if t > s.budget_rub}
    return base | {abs(a - b) for a, b in combinations(base, 2)}


@pytest.mark.parametrize(
    ("place", "update"),
    [
        ("Сочи", {"meal": "BB", "budget_rub": 40_000}),
        ("Сочи", {"budget_rub": 1000}),
        ("Казань", {"priorities": ["cancellation", "price"]}),
        ("Анталия", {"children_ages": [5], "budget_rub": 150_000}),
    ],
)
def test_trap_4_texts_use_only_offer_facts(place, update):
    """TRAP-4: explanations refer to real offer ids; every amount comes from the offers."""
    s = spec(destinations=[place], **update)
    items = find_offers(s, place)
    r = optimize(s, items)
    ids = {o.offer_id for o in items}
    assert r.selected and set(r.selected) <= ids
    assert {e.offer_id for e in r.explanations} <= ids
    assert all(set(b.offer_ids) <= ids for b in r.bundles)
    allowed = _allowed_amounts(s, items, r.bundles)
    texts = [x for e in r.explanations for x in (e.why, *e.tradeoffs, *e.risks)]
    texts += [t for b in r.bundles for t in b.tradeoffs]
    for text in texts:
        for m in RUB_RE.finditer(text):
            assert int(m.group(1).replace(" ", "")) in allowed, text


def test_no_hotels_gives_an_empty_ranking():
    assert rank(spec(), []) == ([], [])
    assert bundles(spec(), FLIGHTS) == []


def test_replan_fills_the_bundles_with_the_ranking():
    s = spec(date_from=date(2030, 11, 20), date_to=date(2030, 11, 23))
    plan, _, done = dag.replan(Plan(spec=TripSpec()), s, Cart())
    assert "ranking" in done and plan.bundles
    assert _no_transfers(plan.bundles[0].offer_ids, plan.offers) == plan.selected
    edited, _, _ = dag.replan(plan, s.model_copy(update={"budget_rub": 1000}), Cart())
    assert edited.bundles[0].label == "ближе всего к условиям"
