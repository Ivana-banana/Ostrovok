"""Re-plan DAG (T6, #10): a spec edit recomputes only the dependent nodes."""

from datetime import date, timedelta

import pytest

from api import dag
from api.contracts import Cart, ChatRequest, Plan, TripSpec
from api.model import chat
from api.optimizer import enumerate_trips, hotel_rows
from api.store import SessionStore

SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых, отель до 40 тысяч, нужен завтрак"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


@pytest.fixture
def booked(store):
    """Session at checkout: offers, selection, timeline and a full cart."""
    r = say(store, SOCHI)
    for text in ("беру", "да"):
        r = say(store, text, r.session_id)
    assert r.stage == "checkout" and r.plan.timeline and r.cart.items
    return r


@pytest.fixture
def calls(monkeypatch):
    """Counts the seam calls made by the DAG."""
    seen: list[str] = []
    for name in ("find_offers", "rank", "build_timeline"):
        real = getattr(dag, name)

        def spy(*a, _real=real, _name=name, **kw):
            seen.append(_name)
            return _real(*a, **kw)

        monkeypatch.setattr(dag, name, spy)
    return seen


@pytest.mark.parametrize(
    ("update", "nodes"),
    [
        ({"destinations": ["Казань"]}, {"destination", "offers", "ranking", "timeline", "cart"}),
        ({"nights": 5}, {"offers", "ranking", "timeline", "cart"}),
        ({"adults": 3}, {"offers", "ranking", "timeline", "cart"}),
        ({"origin": "Санкт-Петербург"}, {"offers", "ranking", "timeline", "cart"}),
        ({"meal": "AI"}, {"ranking", "timeline", "cart"}),
        ({"refundable": True}, {"ranking", "timeline", "cart"}),
        ({"budget_rub": 1000}, {"ranking", "timeline", "cart"}),
        ({"priorities": ["price"]}, {"ranking", "timeline", "cart"}),
        ({"assumptions": ["бюджет: не задан"]}, set()),
    ],
)
def test_dirty_nodes_start_at_the_first_node_that_reads_the_field(update, nodes):
    spec = TripSpec.model_validate({"destinations": ["Сочи"], "origin": "Москва", "meal": "BB"})
    assert dag.dirty_nodes(spec, spec.model_copy(update=update)) == nodes


def test_meal_change_reranks_without_a_new_search(booked, calls):
    spec = booked.plan.spec.model_copy(update={"meal": "AI"})
    plan, cart, done = dag.replan(booked.plan, spec, booked.cart)
    assert done == ["ranking", "timeline", "cart"] and cart.items == []
    assert "find_offers" not in calls
    # transport and hotels are kept, the tariffs of a hotel are only ordered again
    assert sorted(plan.offers, key=lambda o: o.offer_id) == sorted(
        booked.plan.offers, key=lambda o: o.offer_id
    )
    hotel = next(o for o in plan.offers if o.offer_id in plan.selected and o.kind == "hotel")
    assert hotel.meal == "AI"
    assert any(c.what == "питание" for c in plan.changes)


def test_same_selection_keeps_the_timeline(booked, calls):
    # refundable=True would pick the free-cancellation tariff of the hotel: another selection.
    spec = booked.plan.spec.model_copy(update={"refundable": False})
    plan, cart, done = dag.replan(booked.plan, spec, booked.cart)
    assert done == ["ranking", "cart"] and cart.items == []
    assert calls == ["rank"]
    assert plan.selected == booked.plan.selected
    assert plan.timeline == booked.plan.timeline and plan.issues == booked.plan.issues


def test_empty_timeline_of_a_plan_with_offers_is_not_rebuilt_by_a_kept_selection(booked, calls):
    """#79: the timeline node counts as recomputed only when the selection changed or on the
    first plan; an old plan with offers and an empty timeline does not inflate the count."""
    old = booked.plan.model_copy(update={"timeline": [], "issues": []})
    spec = old.spec.model_copy(update={"refundable": False})
    plan, _, done = dag.replan(old, spec, booked.cart)
    assert plan.selected == old.selected
    assert "timeline" not in done and "build_timeline" not in calls


