"""Re-plan DAG (T6, #10): a spec edit recomputes only the dependent nodes."""

from datetime import date, timedelta

import pytest

from api import dag
from api.contracts import Cart, ChatRequest, Plan, TripSpec
from api.model import chat
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
    assert plan.offers == booked.plan.offers  # transport and hotels are kept
    hotel = next(o for o in plan.offers if o.offer_id in plan.selected and o.kind == "hotel")
    assert hotel.meal == "AI"
    assert any(c.what == "питание" for c in plan.changes)


def test_same_selection_keeps_the_timeline(booked, calls):
    spec = booked.plan.spec.model_copy(update={"refundable": True})
    plan, cart, done = dag.replan(booked.plan, spec, booked.cart)
    assert done == ["ranking", "cart"] and cart.items == []
    assert calls == ["rank"]
    assert plan.selected == booked.plan.selected
    assert plan.timeline == booked.plan.timeline and plan.issues == booked.plan.issues


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
    assert done == ["destination", "offers", "ranking"]
    assert calls == ["find_offers", "rank"]
    assert plan.offers and plan.selected and plan.changes == [] and cart.items == []


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
    """Session where the user picked the first hotel over the ranked one."""
    r = say(store, SOCHI)
    ranked = r.plan.selected[0]
    r = say(store, "первый", r.session_id)
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
    r = say(store, "Сочи, 3 ночи с 20 ноября, двое взрослых, бюджет 58 000 руб")
    r = say(store, "беру третий", r.session_id)
    assert r.stage == "assemble" and "Семейный" in _hotel(r).title
    r = say(store, "а бюджет 55000 руб", r.session_id)
    assert "Комфорт" in _hotel(r).title
    lost = next(c for c in r.plan.changes if c.what == "ваш выбор")
    assert "Семейный" in lost.before and lost.after.endswith("дороже бюджета")
    assert "дороже бюджета" in r.reply
