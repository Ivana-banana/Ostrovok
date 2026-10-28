"""Optimizer (T7, #13): whole-trip bundles, hard filters, weights, "what was sacrificed"."""

import random
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from talk import say

from api import dag
from api.contracts import (
    Cancellation,
    Cart,
    ExtraOffer,
    HotelOffer,
    Plan,
    TransferOffer,
    TransportOffer,
    TripSpec,
)
from api.offers import find_offers, transfer_id
from api.optimizer import (
    DEFAULT_WEIGHTS,
    _pool,
    bundles,
    enumerate_trips,
    explain,
    hotel_rows,
    optimize,
    order_tariffs,
    pick_hotel,
    rank,
    weights,
)
from api.store import SessionStore

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
    assert out[0].offer_ids == rank(s, items)[0]


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
    assert "съедают экономию 5 500 ₽ (синтетика) на отеле" in " ".join(out[second].tradeoffs)


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
    assert f"на {over:,} ₽ (синтетика) больше".replace(",", " ") in risks
    assert "самая дешёвая поездка из найденных" in risks
    assert r.bundles[0].label == "ближе всего к условиям"


def test_hotel_budget_limits_the_hotel_not_the_whole_trip():
    """#41: «отель до 20 000» — the hotel price, the flights above it do not count."""
    good, cheap = hotel("good", 40_000, rating=9.5, stars=5), hotel("cheap", 15_000, rating=7.0)
    items = offers([good, cheap], FLIGHTS)
    s = spec(priorities=["rating"], hotel_budget_rub=20_000)
    assert hotel_of(s, items) == cheap
    r = optimize(s, items)
    assert r.explanations[0].risks == []
    assert "отель в бюджете отеля 20 000 ₽" in r.explanations[0].why
    # 31 000 ₽ for the whole trip fits the same hotel under a trip budget of 35 000
    both = spec(priorities=["rating"], hotel_budget_rub=20_000, budget_rub=35_000)
    assert hotel_of(both, items) == cheap
    wide = spec(priorities=["rating"], hotel_budget_rub=45_000)
    assert hotel_of(wide, items) == good  # the budget does not push a fitting better hotel out


def test_hotel_budget_fallback_names_the_overrun_of_the_hotel():
    """D-006: nothing fits the hotel budget — the smallest overrun, named in the risks."""
    items = offers([hotel("a", 25_000), hotel("b", 30_000, rating=9.5)], FLIGHTS)
    s = spec(priorities=["rating"], hotel_budget_rub=20_000)
    r = optimize(s, items)
    assert r.selected[0] == "h-a"
    risks = " ".join(r.explanations[0].risks)
    assert (
        "отель не укладывается в бюджет отеля 20 000 ₽: 25 000 ₽ (синтетика), на 5 000 ₽ (синтетика) больше"
        in risks
    )
    assert "самая дешёвая поездка из найденных" in risks
    assert "в бюджете отеля" not in r.explanations[0].why


def test_hotel_budget_gives_way_to_a_broken_requirement_last():
    """The meal is kept over the hotel budget, as it is over the trip budget (D-006)."""
    bb, ro = hotel("bb", 30_000, meal="BB"), hotel("ro", 10_000, meal="RO")
    items = offers([bb, ro], FLIGHTS)
    r = optimize(spec(meal="BB", hotel_budget_rub=20_000), items)
    assert r.selected[0] == "h-bb"
    assert "бюджет отеля 20 000 ₽" in " ".join(r.explanations[0].risks)
    assert "в бюджете отеля" not in " ".join(b.label for b in r.bundles)
    alt = next(b for b in r.bundles if b.label == "в бюджете")
    assert "h-ro" in alt.offer_ids