def test_date_shift_searches_again_for_the_same_destination(booked, calls):
    old = booked.plan.spec
    spec = old.model_copy(
        update={
            "date_from": old.date_from + timedelta(days=2),
            "date_to": old.date_to + timedelta(days=2),
        }
    )
    plan, cart, done = dag.replan(booked.plan, spec, booked.cart)
    assert done == ["offers", "ranking", "timeline", "cart"] and cart.items == []
    assert calls == ["find_offers", "rank", "build_timeline"]
    assert plan.spec.destinations == old.destinations
    assert all(h.date_in == spec.date_from for h in plan.offers if h.kind == "hotel")
    assert not {o.offer_id for o in plan.offers} & set(booked.plan.selected)
    dates = next(c for c in plan.changes if c.what == "даты")
    assert dates.before.startswith(f"{old.date_from:%d.%m}")
    assert dates.after.startswith(f"{spec.date_from:%d.%m}")
    assert "месяц" not in {c.what for c in plan.changes}


def test_destination_change_runs_every_node(booked, calls):
    spec = booked.plan.spec.model_copy(update={"destinations": ["Казань"]})
    plan, _, done = dag.replan(booked.plan, spec, booked.cart)
    assert done == list(dag.NODES)
    assert {o.city for o in plan.offers if o.kind == "hotel"} == {"Казань"}


def test_no_change_recomputes_nothing(booked, calls):
    plan, cart, done = dag.replan(booked.plan, booked.plan.spec, booked.cart)
    assert done == [] and calls == []
    assert plan == booked.plan and cart == booked.cart


def test_first_build_runs_all_nodes_without_changes(calls):
    spec = TripSpec.model_validate(
        {
            "destinations": ["Сочи"],
            "origin": "Москва",
            "date_from": date(2030, 11, 20),
            "date_to": date(2030, 11, 23),
            "nights": 3,
        }
    )
    empty = Plan(spec=TripSpec.model_validate({}))
    plan, cart, done = dag.replan(empty, spec, Cart())
    assert done == ["destination", "offers", "ranking", "timeline"]  # #66: the first plan too
    assert calls == ["find_offers", "rank", "build_timeline"]
    assert plan.offers and plan.selected and plan.timeline
    assert plan.changes == [] and cart.items == []


def test_chat_shows_before_after_and_replaces_the_cart(store, booked):
    r = say(store, "а 4 ночи", booked.session_id)
    assert r.stage == "compare" and r.cart.items == []
    assert "Было → стало:" in r.reply and "корзина: " in r.reply
    assert any(c.what == "корзина" and c.after.startswith("пусто") for c in r.plan.changes)
    for text in ("беру", "да"):
        r = say(store, text, r.session_id)
    assert r.stage == "checkout"
    assert [o.offer_id for o in r.cart.items] == r.plan.selected  # replaced, not appended
    hotel = next(o for o in r.cart.items if o.kind == "hotel")
    back = next(o for o in r.cart.items if o.kind == "transport" and o.origin == "Сочи")
    assert hotel.date_out == back.depart_at.date() == r.plan.spec.date_to  # no old dates


def test_spec_edit_at_compare_shows_the_issues_line(store):
    """#79: «стыковки» in «Было → стало» before «беру», when the edit changes the issues."""
    r = say(store, SOCHI)
    assert r.stage == "compare" and r.plan.issues
    old_issues = "; ".join(i.text for i in r.plan.issues)
    r = say(store, "а 4 ночи", r.session_id)
    assert r.stage == "compare"
    line = next(c for c in r.plan.changes if c.what == "стыковки")
    new_issues = "; ".join(i.text for i in r.plan.issues)
    assert (line.before, line.after) == (old_issues, new_issues) and old_issues != new_issues
    assert f"стыковки: {old_issues} → {new_issues}" in r.reply


