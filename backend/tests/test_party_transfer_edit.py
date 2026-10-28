"""#193: a party and a transfer said as a remark are edits, not a question and not a consent.

«а дети с нами — двое, 5 и 8 лет» names the children: the party changes, the plan is rebuilt.
«а давайте без трансфера?» has a consent word and an edit: the edit wins, nothing goes to the
cart. Each phrase runs with a stub model and without a model (no key, TRAP-1).
"""

import pytest
from talk import say

from api import llm
from api.store import SessionStore

SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых, отель до 40 тысяч, нужен завтрак"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


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


def transfers(r):
    return [o for o in r.plan.offers if o.kind == "transfer" and o.offer_id in r.plan.selected]


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("stage", ["assemble", "checkout"])
@pytest.mark.parametrize(
    "text",
    [
        "а дети с нами — двое, 5 и 8 лет",
        "А дети с нами: двое, 5 и 8 лет",
        "а дети с нами — 5 и 8 лет",
    ],
)
def test_children_with_ages_are_a_party_edit(monkeypatch, store, text, stage, model):
    r = at(store, stage)
    if model:
        stub_model(monkeypatch, [])
    r2 = say(store, text, r.session_id)
    assert "Состав не меняю" not in r2.reply and "Это вопрос" not in r2.reply
    assert r2.plan.spec.adults == 2 and r2.plan.spec.children_ages == [5, 8]
    assert r2.stage == "compare" and r2.cart.items == []
    assert "Было → стало" in r2.reply
    assert all(o.children_ages == [5, 8] for o in r2.plan.offers if o.kind == "hotel")


KIDS_QUESTIONS = [
    "а дети бесплатно?",
    "надо ли доплачивать за ребёнка?",
    "а ребёнка потом можно добавить?",
    "а детям до 12 лет скидка?",
]


@pytest.mark.parametrize("stage", ["assemble", "checkout"])
@pytest.mark.parametrize("text", KIDS_QUESTIONS)
def test_kids_question_stays_a_question(store, text, stage):
    r = at(store, stage)
    r2 = say(store, text, r.session_id)
    assert r2.plan.spec == r.plan.spec and r2.cart == r.cart and r2.stage == stage


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize(
    "text", ["а давайте без трансфера?", "давайте без трансфера", "без трансфера"]
)
def test_without_transfer_at_assemble_drops_it_and_keeps_the_cart_empty(
    monkeypatch, store, text, model
):
    r = at(store, "assemble")
    assert transfers(r)
    calls = []
    if model:
        stub_model(monkeypatch, calls)
    r2 = say(store, text, r.session_id)
    assert r2.stage == "assemble" and r2.cart.items == []
    assert transfers(r2) == []
    assert [o for o in r2.plan.selected if o not in {t.offer_id for t in transfers(r)}] == [
        o for o in r.plan.selected if o not in {t.offer_id for t in transfers(r)}
    ]
    assert "трансфер" in r2.reply.lower()
    assert calls == []  # the rules read it, the model does not
    assert not any(e.title.lower().startswith("трансфер") for e in r2.plan.timeline)
    # a plain consent after the edit fills the cart without the transfer
    r3 = say(store, "да", r2.session_id)
    assert r3.stage == "checkout" and r3.cart.items
    assert all(o.kind != "transfer" for o in r3.cart.items)
    assert r3.cart.total_rub == sum(o.price_rub for o in r3.cart.items)
    assert r3.cart.total_rub == sum(
        o.price_rub for o in r.plan.offers if o.offer_id in r3.plan.selected
    )
    assert r3.cart.total_rub < sum(
        o.price_rub for o in r.plan.offers if o.offer_id in r.plan.selected
    )


def test_without_transfer_at_checkout_takes_it_out_of_the_cart(store):
    r = at(store, "checkout")
    assert any(o.kind == "transfer" for o in r.cart.items)
    r2 = say(store, "давайте без трансфера", r.session_id)
    assert r2.stage == "checkout" and all(o.kind != "transfer" for o in r2.cart.items)
    assert r2.cart.total_rub == sum(o.price_rub for o in r2.cart.items) < r.cart.total_rub
    assert transfers(r2) == []


def test_without_transfer_twice_says_there_is_none(store):
    r = at(store, "assemble")
    r2 = say(store, "давайте без трансфера", r.session_id)
    r3 = say(store, "давайте без трансфера", r2.session_id)
    assert r3.plan == r2.plan and r3.stage == "assemble" and r3.cart.items == []
    assert "нет" in r3.reply


def test_consent_word_with_a_meal_edit_is_an_edit(store):
    r = at(store, "assemble")
    r2 = say(store, "давайте без завтрака", r.session_id)
    assert r2.cart.items == [] and r2.stage == "compare" and r2.plan.spec.meal == "RO"


def test_consent_word_with_a_mode_is_a_switch_not_a_consent(store):
    r = at(store, "assemble")
    r2 = say(store, "давай поездом", r.session_id)
    assert r2.cart.items == [] and r2.stage == "assemble" and "Транспорт заменён" in r2.reply
    legs = [o for o in r2.plan.offers if o.kind == "transport" and o.offer_id in r2.plan.selected]
    assert legs and all(o.mode == "train" for o in legs)


