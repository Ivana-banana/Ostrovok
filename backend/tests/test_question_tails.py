"""#181: questions without «ли» and about children keep the plan; «а с бассейном есть?» is a wish.

Each phrase runs with a stub model that adds a wish and without a model (no key): a question
changes nothing, a request re-plans.
"""

import pytest

from api import llm
from api.contracts import ChatRequest
from api.model import CHOICE_RE, chat
from api.store import SessionStore

SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых, отель до 40 тысяч, нужен завтрак"
QUESTIONS = [
    "а трансфер нужен?",
    "а дети бесплатно?",
    "надо ли доплачивать за ребёнка?",
    "а ребёнка потом можно добавить?",
    "а можно будет отменить бесплатно?",
    "что если рейс задержат?",
]
REQUESTS = ["а с бассейном есть?", "есть с бассейном?", "можно ли с бассейном?"]


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def at(store, stage):
    r = say(store, SOCHI)
    for text in ("беру", "да")[: {"assemble": 1, "checkout": 2}[stage]]:
        r = say(store, text, r.session_id)
    assert r.stage == stage
    return r


def stub_model(monkeypatch, calls):
    def stub(message, spec, until=None, today=None):
        calls.append(message)
        return {"wishes": ["бассейн"]}

    monkeypatch.setattr(llm, "extract_spec", stub)


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("stage", ["assemble", "checkout"])
@pytest.mark.parametrize("question", QUESTIONS)
def test_question_keeps_spec_stage_and_cart(monkeypatch, store, question, stage, model):
    r = at(store, stage)
    calls = []
    if model:
        stub_model(monkeypatch, calls)
    r2 = say(store, question, r.session_id)
    assert calls == []  # a question goes to no model
    assert r2.stage == stage and r2.cart == r.cart
    assert r2.plan == r.plan and r2.plan.spec == r.plan.spec
    assert r2.llm is False and r2.reply.strip()


@pytest.mark.parametrize("stage", ["assemble", "checkout"])
def test_cancel_question_answers_with_the_terms_of_the_chosen_hotel(store, stage):
    r = at(store, stage)
    r2 = say(store, "а можно будет отменить бесплатно?", r.session_id)
    hotel = next(o for o in r.plan.offers if o.offer_id in r.plan.selected and o.kind == "hotel")
    assert hotel.title in r2.reply
    assert ("бесплатная отмена" in r2.reply) == hotel.cancellation.refundable
    assert ("невозвратный" in r2.reply) == (not hotel.cancellation.refundable)
    lines = [x for x in r2.reply.splitlines() if "₽" in x]
    assert all("синтетика" in x for x in lines)  # TRAP-7


def test_delay_question_does_not_invent_a_forecast(store):
    r = at(store, "checkout")
    r2 = say(store, "что если рейс задержат?", r.session_id)
    assert "Данных о задержках рейсов нет" in r2.reply
    assert r2.plan.spec.modes == r.plan.spec.modes


def test_transfer_question_names_the_transfer_of_the_plan_or_says_none(store):
    r = at(store, "checkout")
    r2 = say(store, "а трансфер нужен?", r.session_id)
    has = any(o.kind == "transfer" and o.offer_id in r.plan.selected for o in r.plan.offers)
    assert ("Трансфер в плане:" in r2.reply) == has
    assert ("Трансфера в плане нет" in r2.reply) == (not has)


@pytest.mark.parametrize("stage", ["assemble", "checkout"])
@pytest.mark.parametrize("request_", REQUESTS)
def test_wish_after_est_reaches_the_model_and_rebuilds(monkeypatch, store, request_, stage):
    r = at(store, stage)
    calls = []
    stub_model(monkeypatch, calls)
    r2 = say(store, request_, r.session_id)
    assert calls == [request_]
    assert "бассейн" in r2.plan.spec.wishes and r2.stage == "compare" and r2.cart.items == []


@pytest.mark.parametrize("request_", REQUESTS)
def test_wish_after_est_without_a_model_does_not_say_it_is_a_question(store, request_):
    r = at(store, "checkout")
    r2 = say(store, request_, r.session_id)
    assert "Это вопрос" not in r2.reply
    assert r2.stage == "checkout" and r2.cart == r.cart  # no model: the rules know no pool


@pytest.mark.parametrize("edit", ["а подешевле есть?", "а есть что-нибудь с бассейном?"])
def test_comparatives_and_offers_stay_edits(monkeypatch, store, edit):
    r = at(store, "checkout")
    calls = []
    stub_model(monkeypatch, calls)
    r2 = say(store, edit, r.session_id)
    assert calls == [edit]  # an edit goes to the model
    assert r2.stage == "compare" and r2.cart.items == []


@pytest.mark.parametrize("stage", ["assemble", "checkout"])
def test_delay_question_does_not_switch_the_train_to_a_plane(store, stage):
    r = say(store, SOCHI)
    r = say(store, "возьми поезд", r.session_id)
    for text in ("беру", "да")[: {"assemble": 1, "checkout": 2}[stage]]:
        r = say(store, text, r.session_id)
    assert r.stage == stage
    r2 = say(store, "что если рейс задержат?", r.session_id)
    assert r2.stage == stage and r2.plan == r.plan and r2.cart == r.cart
    assert r2.plan.selected == r.plan.selected and r2.plan.spec.modes == r.plan.spec.modes


WISH_THEN_ASK = [
    "а поездом можно?",
    "а у моря нужно?",
    "а с бассейном можно?",
    "а с завтраком можно?",
    "а без пересадок можно?",
]


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("stage", ["assemble", "checkout"])
@pytest.mark.parametrize("text", WISH_THEN_ASK)
def test_wish_then_can_is_a_request(monkeypatch, store, text, stage, model):
    r = at(store, stage)
    calls = []
    if model:
        stub_model(monkeypatch, calls)
    r2 = say(store, text, r.session_id)
    assert "Это вопрос" not in r2.reply
    assert calls == ([text] if model else [])
    if model or "поезд" in text or "моря" in text:  # a wish is read: the plan is re-planned
        assert r2.cart.items == [] and r2.stage == "compare"
    else:  # no model: the rules know no wish, nothing changes
        assert r2.stage == stage and r2.cart == r.cart


def test_choice_verb_with_if_still_switches_the_transport(store):
    r = at(store, "assemble")
    r2 = say(store, "возьми поезд, если есть?", r.session_id)
    assert "Транспорт заменён" in r2.reply
    assert r2.plan.selected != r.plan.selected


@pytest.mark.parametrize("text", ["что если рейс задержат, а до берега далеко?", "а берег рядом?"])
def test_shore_is_no_choice_verb(text):
    assert CHOICE_RE.search(text) is None


@pytest.mark.parametrize("text", ["берите поезд", "бери второй", "берем поезд", "беру"])
def test_take_is_a_choice_verb(text):
    assert CHOICE_RE.search(text)
