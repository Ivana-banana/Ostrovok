"""#184: «а можно без перелёта?», «на день дольше», «без пересадок», «на 3 ночи», «отель получше»
at compare and assemble are edits: the spec or the pick changes, «Было → стало» shows it, the DAG
re-plans (no search from scratch). A condition that already holds («уже поездом») changes nothing.
«Убери трансфер» is #193.

Prices come from the plan (TRAP-4); every price line is marked as synthetic (TRAP-7).
"""

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore

KAZAN = "Казань, выходные, двое взрослых"
SOCHI = "Сочи, 3 ночи с 20 ноября, двое взрослых"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def at(store, first, stage, first_words=()):
    r = say(store, first)
    for text in first_words:
        r = say(store, text, r.session_id)
    if stage == "assemble":
        r = say(store, "беру", r.session_id)
    assert r.stage == stage
    return r


def chosen(r, kind):
    return [o for o in r.plan.offers if o.offer_id in r.plan.selected and o.kind == kind]


def modes(r):
    return [x.mode for x in chosen(r, "transport")]


STAGES = ["compare", "assemble"]


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("text", ["а можно без перелёта?", "без перелёта", "не на самолёте"])
def test_no_flight_edits_the_spec_and_shows_before_after(store, stage, text):
    r = at(store, SOCHI, stage, ["возьми самолёт"])
    assert set(modes(r)) == {"flight"}
    r2 = say(store, text, r.session_id)
    assert r2.stage == "compare" and "Это вопрос" not in r2.reply
    assert r2.plan.spec.modes == ["train", "car"] and "flight" not in modes(r2)
    assert "Было → стало:" in r2.reply and "транспорт: любой → поезд, машина" in r2.reply
    s1, s2 = r.plan.spec, r2.plan.spec
    assert (s2.destinations, s2.origin, s2.date_from) == (s1.destinations, s1.origin, s1.date_from)
    assert set(r2.plan.selected) <= {o.offer_id for o in r2.plan.offers}  # TRAP-4


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("text", ["а можно без перелёта?", "поездом", "без самолёта"])
def test_condition_already_holds_says_so_and_changes_nothing(store, stage, text):
    r = at(store, KAZAN, stage)
    assert set(modes(r)) == {"train"}
    r2 = say(store, text, r.session_id)
    assert "Уже поездом" in r2.reply
    assert r2.plan == r.plan and r2.stage == stage and r2.cart == r.cart


def test_long_flights_wish_is_no_ban_on_flights(store):
    r = at(store, SOCHI, "compare")
    r2 = say(store, "хочу без долгих перелётов", r.session_id)
    assert "flight" not in r2.plan.spec.modes and r2.plan.spec.modes == []


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize(
    "text,delta",
    [
        ("на день дольше", 1),
        ("а можно на день дольше?", 1),
        ("на 2 дня дольше", 2),
        ("еще на ночь", 1),
        ("добавь ночь", 1),
        ("на день короче", -1),
    ],
)
def test_stay_edit_is_relative_to_the_current_nights(store, stage, text, delta):
    r = at(store, SOCHI + ", 4 ночи", stage) if delta < 0 else at(store, SOCHI, stage)
    s1 = r.plan.spec
    r2 = say(store, text, r.session_id)
    s2 = r2.plan.spec
    assert r2.stage == "compare" and "Это вопрос" not in r2.reply
    assert s2.nights == s1.nights + delta and s2.date_from == s1.date_from
    assert (s2.date_to - s2.date_from).days == s2.nights
    assert "Было → стало:" in r2.reply and "даты:" in r2.reply
    assert s2.destinations == s1.destinations


def test_stay_edit_cannot_go_under_one_night(store):
    r = at(store, "Сочи, 1 ночь с 20 ноября, двое взрослых", "compare")
    r2 = say(store, "на день короче", r.session_id)
    assert "Меньше одной ночи" in r2.reply
    assert r2.plan == r.plan and r2.stage == r.stage


@pytest.mark.parametrize("stage", STAGES)
def test_absolute_length_keeps_the_start_date(store, stage):
    r = at(store, KAZAN, stage)
    r2 = say(store, "на 3 ночи", r.session_id)
    assert r2.plan.spec.nights == 3 and r2.plan.spec.date_from == r.plan.spec.date_from
    assert "Было → стало:" in r2.reply and "3 ноч." in r2.reply