def test_train_and_without_transfer_in_one_message_do_both(store):
    r = at(store, "assemble")
    r2 = say(store, "давай поездом, без трансфера", r.session_id)
    assert r2.cart.items == [] and r2.stage == "assemble" and transfers(r2) == []
    legs = [o for o in r2.plan.offers if o.kind == "transport" and o.offer_id in r2.plan.selected]
    assert legs and all(o.mode == "train" for o in legs)


def test_transfer_question_is_still_a_question(store):
    r = at(store, "assemble")
    r2 = say(store, "а трансфер нужен?", r.session_id)
    assert r2.plan == r.plan and r2.stage == "assemble" and "Это вопрос" in r2.reply


ASKS_ABOUT_TRANSFER = [
    "а без трансфера как добраться?",
    "сколько стоит без трансфера?",
    "без трансфера дороже?",
    "трансфер не нужен?",
    "нам трансфер не нужен?",
]


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("stage", ["assemble", "checkout"])
@pytest.mark.parametrize("text", ASKS_ABOUT_TRANSFER)
def test_question_about_no_transfer_is_a_question(monkeypatch, store, text, stage, model):
    r = at(store, stage)
    calls = []
    if model:
        stub_model(monkeypatch, calls)
    r2 = say(store, text, r.session_id)
    assert "Это вопрос" in r2.reply and calls == []
    assert r2.plan == r.plan and r2.cart == r.cart and r2.stage == stage


@pytest.mark.parametrize("stage", ["assemble", "checkout"])
@pytest.mark.parametrize(
    "text",
    [
        "можно без трансфера?",
        "не нужен трансфер",
        "нам трансфер не нужен",
        "трансфер не нужен",
        "трансфер нам не нужен, давайте дальше",
    ],
)
def test_no_transfer_said_as_a_request_is_an_edit(store, text, stage):
    r = at(store, stage)
    r2 = say(store, text, r.session_id)
    assert r2.stage == stage and transfers(r2) == []
    assert all(o.kind != "transfer" for o in r2.cart.items)
    assert (r2.cart.items == []) == (stage == "assemble")


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("stage", ["assemble", "checkout"])
@pytest.mark.parametrize(
    "text", ["без трансфера дешевле?", "без трансфера выгоднее?", "без трансфера дороже?"]
)
def test_comparison_about_no_transfer_is_a_question(monkeypatch, store, text, stage, model):
    r = at(store, stage)
    if model:
        stub_model(monkeypatch, [])
    r2 = say(store, text, r.session_id)
    assert "Это вопрос" in r2.reply
    assert r2.plan == r.plan and r2.cart == r.cart and r2.stage == stage


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("stage", ["assemble", "checkout"])
@pytest.mark.parametrize(
    "text", ["убери трансфер, так как доберёмся сами", "без трансфера, доберёмся как-нибудь сами"]
)
def test_as_and_somehow_are_no_question(monkeypatch, store, text, stage, model):
    r = at(store, stage)
    if model:
        stub_model(monkeypatch, [])
    r2 = say(store, text, r.session_id)
    assert "Это вопрос" not in r2.reply
    assert r2.stage == stage and transfers(r2) == []
    assert all(o.kind != "transfer" for o in r2.cart.items)


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("stage", ["assemble", "checkout"])
def test_question_with_dates_keeps_the_date_edit(monkeypatch, store, stage, model):
    r = at(store, stage)
    if model:
        stub_model(monkeypatch, [])
    r2 = say(store, "без трансфера и на неделю позже, сколько выйдет?", r.session_id)
    assert r2.plan.spec.date_from > r.plan.spec.date_from
    assert "Это вопрос" not in r2.reply and r2.cart.items == []


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("stage", ["assemble", "checkout"])
def test_question_with_a_city_keeps_the_destination_edit(monkeypatch, store, stage, model):
    r = at(store, stage)
    if model:
        stub_model(monkeypatch, [])
    r2 = say(store, "без трансфера в Анапу, сколько будет?", r.session_id)
    assert r2.plan.spec.destinations == ["Анапа"]
    assert "Это вопрос" not in r2.reply and r2.cart.items == []


@pytest.mark.parametrize("model", [False, True])
def test_question_with_a_mode_switches_at_assemble(monkeypatch, store, model):
    r = at(store, "assemble")
    if model:
        stub_model(monkeypatch, [])
    r2 = say(store, "давай поездом и без трансфера, сколько выйдет?", r.session_id)
    legs = [o for o in r2.plan.offers if o.kind == "transport" and o.offer_id in r2.plan.selected]
    assert legs and all(o.mode == "train" for o in legs)
    assert "Это вопрос" not in r2.reply and r2.cart.items == []


@pytest.mark.parametrize("model", [False, True])
def test_question_with_a_mode_at_checkout_keeps_the_cart(monkeypatch, store, model):
    r = at(store, "checkout")
    if model:
        stub_model(monkeypatch, [])
    r2 = say(store, "давай поездом и без трансфера, сколько выйдет?", r.session_id)
    assert "Корзина уже собрана" in r2.reply
    assert r2.cart == r.cart and r2.stage == "checkout"
