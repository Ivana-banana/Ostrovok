"""Exits from any dialog stage (#90, S5/S6): «тогда <город>», «начать заново», «другое направление».

All run on rules, without a model (TRAP-1).
"""

import pytest

from api import catalog
from api.contracts import ChatRequest
from api.extract import extract
from api.model import chat
from api.store import SessionStore


def in_catalog(name: str) -> bool:
    """A place of catalog.json under its own name (#20: inspiration ranks the whole catalogue)."""
    place = catalog.resolve(name)
    return place is not None and place["name"] == name


SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def to_stage(store, stage):
    r = say(store, SOCHI)
    for text, want in (("беру", "assemble"), ("да", "checkout"), ("подтверждаю", "booked")):
        if r.stage == stage:
            break
        r = say(store, text, r.session_id)
        assert r.stage == want
    assert r.stage == stage
    return r


@pytest.mark.parametrize(
    ("text", "city"),
    [
        ("тогда Барселона", "Барселона"),
        ("Ну тогда в Барселону", "Барселона"),
        ("лучше Анталия", "Анталия"),
        ("ладно, Казань", "Казань"),
        ("давайте Санкт-Петербург", "Санкт-Петербург"),
    ],
)
def test_switch_lead_names_the_city(text, city):
    assert extract(text, True)[0]["destinations"] == [city]


@pytest.mark.parametrize(
    "text",
    [
        "тогда да",
        "тогда беру",
        "Тогда Подтверждаю",
        "лучше поезд",
        "Лучше Завтра",
        "Лучше Вечером",
        "А если Дети",
        "Тогда Сначала",
    ],
)
def test_switch_lead_without_a_city_names_none(text):
    assert "destinations" not in extract(text, True)[0]


@pytest.mark.parametrize("stage", ["compare", "assemble", "checkout"])
def test_then_city_changes_destination_on_any_stage(store, stage):
    r = to_stage(store, stage)
    r = say(store, "тогда Барселона", r.session_id)
    assert r.plan.spec.destinations == ["Барселона"]
    assert r.stage == "compare" and r.plan.offers
    assert all(o.kind != "hotel" or o.city == "Барселона" for o in r.plan.offers)


def test_then_city_on_select_stage_finds_offers(store):
    r = say(store, "Хиврии, 3 ночи с 20 ноября")
    assert r.stage == "select"
    r = say(store, "тогда Барселона", r.session_id)
    assert r.plan.spec.destinations == ["Барселона"] and r.stage == "compare"


@pytest.mark.parametrize("stage", ["compare", "assemble", "checkout"])
def test_other_destination_clears_the_city_and_offers_variants(store, stage):
    r = to_stage(store, stage)
    r = say(store, "Выберите другое направление", r.session_id)
    assert r.plan.spec.destinations == [] and r.stage == "inspire"
    assert r.cart.items == [] and r.voucher is None
    names = [d.name for d in r.plan.destinations]
    assert 2 <= len(names) <= 3 and "Сочи" not in names
    assert all(in_catalog(n) for n in names)  # from catalog.json, never invented
    assert r.questions[0].options == names
    picked = say(store, "1", r.session_id)  # a variant by its number leads on
    assert picked.plan.spec.destinations == [names[0]] and picked.stage == "compare"


def test_unknown_city_answer_has_two_or_three_variants(store):
    r = say(store, "Хиврии, 3 ночи с 20 ноября")
    assert r.stage == "select" and "другое направление" in r.reply
    assert r.questions[0].options == []  # #346: the country is asked, no unrelated cities
    # A message with no new city and no country gets the question again.
    again = say(store, "не знаю", r.session_id)
    assert again.stage == "select" and again.questions[0].options == []
    r = say(store, "Хорватия", r.session_id)
    options = r.questions[0].options
    assert 2 <= len(options) <= 3 and all(in_catalog(o) for o in options)
    assert all(o in r.reply for o in options)
    # A variant as a button text and as a number both lead on.
    assert say(store, options[1], r.session_id).plan.spec.destinations == [options[1]]
    assert say(store, "2", r.session_id).plan.spec.destinations == [options[1]]


@pytest.mark.parametrize("stage", ["compare", "assemble", "checkout", "booked"])
def test_restart_drops_the_plan_on_any_stage(store, stage):
    r = to_stage(store, stage)
    r = say(store, "начать заново", r.session_id)
    assert r.stage == "clarify" and len(r.questions) == 2
    assert r.plan.spec.destinations == [] and r.plan.offers == []
    assert r.cart.items == [] and r.voucher is None
    assert say(store, "Сочи", r.session_id).stage == "compare"  # the session works again


def test_restart_with_a_new_request_plans_it(store):
    r = to_stage(store, "assemble")
    r = say(store, "Начнём сначала: Анталия, 4 ночи", r.session_id)
    assert r.stage == "compare" and r.plan.spec.destinations == ["Анталия"]
    assert r.plan.spec.nights == 4 and r.cart.items == []


@pytest.mark.parametrize(
    "text",
    [
        "не надо начинать заново",
        "хочу сначала отель получше",
        "сбрось цену",
        "сбрось бюджет",
        "сбрось фильтр по цене",
        "с нуля не надо",
        "не хочу с нуля",
        "новую поездку не надо",
        "собери с нуля корзину",
    ],
)
def test_restart_words_in_other_sense_change_nothing(store, text):
    r = to_stage(store, "checkout")
    again = say(store, text, r.session_id)
    assert again.plan.spec.destinations == r.plan.spec.destinations
    assert again.cart.items == r.cart.items and again.cart.items and again.stage != "clarify"


def test_started_over_in_a_word_is_a_replan_not_a_reset(store):
    r = to_stage(store, "assemble")
    again = say(store, "поехали сначала в Казань", r.session_id)
    assert again.stage == "compare" and again.plan.spec.destinations == ["Казань"]
    assert "Начинаем заново" not in again.reply


@pytest.mark.parametrize("text", ["сбрось", "Сбрось всё.", "начни с нуля", "давай заново"])
def test_restart_phrases(store, text):
    r = to_stage(store, "assemble")
    assert say(store, text, r.session_id).stage == "clarify"


def test_restart_after_booking_names_the_voucher(store):
    r = to_stage(store, "booked")
    again = say(store, "начать заново", r.session_id)
    assert f"Бронь {r.voucher.booking_id} остаётся оформленной" in again.reply


def test_other_variants_on_inspire_are_new(store):
    first = say(store, "куда-нибудь в ноябре, недорого, двое взрослых")
    assert first.stage == "inspire"
    shown = [d.name for d in first.plan.destinations]
    more = say(store, "покажи другие варианты", first.session_id)
    names = [d.name for d in more.plan.destinations]
    assert more.stage == "inspire" and 2 <= len(names) <= 3 and not set(names) & set(shown)