def test_same_length_is_named_and_nothing_changes(store):
    r = at(store, SOCHI, "compare")
    r2 = say(store, "на 3 ночи", r.session_id)
    assert "Уже 3 ноч." in r2.reply and r2.plan == r.plan


@pytest.mark.parametrize("stage", STAGES)
def test_without_changes_says_the_legs_are_direct(store, stage):
    r = at(store, KAZAN, stage)
    r2 = say(store, "а можно без пересадок?", r.session_id)
    assert "Уже без пересадок" in r2.reply and r2.plan == r.plan and r2.stage == stage


def test_without_changes_names_the_absence_of_direct_legs(store):
    r = at(store, "Рим, 5 ночей с 20 ноября, двое взрослых", "compare")
    assert all(x.changes for x in chosen(r, "transport"))
    r2 = say(store, "без пересадок", r.session_id)
    assert "Прямых рейсов Москва → Рим" in r2.reply and r2.plan == r.plan


@pytest.mark.parametrize("stage", STAGES)
def test_without_changes_picks_a_direct_leg_of_the_plan(store, stage):
    r = at(store, SOCHI, stage, ["возьми самолёт"])
    out = next(x for x in chosen(r, "transport") if x.destination == "Сочи")
    stops = out.model_copy(update={"changes": 1, "title": out.title + ", пересадок: 1"})
    offers = [stops if o.offer_id == out.offer_id else o for o in r.plan.offers]
    bent = r.model_copy(update={"plan": r.plan.model_copy(update={"offers": offers})})
    store.put(bent)
    r2 = say(store, "без пересадок", r.session_id)
    legs = chosen(r2, "transport")
    assert legs and all(x.changes == 0 for x in legs) and out.offer_id not in r2.plan.selected
    assert "Было → стало:" in r2.reply and r2.plan.spec == r.plan.spec
    assert set(r2.plan.selected) <= {o.offer_id for o in r2.plan.offers}  # TRAP-4


def hotel_score(h):
    return (h.rating, h.stars or 0)


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("text", ["отель получше", "а можно отель получше?", "хочу получше отель"])
def test_better_hotel_steps_up_by_rating_and_says_what_it_meant(store, stage, text):
    r = at(store, KAZAN, stage)
    before = chosen(r, "hotel")[0]
    r2 = say(store, text, r.session_id)
    after = chosen(r2, "hotel")[0]
    assert r2.stage == stage and r2.plan.spec == r.plan.spec
    assert hotel_score(after) > hotel_score(before)
    assert f"рейтинг выше: {before.rating} → {after.rating}" in r2.reply
    assert "(синтетика)" in r2.reply and "Было → стало:" in r2.reply
    transfers = chosen(r2, "transfer")  # the transfers follow the new hotel
    assert len(transfers) == 2 and all(after.title in t.title for t in transfers)


def test_better_hotel_repeats_until_the_best_then_says_so(store):
    r = at(store, KAZAN, "compare")
    seen = [chosen(r, "hotel")[0].offer_id]
    for _ in range(10):
        r = say(store, "отель получше", r.session_id)
        now = chosen(r, "hotel")[0].offer_id
        if now == seen[-1]:
            break
        seen.append(now)
    assert len(seen) > 1 and "в предложениях нет" in r.reply and "Лучше «" in r.reply


def test_better_hotel_with_the_stay_edit_in_one_message(store):
    r = at(store, KAZAN, "compare")
    r2 = say(store, "на день дольше и отель получше", r.session_id)
    assert r2.plan.spec.nights == r.plan.spec.nights + 1
    assert "Отель получше" in r2.reply and "даты:" in r2.reply


def test_rebuild_at_checkout_empties_the_cart_or_keeps_it(store):
    r = at(store, KAZAN, "assemble")
    r = say(store, "да", r.session_id)
    assert r.stage == "checkout" and r.cart.items
    same = say(store, "поездом", r.session_id)
    assert "Уже поездом" in same.reply and same.cart == r.cart and same.stage == "checkout"
    better = say(store, "отель получше", r.session_id)
    assert better.cart == r.cart and "Корзина уже собрана" in better.reply
    longer = say(store, "на день дольше", r.session_id)
    assert longer.stage == "compare" and longer.cart.items == []
    assert longer.plan.spec.nights == r.plan.spec.nights + 1