def test_budget_risk_looks_at_every_hotel_not_only_the_meal_ones():
    """PR #35: the breakfast hotel is over the budget, a hotel without meals fits."""
    bb, ro = hotel("bb", 30_000, meal="BB"), hotel("ro", 10_000, meal="RO")
    items = offers([bb, ro], FLIGHTS)
    r = optimize(spec(meal="BB", budget_rub=40_000), items)
    assert r.selected[0] == "h-bb"  # the stated meal is kept, the budget gives way
    risks = " ".join(r.explanations[0].risks)
    assert "не укладывается в бюджет 40 000 ₽" in risks
    assert "в бюджет укладывается Отель ro" in risks and "без питания" in risks
    assert "отель 30 000 ₽ (синтетика) укладывается" in risks  # the road makes the overrun
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
    assert out[0].offer_ids == rank(s, items)[0]
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
    """Prices, trip totals and parts, their differences, the budget and the overrun.

    The sums come from price_rub of the offers, not from total_rub of the bundles. A difference
    needs a bundle sum or a price of a bundle offer on one side: pairs of any numbers would
    allow almost every round amount.
    """
    by_id = {o.offer_id: o for o in items}
    base = {o.price_rub for o in items}
    bund = set()
    for b in out:
        parts = [by_id[i] for i in b.offer_ids]
        road = sum(o.price_rub for o in parts if o.kind == "transport")
        moves = sum(o.price_rub for o in parts if o.kind == "transfer")
        whole = sum(o.price_rub for o in parts)
        assert b.total_rub == whole, b  # the bundle total is the sum of its offers
        bund |= {whole, road, moves, *(o.price_rub for o in parts)}
    base |= bund
    pool, _ = _pool(s, enumerate_trips(items))  # the texts compare trips of the pool
    base |= {t.total for t in pool}
    if s.budget_rub is not None:
        base |= {s.budget_rub} | {t - s.budget_rub for t in base if t > s.budget_rub}
    if s.hotel_budget_rub is not None:
        hb = s.hotel_budget_rub
        base |= {hb} | {o.price_rub - hb for o in items if o.price_rub > hb}
    return base | {abs(a - b) for a in bund for b in base}


