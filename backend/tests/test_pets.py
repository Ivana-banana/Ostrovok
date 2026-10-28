"""#131: with a pet the agent never leads to a booking with a hotel or a leg that refuses pets."""

import pytest
from talk import say

from api.offers import find_offers
from api.optimizer import optimize
from api.store import SessionStore

KAZAN_DOG = "Казань на выходные 12–14 декабря с собакой, из Москвы, двое взрослых"
FLIGHT_ONLY = "Анталия, 5 ночей с 14 ноября, двое взрослых, из Москвы, с собакой"
NO_CATALOG = "нет в каталоге"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def chosen(r):
    return [o for o in r.plan.offers if o.offer_id in r.plan.selected]


def hotel_rows(r):
    return [x for x in r.reply.splitlines() if x[:2] in ("1.", "2.", "3.", "4.", "5.", "6.")]


def test_kazan_with_a_dog_picks_no_hotel_and_keeps_only_legs_that_take_the_pet(store):
    r = say(store, KAZAN_DOG)
    assert r.plan.spec.pets and r.stage == "compare"
    picked = chosen(r)
    assert picked and all(o.kind == "transport" and o.pets_allowed for o in picked)
    assert not r.cart.items


def test_kazan_reply_says_the_pet_condition_is_unconfirmed_and_offers_a_way_out(store):
    r = say(store, KAZAN_DOG)
    assert "нет отметки о приёме животных" in r.reply and "не подтверждено" in r.reply
    assert "уточнив у отеля" in r.reply
    options = r.questions[0].options
    assert options and "Казань" not in options
    rows = [x for x in r.reply.splitlines() if x.startswith("Нет отметки о животных")]
    assert rows == ["Нет отметки о животных: у 5 из 5 отелей."]


def test_kazan_beru_is_no_consent_and_fills_no_cart(store):
    r = say(store, KAZAN_DOG)
    again = say(store, "беру", r.session_id)
    assert again.stage == "compare" and not again.cart.items
    assert not any(o.kind == "hotel" for o in chosen(again))
    assert "нет отметки о приёме животных" in again.reply
    assert say(store, "да", r.session_id).stage != "checkout"


def test_a_click_on_an_offered_city_goes_on_without_the_catalogue_phrase(store):
    r = say(store, KAZAN_DOG)
    other = say(store, r.questions[0].options[0], r.session_id)
    assert NO_CATALOG not in other.reply
    assert other.stage == "compare" and other.plan.spec.destinations != ["Казань"]
    picked = [o for o in chosen(other) if o.kind in ("hotel", "transport")]
    assert any(o.kind == "hotel" for o in picked) and all(o.pets_allowed for o in picked)


def test_the_hotel_number_is_the_explicit_consent(store):
    r = say(store, KAZAN_DOG)
    picked = say(store, "4", r.session_id)
    assert picked.stage == "assemble"
    assert any(o.kind == "hotel" and not o.pets_allowed for o in chosen(picked))
    assert say(store, "да", r.session_id).stage == "checkout"


def test_without_a_pet_the_city_keeps_its_five_hotels_and_a_choice(store):
    r = say(store, "Казань на выходные 12–14 декабря, из Москвы, двое взрослых")
    hotels = {o.title for o in r.plan.offers if o.kind == "hotel"}
    assert len(hotels) == 5 and any(o.kind == "hotel" for o in chosen(r))
    assert "животн" not in r.reply


def test_optimizer_selects_nothing_when_no_leg_takes_the_pet(store):
    r = say(store, FLIGHT_ONLY)
    assert optimize(r.plan.spec, find_offers(r.plan.spec, "Анталия")).selected == []


def test_flight_only_dead_end_says_so_and_never_fills_the_cart(store):
    r = say(store, FLIGHT_ONLY)
    assert r.stage == "select" and not r.plan.selected and not r.cart.items
    assert "животн" in r.reply and "Анталия" in r.reply
    options = r.questions[0].options
    assert options and "Калининград" not in options  # no train there: the dog has no trip
    again = say(store, "да", r.session_id)
    assert again.stage != "checkout" and not again.cart.items
    final = say(store, "подтверждаю", again.session_id)
    assert final.voucher is None and final.stage != "booked"