def test_budget_change_line_is_readable(store, booked):
    r = say(store, "а бюджет 30 000 руб", booked.session_id)
    assert "бюджет: 40 000 ₽ → 30 000 ₽" in r.reply
    assert "budget_rub" not in r.reply


def test_before_after_lives_only_in_the_replan_reply(store, booked):
    r = say(store, "а 4 ночи", booked.session_id)
    assert r.plan.changes
    for text in ("беру", "да"):
        r = say(store, text, r.session_id)
    assert r.stage == "checkout" and r.cart.items
    assert r.plan.changes == []  # no «корзина: … → пусто» next to a full cart


S2_SESSION = "Анталия с 8 ноября на 7 ночей, двое взрослых и ребёнок 5 лет, летим из Москвы"
S2 = "А если на два дня позже? И бюджет тысяч 150 максимум на всё."


def test_s2_shifts_dates_and_budget_without_new_questions(store):
    r = say(store, S2_SESSION)
    for text in ("беру", "да"):
        r = say(store, text, r.session_id)
    old = r.plan.spec
    assert r.stage == "checkout" and r.cart.items and old.date_from and old.date_to
    r = say(store, S2, r.session_id)
    spec = r.plan.spec
    assert r.stage == "compare" and r.questions == [] and r.cart.items == []
    assert spec.date_from == old.date_from + timedelta(days=2)
    assert spec.date_to == old.date_to + timedelta(days=2) and spec.nights == old.nights
    assert spec.budget_rub == 150_000
    assert (spec.destinations, spec.adults, spec.children_ages, spec.origin) == (
        old.destinations,
        old.adults,
        old.children_ages,
        old.origin,
    )
    assert {"даты", "бюджет", "корзина"} <= {c.what for c in r.plan.changes}
    assert all(o.date_in == spec.date_from for o in r.plan.offers if o.kind == "hotel")


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("на два дня позже", 2),
        ("а на 3 дня раньше?", -3),
        ("сдвинь на неделю позже", 7),
        ("на день раньше", -1),
        ("на 3 дня", 0),  # a duration, not a shift
        ("бюджет меньше", 0),
    ],
)
def test_shift_dates_moves_the_current_dates(text, days):
    spec = TripSpec.model_validate(
        {
            "date_from": date(2030, 11, 8),
            "date_to": date(2030, 11, 15),
            "nights": 7,
            "assumptions": ["даты: 08.11–15.11 (точных дат не было)", "откуда: Москва"],
        }
    )
    out = dag.shift_dates(spec, text)
    assert out.date_from == date(2030, 11, 8) + timedelta(days=days)
    assert out.date_to == date(2030, 11, 15) + timedelta(days=days) and out.nights == 7
    assert (out.assumptions == spec.assumptions) == (days == 0)


@pytest.fixture
def picked(store):
    """Session where the user picked the second hotel over the ranked one."""
    r = say(store, SOCHI)
    ranked = r.plan.selected[0]
    r = say(store, "второй", r.session_id)
    assert r.stage == "assemble" and r.plan.selected[0] != ranked
    return r


def _hotel(r):
    return next(o for o in r.plan.offers if o.kind == "hotel" and o.offer_id in r.plan.selected)


def test_replan_keeps_the_hotel_the_user_picked(store, picked):
    title = _hotel(picked).title
    r = say(store, "а бюджет 30 000 руб", picked.session_id)
    assert _hotel(r).title == title and r.plan.explanations[0].why == dag.MANUAL_WHY
    r = say(store, "а 4 ночи", r.session_id)  # new offers: the same hotel for new dates
    assert _hotel(r).title == title and _hotel(r).date_out == r.plan.spec.date_to
    assert "ваш выбор" not in {c.what for c in r.plan.changes}


def test_replan_says_why_the_picked_hotel_changed(store, picked):
    r = say(store, "а можно всё включено", picked.session_id)
    assert _hotel(r).meal == "AI" and _hotel(r).title != _hotel(picked).title
    lost = next(c for c in r.plan.changes if c.what == "ваш выбор")
    assert lost.before == _hotel(picked).title and "всё включено" in lost.after
    assert "ваш выбор: " in r.reply