@pytest.mark.parametrize(
    ("place", "update"),
    [
        ("Сочи", {"meal": "BB", "budget_rub": 40_000}),
        ("Сочи", {"budget_rub": 1000}),
        ("Казань", {"priorities": ["cancellation", "price"]}),
        ("Анталия", {"children_ages": [5], "budget_rub": 150_000}),
        ("Сочи", {"budget_rub": 1000, "priorities": ["rating", "price"]}),
        ("Казань", {"priorities": ["travel_time"], "budget_rub": 20_000}),
        ("Анталия", {"meal": "AI", "priorities": ["price", "location"]}),
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
    assert plan.bundles[0].offer_ids == plan.selected
    edited, _, _ = dag.replan(plan, s.model_copy(update={"budget_rub": 1000}), Cart())
    assert edited.bundles[0].label == "ближе всего к условиям"


S6 = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё."


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def _itogo(reply):
    return int(re.search(r"Итого (\d[\d ]*) ₽", reply).group(1).replace(" ", ""))


def _transfers_of(hotel, items, ids):
    by_id = {o.offer_id: o for o in items}
    legs = [by_id[i] for i in ids if by_id[i].kind == "transport" and by_id[i].mode != "car"]
    return {transfer_id(hotel.offer_id, x.offer_id) for x in legs}


def test_ranked_trip_has_one_total_in_chat_bundle_and_cart(store):
    r = say(store, S6)
    items = r.plan.offers
    assert r.plan.selected == r.plan.bundles[0].offer_ids
    total_rub = r.plan.bundles[0].total_rub
    assert _itogo(r.reply) == total_rub == total(r.plan.selected, items)
    assert any(o.kind == "transfer" for o in items if o.offer_id in r.plan.selected)
    for text in ("беру", "да"):
        r = say(store, text, r.session_id)
    assert r.stage == "checkout" and r.cart.total_rub == total_rub
    assert [o.offer_id for o in r.cart.items] == r.plan.selected


def test_picked_hotel_brings_only_its_own_transfers(store):
    """«беру второй»: the 2nd hotel and its transfers for the selected legs, totals equal."""
    r = say(store, S6)
    second = hotel_rows(r.plan.offers, r.plan.selected)[1]
    assert second.offer_id not in r.plan.selected  # the ranking chose another hotel
    r = say(store, "беру второй", r.session_id)
    assert r.stage == "assemble"
    items = r.plan.offers
    chosen = [o for o in items if o.offer_id in r.plan.selected]
    assert [o.offer_id for o in chosen if o.kind == "hotel"] == [second.offer_id]
    got = {o.offer_id for o in chosen if o.kind == "transfer"}
    assert got and got == _transfers_of(second, items, r.plan.selected)
    assert _itogo(r.reply) == total(r.plan.selected, items)
    r = say(store, "да", r.session_id)
    assert r.stage == "checkout"
    assert {o.offer_id for o in r.cart.items} == set(r.plan.selected)
    assert r.cart.total_rub == sum(o.price_rub for o in r.cart.items) == _itogo_cart(r.reply)


def _itogo_cart(reply):
    return int(re.search(r"на (\d[\d ]*) ₽", reply).group(1).replace(" ", ""))


def test_replan_keeps_the_pick_with_its_own_transfers(store):
    r = say(store, S6)
    r = say(store, "беру второй", r.session_id)
    pick = next(o for o in r.plan.offers if o.offer_id in r.plan.selected and o.kind == "hotel")
    r = say(store, "а бюджет 60 000 руб", r.session_id)  # a re-rank keeps the manual pick
    hotel = next(o for o in r.plan.offers if o.offer_id in r.plan.selected and o.kind == "hotel")
    assert hotel.title == pick.title
    got = {
        o.offer_id for o in r.plan.offers if o.offer_id in r.plan.selected and o.kind == "transfer"
    }
    assert got == _transfers_of(hotel, r.plan.offers, r.plan.selected)


def test_pick_hotel_swaps_the_hotel_part_and_keeps_the_legs():
    a, b = hotel("A", 10_000), hotel("B", 12_000)
    extra = ExtraOffer(offer_id="e-A", title="Ранний заезд", for_offer_id="h-A", price_rub=900)
    items = [*offers([a, b], FLIGHTS), extra]
    old = [*rank(spec(), items)[0], "e-A"]
    assert old[0] == "h-A"
    new = pick_hotel(old, items, "h-B")
    want = {transfer_id("h-B", x.offer_id) for x in FLIGHTS}
    assert new == ["h-B", "t-out", "t-back", *sorted(want, key=new.index)]
    assert set(new[3:]) == want  # no transfer or add-on of hotel A is left
    with pytest.raises(TypeError):
        pick_hotel(old, items, "t-out")


S9_FIRST = "Казань, 11–13 декабря, двое взрослых, из Москвы"


def _first_hotel(reply):
    by_id = {o.offer_id: o for o in reply.plan.offers}
    top = reply.plan.bundles[0]
    return top, next(by_id[i] for i in top.offer_ids if isinstance(by_id[i], HotelOffer))


def test_default_order_puts_the_cheapest_trip_first():
    """#50: nothing said about priorities -> the cheapest whole trip is the first option."""
    cheap = hotel("cheap", 10_000, rating=7.0)
    refund = hotel("refund", 11_800, refundable=True, rating=7.5)
    star = hotel("star", 25_000, rating=9.6, stars=5)
    items = offers([cheap, refund, star], FLIGHTS)
    for s in (spec(), spec(wishes=["недорого"])):
        assert hotel_of(s, items) == cheap
    assert hotel_of(spec(priorities=["cancellation", "price"]), items) == refund


def test_s9_edit_priorities_changes_the_first_option(store, monkeypatch):
    """S9 (eval/cases.json, check_S9): the first option is the cheapest non-refundable one;
    «мне важнее бесплатная отмена» moves only the priorities and puts a refundable tariff first,
    the text names the priority and the price of the trade-off."""
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    r = say(store, S9_FIRST)
    while r.stage == "clarify":
        r = say(store, " ".join(q.options[0] for q in r.questions if q.options), r.session_id)
    assert r.stage == "compare"
    top, first = _first_hotel(r)
    assert top.total_rub == min(b.total_rub for b in r.plan.bundles)
    assert not first.cancellation.refundable

    after = say(store, "Мне важнее бесплатная отмена, чем цена.", r.session_id)
    spec_after, spec_before = after.plan.spec, r.plan.spec
    assert spec_after.priorities[:1] == ["cancellation"] and spec_after.refundable is None
    assert spec_after.model_dump(exclude={"priorities"}) == spec_before.model_dump(
        exclude={"priorities"}
    )
    assert after.questions == []
    top, hotel_after = _first_hotel(after)
    assert hotel_after.cancellation.refundable
    expl = next(e for e in after.plan.explanations if e.offer_id == hotel_after.offer_id)
    text = " ".join([expl.why, *expl.tradeoffs])
    gap = top.total_rub - min(b.total_rub for b in after.plan.bundles)
    assert gap > 0 and "отмен" in text.lower() and f"{gap:,} ₽".replace(",", " ") in text
    assert "плата за приоритет «отмена»" in text


@pytest.mark.parametrize(
    "city,nights,priorities",
    [("Екатеринбург", 2, []), ("Краснодар", 3, []), ("Екатеринбург", 2, ["cancellation"])],
)
def test_a_leg_longer_than_one_night_does_not_go_first_when_a_flight_exists(
    city, nights, priorities
):
    """#50 review: the price weight must not send the user on a 20+ hour train by default."""
    day = date(2026, 12, 11)
    s = TripSpec(
        origin="Москва",
        destinations=[city],
        date_from=day,
        date_to=day + timedelta(days=nights),
        nights=nights,
        priorities=priorities,
    )
    items = find_offers(s, city)
    by_id = {o.offer_id: o for o in items}
    assert any(o.kind == "transport" and o.mode == "flight" for o in items)
    selected, _ = rank(s, items)
    modes = [by_id[i].mode for i in selected if by_id[i].kind == "transport"]
    assert modes and set(modes) == {"flight"}


def test_the_overnight_train_may_go_first_and_a_named_price_priority_is_kept():
    kazan = TripSpec(
        origin="Москва",
        destinations=["Казань"],
        date_from=date(2026, 12, 11),
        date_to=date(2026, 12, 13),
        nights=2,
    )
    items = find_offers(kazan, "Казань")
    by_id = {o.offer_id: o for o in items}
    assert {by_id[i].mode for i in rank(kazan, items)[0] if by_id[i].kind == "transport"} == {
        "train"
    }
    ural = kazan.model_copy(update={"destinations": ["Екатеринбург"], "priorities": ["price"]})
    items = find_offers(ural, "Екатеринбург")
    by_id = {o.offer_id: o for o in items}
    legs = [by_id[i] for i in rank(ural, items)[0] if by_id[i].kind == "transport"]
    assert {x.mode for x in legs} == {"train"}  # the user put the price first: their choice


def test_trade_off_names_the_priority_only_when_the_first_option_wins_on_it():
    """#50 review: the price gap from a faster leg is not the price of «отмена»."""
    cheap = hotel("cheap", 10_000, refundable=True, rating=7.0)
    pricey = hotel("pricey", 14_000, refundable=True, rating=9.0)
    items = offers([cheap, pricey], FLIGHTS)
    s = spec(priorities=["cancellation", "rating"])
    first = rank(s, items)[1][0]
    assert first.offer_id == "h-pricey"
    assert "плата за приоритет" not in " ".join(first.tradeoffs)  # both are refundable
    plain = hotel("plain", 10_000, rating=7.0)
    items = offers([plain, hotel("refund", 11_800, refundable=True, rating=7.5)], FLIGHTS)
    first = rank(spec(priorities=["cancellation", "price"]), items)[1][0]
    assert "плата за приоритет «отмена»" in " ".join(first.tradeoffs)


def _ural():
    day = date(2026, 12, 11)
    s = TripSpec(
        origin="Москва",
        destinations=["Екатеринбург"],
        date_from=day,
        date_to=day + timedelta(days=2),
        nights=2,
    )
    items = find_offers(s, "Екатеринбург")
    return s, items, {o.offer_id: o for o in items}


def test_cheap_in_wishes_keeps_the_long_train_down_and_a_price_priority_lifts_it():
    """#53, D-009: «недорого» is a wish; only an explicit price priority sends the user by train."""
    base, items, by_id = _ural()

    def modes(s):
        return {by_id[i].mode for i in rank(s, items)[0] if by_id[i].kind == "transport"}

    assert any(o.kind == "transport" and o.mode == "flight" for o in items)
    assert modes(base.model_copy(update={"wishes": ["недорого"]})) == {"flight"}
    assert modes(base.model_copy(update={"priorities": ["price"]})) == {"train"}


def test_why_for_cheap_wish_names_the_cheaper_option_with_its_road_hours():
    """#53: not «важнее всего цена» when the first package is not the cheapest (TRAP-4)."""
    s, items, _ = _ural()
    s = s.model_copy(update={"wishes": ["недорого"]})
    why = rank(s, items)[1][0].why
    assert "важнее всего цена" not in why
    assert "цена важна («недорого»), самый дешёвый вариант ниже: дорога " in why
    trains = [o for o in items if o.kind == "transport" and o.mode == "train"]
    hours = {f"{round((o.arrive_at - o.depart_at).total_seconds() / 3600, 1):g} ч" for o in trains}
    assert any(why.endswith(f"дорога {h}") for h in hours)


def test_why_for_cheap_wish_gives_no_hours_when_the_cheaper_road_is_not_slow():
    """The cheaper package differs by the hotel, not by a long leg: no «дорога N ч»."""
    cheap = hotel("cheap", 10_000, rating=5.0)
    better = hotel("better", 10_300, refundable=True, rating=9.8, meal="BB")
    far = hotel("far", 30_000, rating=6.0)  # a wide price range: 300 ₽ is a small gap
    first = rank(spec(wishes=["недорого"]), offers([cheap, better, far], FLIGHTS))[1][0]
    assert first.offer_id == "h-better"
    assert first.why.endswith("цена важна («недорого»), самый дешёвый вариант ниже")


def test_why_for_cheap_wish_keeps_the_old_text_when_the_first_package_is_the_cheapest():
    first = rank(spec(wishes=["недорого"]), offers([hotel("a", 10_000)], FLIGHTS))[1][0]
    assert first.why.endswith("важнее всего цена («недорого»)")


def test_slow_leg_line_gives_the_hours_of_the_leg_that_made_it_slow():
    """#53: the slow leg is the way back; the line must not show the hours of the leg there."""
    hotel_a = hotel("a", 10_000)
    legs = [
        leg("out", 8000),
        leg("back-flight", 8000, there=False),
        leg("back-train", 1000, there=False, mode="train", hours=20),
    ]
    first = rank(spec(), offers([hotel_a], legs))[1][0]
    line = next(x for x in first.tradeoffs if "дорога в одну сторону" in x)
    assert "дорога в одну сторону 20 ч" in line
    assert "back-train" in line  # the title of the slow leg


def test_compare_lists_a_hotel_once_with_its_best_tariff(store):
    """#39: one row per hotel in the chat and in «беру N»; the row shows the tariff of the spec."""
    r = say(store, S6)
    hotels = [o for o in r.plan.offers if o.kind == "hotel"]
    rows = hotel_rows(r.plan.offers, r.plan.selected)
    assert len(hotels) > len(rows) and 3 <= len(rows) <= 5
    assert len({h.title for h in rows}) == len(rows)
    assert "на доске" in r.reply and len(r.reply.splitlines()) <= 12  # #349: no list in chat
    assert not any(h.title in r.reply.split("Предлагаю:")[0] for h in rows)
    assert sum(h.offer_id in r.plan.selected for h in rows) == 1  # the ranked tariff is a row
    assert rows[1].offer_id not in r.plan.selected
    r = say(store, "второй", r.session_id)
    assert [
        o.offer_id for o in r.plan.offers if o.offer_id in r.plan.selected and o.kind == "hotel"
    ] == [rows[1].offer_id]


def test_order_tariffs_puts_the_tariff_of_the_spec_first():
    """The first tariff of a hotel is its best one for the spec; the offers are the same."""
    s = spec(meal="AI")
    items = find_offers(s, "Сочи")
    ordered = order_tariffs(s, items)
    assert sorted(o.offer_id for o in ordered) == sorted(o.offer_id for o in items)
    first = {h.title: h for h in reversed(hotel_rows(ordered, []))}
    sea = next(h for h in first.values() if h.stars == 5)
    assert sea.meal == "AI"  # the only hotel with all inclusive


def _texts(r):
    out = [x for e in r.explanations for x in (e.why, *e.tradeoffs, *e.risks)]
    return out + [t for b in r.bundles for t in b.tradeoffs]


@pytest.mark.parametrize(
    ("best_kw", "other_kw"),
    [
        ({"km": None, "rating": None}, {"km": 3.0, "rating": 9.0}),
        ({"km": 1.0, "rating": 9.0}, {"km": None, "rating": None}),
        ({"km": None, "rating": 9.0}, {"km": 3.0, "rating": None}),
        ({"km": None, "rating": None}, {"km": None, "rating": None}),
    ],
)
def test_hotel_without_distance_and_rating_writes_no_absent_fact(best_kw, other_kw):
    """Contract: distance_center_km and rating may be None; the text then says nothing of them."""
    a = hotel("a", 10_000, **best_kw)  # the best by price
    b = hotel("b", 9_000, **other_kw)  # the cheapest hotel, dear transfers
    c = hotel("c", 14_000, meal="AI", refundable=True, rating=9.5, stars=5, km=0.1)  # comfortable
    items = offers([a, b, c], FLIGHTS, fares={"a": 500, "b": 3000, "c": 500})
    for update in ({}, {"priorities": ["rating"]}, {"priorities": ["location"]}):
        r = optimize(spec(**update), items)
        assert r.selected and len(r.bundles) >= 2
        for text in _texts(r):
            assert "None" not in text, text
        if r.selected[0] == "h-a":
            why = r.explanations[0].why
            assert ("рейтинг" in why) == (best_kw["rating"] is not None)
            assert ("от центра" in why) == (best_kw["km"] is not None)


def test_unknown_rating_or_distance_is_not_called_worse():
    """A hotel with no rating does not «lose» on rating and does not «win» on distance."""
    known = hotel("a", 10_000, km=2.0, rating=9.0)
    blank = hotel("b", 9_000, km=None, rating=None)
    items = offers([known, blank], FLIGHTS)
    r = optimize(spec(priorities=["price", "location"]), items)
    assert r.selected[0] == "h-b"
    text = " ".join(_texts(r))
    assert "рейтинг ниже" not in text and "до центра" not in text and "None" not in text


def test_switch_transport_why_handles_hotel_without_distance_and_rating():
    """model._explain calls the optimizer helpers: a hotel with None fields must not break it."""
    from api.model import switch_transport

    h = hotel("a", 10_000, km=None, rating=None)
    train = [leg("train", 9000, mode="train", hours=9), leg("tb", 9000, there=False, mode="train")]
    items = offers([h], [*FLIGHTS, *train])
    s = spec()
    r = optimize(s, items)
    plan = Plan(spec=s, offers=items, bundles=r.bundles, selected=r.selected)
    new, note = switch_transport(plan, "возьми поезд")
    assert new is not None, note
    assert "t-train" in new.selected
    assert new.explanations
    for e in new.explanations:
        assert "None" not in " ".join([e.why, *e.tradeoffs, *e.risks])


def test_in_budget_bundle_is_among_the_alternatives_even_when_others_fill_the_list():
    """Over the budget the «в бюджете» bundle goes first, so MAX_ALTERNATIVES keeps it."""
    a = hotel("a", 20_000, meal="BB", rating=7.0, km=2.0)  # the smallest overrun: the best
    b = hotel("b", 18_000, meal="BB", rating=7.0, km=2.0)  # cheapest hotel, dear transfers
    c = hotel("c", 24_000, meal="BB", rating=9.5, stars=5, km=0.2)  # most comfortable
    d = hotel("d", 8_000, meal="RO", rating=6.0, km=5.0)  # in the budget, breaks the meal
    legs = [
        leg("slow", 3000, hours=5),
        leg("slowb", 3000, there=False, hours=5),
        leg("fast", 5000, hours=1.5),
        leg("fastb", 5000, there=False, hours=1.5),
    ]
    items = offers([a, b, c, d], legs, fares={"a": 500, "b": 4000, "c": 500, "d": 500})
    s = spec(meal="BB", budget_rub=25_000)
    r = optimize(s, items)
    labels = [x.label for x in r.bundles]
    assert r.selected[0] == "h-a" and r.bundles[0].total_rub > 25_000
    assert labels[1] == "в бюджете", labels
    assert len(r.bundles) == 1 + 3
    assert r.bundles[1].total_rub <= 25_000


def test_allowed_amounts_reject_a_made_up_round_difference():
    """A text with a round difference that no sums of the offers give (a mutant of _diff) fails."""
    a, b = hotel("a", 10_000), hotel("b", 14_000, rating=9.0)
    items = offers([a, b], FLIGHTS)
    s = spec(priorities=["rating"])
    r = optimize(s, items)
    allowed = _allowed_amounts(s, items, r.bundles)
    real = {int(m.group(1).replace(" ", "")) for t in _texts(r) for m in RUB_RE.finditer(t)}
    assert real and real <= allowed
    for k in (100, 500, 1500):  # a mutant prints d + k instead of d
        assert not {n + k for n in real} <= allowed, k


def _trip_ids(items, hotel_key, there_key, back_key):
    h = next(o for o in items if o.offer_id == f"h-{hotel_key}")
    legs = [o for o in items if o.offer_id in (there_key, back_key)]
    return pick_hotel([x.offer_id for x in legs], items, h.offer_id)


def _train_case():
    h = hotel("a", 10_000)
    train = [leg("train", 9000, mode="train", hours=9), leg("tb", 9000, there=False, mode="train")]
    items = offers([h], [*FLIGHTS, *train])
    s = spec()
    return s, items, optimize(s, items)


def test_model_imports_only_public_names_of_the_optimizer():
    """#58: model.py uses explain(), not the private helpers of the optimizer."""
    import ast
    from pathlib import Path

    import api.model

    tree = ast.parse(Path(api.model.__file__).read_text(encoding="utf-8"))
    names = [
        a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module == "api.optimizer"
        for a in n.names
    ]
    assert "explain" in names and not [x for x in names if x.startswith("_")]


def test_explain_of_the_best_selection_equals_the_ranking_why():
    s, items, r = _train_case()
    assert explain(s, items, r.selected) == r.explanations
    assert explain(s, items, []) == []


def test_explain_names_the_selected_trip_not_the_best_one():
    s, items, r = _train_case()
    other = _trip_ids(items, "a", "t-train", "t-tb")
    assert other != r.selected
    why = explain(s, items, other)
    assert why[0].offer_id == "h-a"
    assert [e.offer_id for e in why[1:]] == ["t-train", "t-tb"]
    t = total(other, items)
    assert t != total(r.selected, items)
    assert f"поездка целиком {t:,} ₽".replace(",", " ") in why[0].why


def test_bundles_keep_the_best_first_and_add_the_selected_trip_as_own_choice():
    """#58: after a switch the label «лучший» must not sit on a trip the «Почему» does not name."""
    s, items, r = _train_case()
    assert bundles(s, items, r.selected) == r.bundles == bundles(s, items)
    other = _trip_ids(items, "a", "t-train", "t-tb")
    out = bundles(s, items, other)
    assert out[0] == r.bundles[0] and out[0].offer_ids == r.selected  # dag.py reads bundles[0]
    assert out[1].offer_ids == other and out[1].label.startswith("ваш выбор")
    assert len({tuple(b.offer_ids) for b in out}) == len(out)  # each trip once
    assert out[1].total_rub == total(other, items)


def test_switch_transport_recomputes_the_bundles_and_the_why_together():
    from api.model import switch_transport

    s, items, r = _train_case()
    plan = Plan(spec=s, offers=items, bundles=r.bundles, selected=r.selected)
    new, note = switch_transport(plan, "возьми поезд")
    assert new is not None, note
    own = next(b for b in new.bundles if b.label.startswith("ваш выбор"))
    assert set(own.offer_ids) == set(new.selected)
    assert new.explanations[0].offer_id in own.offer_ids
    assert new.bundles[0].label.startswith("лучший") and new.bundles[0] == r.bundles[0]
    assert [e.offer_id for e in new.explanations[1:]] == ["t-train", "t-tb"]
    assert sum(b.label.startswith("лучший") for b in new.bundles) == 1
    best = next(b for b in new.bundles if b.label.startswith("лучший"))
    assert "t-train" not in best.offer_ids  # the best trip is the other transport


@pytest.mark.parametrize(
    ("place", "update"),
    [
        ("Сочи", {"budget_rub": 1000}),
        ("Казань", {"priorities": ["cancellation", "price"]}),
        ("Анталия", {"children_ages": [5], "budget_rub": 150_000}),
        ("Казань", {"priorities": ["travel_time"], "budget_rub": 20_000}),
    ],
)
def test_trap_4_explain_and_selected_bundles_use_only_offer_facts(place, update):
    """TRAP-4: for every trip the user can select (any hotel, tariff, legs) explain() and the
    «ваш выбор» bundle name real ids and only amounts that the offers give."""
    s = spec(destinations=[place], **update)
    items = find_offers(s, place)
    ids = {o.offer_id for o in items}
    trips = enumerate_trips(items)
    assert trips
    for trip in trips[:: max(1, len(trips) // 25)]:  # an even sample keeps the test fast
        out = bundles(s, items, trip.ids)
        why = explain(s, items, trip.ids)
        assert why[0].offer_id == trip.hotel.offer_id
        assert {e.offer_id for e in why} <= set(trip.ids) and set(trip.ids) <= ids
        assert all(set(b.offer_ids) <= ids for b in out)
        allowed = _allowed_amounts(s, items, out)
        texts = [x for e in why for x in (e.why, *e.tradeoffs, *e.risks)]
        texts += [t for b in out for t in b.tradeoffs]
        for text in texts:
            for m in RUB_RE.finditer(text):
                assert int(m.group(1).replace(" ", "")) in allowed, (trip.ids, text)


def test_no_transport_in_the_plan_is_no_whole_trip_and_says_so():
    """#225: a plan with no leg is no «поездка целиком»; the risks say the road has no data."""
    s = spec(budget_rub=1000)
    _, ex = rank(s, [hotel("a", 20000)])
    text = " ".join([ex[0].why, *ex[0].risks])
    assert "поездка целиком" not in text
    assert "как добраться — нет данных" in ex[0].risks
    _, ex = rank(s, [*FLIGHTS, hotel("a", 20000)])
    assert "поездка целиком" in ex[0].why
    assert not any("как добраться" in r for r in ex[0].risks)


def test_bundles_of_a_pick_stay_empty_when_nothing_takes_the_pet():
    """#58 + #131: the optimizer offers no trip for the pet; a hand pick must not crash bundles."""
    items = offers([hotel("no", 9000)], FLIGHTS)
    s = spec(pets=True)
    assert optimize(s, items).bundles == []
    ids = [o.offer_id for o in items if o.kind in ("hotel", "transport")]
    assert bundles(s, items, ids) == []
