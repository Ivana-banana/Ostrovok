"""#369: «на 1 человека» is a unit of price, not a party. Without a model (TRAP-1)."""

import pytest

from api.contracts import ChatRequest
from api.extract import extract
from api.model import chat
from api.store import SessionStore

FAMILY = "Едем с женой и сыном 6 лет в Сочи на 5 ночей с 1 июля"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


@pytest.mark.parametrize(
    "question",
    ["Сколько это на 1 человека?", "Покажи цену на 1 человека", "А на человека сколько?"],
)
def test_per_head_question_keeps_the_party_and_names_the_price(store, question):
    r = say(store, FAMILY)
    r2 = say(store, question, r.session_id)
    spec = r2.plan.spec
    assert spec.adults == 2 and spec.children_ages == [6] and r2.stage == r.stage
    total = sum(o.price_rub for o in r.plan.offers if o.offer_id in r.plan.selected)
    assert f"{round(total / 3):,}".replace(",", " ") in r2.reply.replace(" ", " ")
    assert "синтетика" in r2.reply and not r2.llm


def test_room_for_two_at_a_plan_for_four_changes_no_party(store):
    r = say(store, "Едем вчетвером в Сочи на 5 ночей с 1 июля")
    r2 = say(store, "Есть номер на 2 человека?", r.session_id)
    assert r2.plan.spec.adults == 4 and "Состав не меняю" in r2.reply


def test_budget_per_head_is_a_unit_not_the_party():
    found, notes = extract("Едем с женой, по 50 тысяч на 1 человека")
    assert found["adults"] == 2 and found["budget_rub"] == 100_000
    assert "бюджет: 50 000 ₽ × 2 взрослых" in notes


def test_a_room_for_one_in_a_first_message_is_still_a_party():
    found, _ = extract("Сочи, номер на 1 человека, 50 тысяч")
    assert found["adults"] == 1


# --- review: an edit in the same message is a rebuild; the first message keeps the party ---


@pytest.mark.parametrize(
    "question, field",
    [
        ("Покажи отели подешевле, до 20 тысяч на человека", "budget"),
        ("Покажи варианты с бюджетом 30 тысяч на человека", "budget"),
        ("Перенеси на 2 дня позже, сколько на человека выйдет?", "date"),
    ],
)
def test_an_edit_with_a_per_head_phrase_is_still_a_rebuild(store, question, field):
    r = say(store, FAMILY)
    r2 = say(store, question, r.session_id)
    assert "Состав не меняю" not in r2.reply
    if field == "budget":
        assert r2.plan.spec.budget_rub in (40_000, 60_000)
    else:
        assert r2.plan.spec.date_from > r.plan.spec.date_from


def test_a_new_party_in_the_same_message_is_applied(store):
    r = say(store, "Едем вчетвером в Сочи на 5 ночей с 1 июля")
    r2 = say(store, "Нас будет двое. Есть номер на 2 человека?", r.session_id)
    assert r2.plan.spec.adults == 2


@pytest.mark.parametrize(
    "text",
    [
        "Сколько стоит Сочи на одного человека на 5 ночей в июле?",
        "Сочи на 5 ночей, номер на 1 человека, сколько стоит?",
        "Цена поездки в Казань на 1 человека в ноябре",
        "Еду в Сочи, цена на 1 человека до 40 тысяч",
    ],
)
def test_in_the_first_message_for_one_person_is_the_party(text):
    found, _ = extract(text)
    assert found["adults"] == 1


def test_children_in_the_party_name_the_child_fare(store):
    r = say(store, FAMILY)
    r2 = say(store, "Сколько это на 1 человека?", r.session_id)
    assert "в среднем на человека" in r2.reply and "детскому тарифу" in r2.reply