@pytest.mark.parametrize(("text", "days"), [("а на неделю позже?", 7), ("на две недели позже", 14)])
def test_week_shift_keeps_the_nights(store, text, days):
    r = say(store, SOCHI)
    old = r.plan.spec
    assert old.date_from and old.date_to and old.nights == 3
    r = say(store, text, r.session_id)
    spec = r.plan.spec
    assert spec.nights == 3 and spec.date_from and spec.date_to
    assert spec.date_from == old.date_from + timedelta(days=days)
    assert (spec.date_to - spec.date_from).days == 3


def test_lower_budget_drops_a_pick_that_no_longer_fits(store):
    # The budget comes from the offers, not from fixture prices: providers are synthetic (T5).
    r = say(store, "Сочи, 3 ночи с 20 ноября, двое взрослых")
    hotels = hotel_rows(r.plan.offers, r.plan.selected)[:4]  # a row per hotel, as in the chat
    cheapest = min(h.price_rub for h in hotels)
    i = next(
        i
        for i, h in enumerate(hotels)
        if h.offer_id not in r.plan.selected and h.price_rub > cheapest
    )
    r = say(store, "беру " + ("первый", "второй", "третий", "четвёртый")[i], r.session_id)
    pick = _hotel(r)
    assert r.stage == "assemble" and pick.offer_id not in dag.rank(r.plan.spec, r.plan.offers)[0]
    # The optimizer also swaps transport, so the budget is 100 ₽ below the cheapest whole trip
    # with the pick (T7, #13); cheaper hotels fit.
    budget = min(t.total for t in enumerate_trips(r.plan.offers) if t.hotel.title == pick.title)
    budget -= 100
    r = say(store, f"а бюджет {budget} руб", r.session_id)
    assert _hotel(r).title != pick.title
    assert sum(o.price_rub for o in r.plan.offers if o.offer_id in r.plan.selected) <= budget
    lost = next(c for c in r.plan.changes if c.what == "ваш выбор")
    assert pick.title == lost.before and lost.after.endswith("дороже бюджета")
    assert "дороже бюджета" in r.reply


def _pick_third(store):
    """The user picked the third hotel of the table; returns the reply and the hotel."""
    r = say(store, SOCHI)
    r = say(store, "третий", r.session_id)
    assert (
        r.stage == "assemble" and r.plan.selected[0] not in dag.rank(r.plan.spec, r.plan.offers)[0]
    )
    return r, _hotel(r)


def test_picked_hotel_gets_the_tariff_of_the_new_spec(store):
    """#39: the user picked a hotel, not a tariff; a refund wish re-picks its tariff."""
    r, pick = _pick_third(store)
    assert not pick.cancellation.refundable
    ranked = dag.rank(r.plan.spec, r.plan.offers)[0][0]
    r = say(store, "а нужна бесплатная отмена", r.session_id)
    hotel = _hotel(r)
    assert hotel.title == pick.title and hotel.offer_id != pick.offer_id  # the user's hotel
    assert hotel.cancellation.refundable and hotel.meal == pick.meal
    assert hotel.offer_id != ranked and r.plan.explanations[0].why == dag.MANUAL_WHY
    # the row of the table is the tariff of the plan
    assert hotel_rows(r.plan.offers, r.plan.selected)[2].offer_id == hotel.offer_id


def test_date_shift_keeps_the_tariff_terms_of_the_picked_hotel(store):
    r, pick = _pick_third(store)
    r = say(store, "а нужна бесплатная отмена", r.session_id)
    before = _hotel(r)
    r = say(store, "а 4 ночи", r.session_id)  # new offers: the same hotel and the same terms
    hotel = _hotel(r)
    assert hotel.title == pick.title and hotel.offer_id != before.offer_id
    assert (hotel.meal, hotel.cancellation.refundable) == (
        before.meal,
        before.cancellation.refundable,
    )