def test_a_click_after_the_flight_only_dead_end_has_no_catalogue_phrase(store):
    r = say(store, FLIGHT_ONLY)
    other = say(store, r.questions[0].options[0], r.session_id)
    assert NO_CATALOG not in other.reply and other.stage == "compare"
    assert all(o.pets_allowed for o in chosen(other) if o.kind in ("hotel", "transport"))


# --- #136: the tails of the #131 review ---


@pytest.mark.parametrize("word", ["с котом", "с кошкой", "с псом", "с собакой"])
def test_pet_word_forms_set_the_pet_flag(store, word):
    r = say(store, f"Сочи {word} на выходные 12–14 декабря, из Москвы, двое взрослых")
    assert r.plan.spec.pets


@pytest.mark.parametrize("pick", ["5", "пятый", "беру пятый"])
def test_the_fifth_hotel_is_picked_by_number(store, pick):
    r = say(store, "Казань на выходные 12–14 декабря, из Москвы, двое взрослых")
    picked = say(store, pick, r.session_id)
    assert picked.stage == "assemble"
    assert any(o.kind == "hotel" and o.title.startswith("Казантель") for o in chosen(picked))


def test_no_second_dead_end_after_the_select_dead_end(store):
    r = say(store, "Тбилиси, 5 ночей с 14 ноября, двое взрослых, из Москвы, с собакой")
    assert r.stage == "select"
    again = say(store, "беру", r.session_id)
    assert again.questions and "Казань" not in again.questions[0].options
    assert "Казань" not in again.reply


def test_the_dead_end_reply_shows_what_changed(store):
    r = say(store, "Казань на выходные 12–14 декабря, из Москвы, двое взрослых")
    dog = say(store, "с нами едет собака", r.session_id)
    assert dog.reply.startswith("Было → стало:")
    assert "нет отметки о приёме животных" in dog.reply


def test_the_dead_end_text_agrees_in_case(store):
    r = say(store, KAZAN_DOG)
    assert "в «Казань»" not in r.reply and "в данных 5 отелей" not in r.reply
    assert "Ни у одного из 5 отелей" in r.reply


def test_simulator_dead_end_rule_ignores_the_catalogue_phrase(store):
    from api.checks import _is_pets_dead_end

    dead = say(store, KAZAN_DOG)
    assert _is_pets_dead_end(dead)
    unknown = dead.model_copy(update={"reply": "Направления «Х» пока нет в каталоге."})
    assert not _is_pets_dead_end(unknown)


# --- #149: more pet words; the simulator does not match the reply text ---


@pytest.mark.parametrize(
    "word",
    [
        "со щенком",
        "со щенками",
        "щенки",
        "щенята",
        "с щенятами",
        "с котёнком",
        "с котенком",
        "с котятами",
        "котята",
        "с псами",
        "два пса",
        "псам",
    ],
)
def test_young_and_plural_pet_words_set_the_pet_flag(store, word):
    r = say(store, f"Сочи {word} на выходные 12–14 декабря, из Москвы, двое взрослых")
    assert r.plan.spec.pets


@pytest.mark.parametrize(
    "word",
    [
        "котел",
        "пасе",
        "псевдо",
        "псих",
        "скот",
        "котлета",
        "Псков",
        "Котельнич",
        "Щенково",
        "щенячий",
        "Котенко",
    ],
)
def test_look_alike_words_do_not_set_the_pet_flag(store, word):
    r = say(store, f"Сочи, {word}, на выходные 12–14 декабря, из Москвы, двое взрослых")
    assert not r.plan.spec.pets


def test_off_catalogue_reply_is_no_pets_dead_end(store):
    from api.checks import _is_pets_dead_end
    from api.model import NOT_IN_CATALOG

    r = say(store, "Пномпень, 5 ночей с 14 ноября, двое взрослых, из Москвы, с собакой")
    assert NOT_IN_CATALOG in r.reply
    assert not _is_pets_dead_end(r)