def test_manual_pick_comes_from_the_stored_ranking_not_a_second_rank(store, monkeypatch):
    """#37: the pick is read from old.bundles (what the user saw); rank() runs once, for the new spec."""
    r, pick = _pick_third(store)
    assert r.plan.bundles and pick.offer_id not in r.plan.bundles[0].offer_ids
    specs = []
    real = dag.rank

    def spy(spec, offers):
        specs.append(spec)
        return real(spec, offers)

    monkeypatch.setattr(dag, "rank", spy)
    r = say(store, "а нужна бесплатная отмена", r.session_id)
    assert _hotel(r).title == pick.title and r.plan.explanations[0].why == dag.MANUAL_WHY
    assert len(specs) == 1 and specs[0] == r.plan.spec  # no rank() of the old spec


def test_best_labels_match_the_optimizer(store):
    """_ranked_hotel finds the best bundle by label; an optimizer label change must break here."""
    plan = say(store, SOCHI).plan
    tight = plan.spec.model_copy(update={"budget_rub": 100})  # nothing fits: the other label
    fits = dag.bundles(plan.spec, plan.offers)[0].label
    over = dag.bundles(tight, plan.offers)[0].label
    assert fits.startswith(dag.BEST_LABELS[0]) and over.startswith(dag.BEST_LABELS[1])


def _with_bundles(r, user_first):
    """The plan of a session where the user picked a hotel, bundles built by hand: the best
    bundle (another hotel) and the user's own («ваш выбор»), in the order asked."""
    plan = r.plan
    best = plan.bundles[0].model_copy(update={"label": dag.BEST_LABELS[0]})
    own = plan.bundles[0].model_copy(
        update={"label": "ваш выбор: " + _hotel(r).title, "offer_ids": plan.selected}
    )
    assert best.offer_ids != own.offer_ids
    return plan.model_copy(update={"bundles": [own, best] if user_first else [best, own]})


@pytest.mark.parametrize("user_first", [True, False])
def test_manual_pick_survives_a_replan_whatever_the_bundle_order(store, user_first):
    r, pick = _pick_third(store)
    plan = _with_bundles(r, user_first)
    assert dag._ranked_hotel(plan).title != pick.title
    spec = plan.spec.model_copy(update={"refundable": True})
    new, _, _ = dag.replan(plan, spec, r.cart)
    hotel = _hotel(r.model_copy(update={"plan": new}))
    assert hotel.title == pick.title and hotel.cancellation.refundable
    assert new.explanations[0].why == dag.MANUAL_WHY


def test_ranked_hotel_picked_is_replaced_by_the_new_ranking(store):
    """Picking the top-ranked hotel is no manual pick: after a spec edit the ranking decides."""
    r = say(store, SOCHI)
    ranked = _hotel(r)
    r = say(store, "беру", r.session_id)
    assert _hotel(r).title == ranked.title
    assert dag._ranked_hotel(r.plan).title == ranked.title
    r = say(store, "а можно всё включено", r.session_id)
    assert r.plan.selected == dag.rank(r.plan.spec, r.plan.offers)[0]
    assert r.plan.explanations[0].why != dag.MANUAL_WHY
    assert "ваш выбор" not in {c.what for c in r.plan.changes}


def test_manual_pick_stays_when_nothing_fits_the_budget(store):
    """The ranked set is over the new budget too: the pick is not blamed for it (#34 review)."""
    r, pick = _pick_third(store)
    r = say(store, "а бюджет 1000 руб", r.session_id)
    ranked_total = sum(
        o.price_rub for o in r.plan.offers if o.offer_id in dag.rank(r.plan.spec, r.plan.offers)[0]
    )
    assert ranked_total > 1000  # nothing fits
    assert _hotel(r).title == pick.title and r.plan.explanations[0].why == dag.MANUAL_WHY
    assert "ваш выбор" not in {c.what for c in r.plan.changes}
