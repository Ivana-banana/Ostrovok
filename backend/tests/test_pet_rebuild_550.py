"""#550: the re-plan «с нами едет собака» leaves no hotel: «беру» says what to do, the other
cities are the nearest ones where the pet goes; «подешевле» survives a shift of the dates."""

import pytest
from talk import say

from api import catalog
from api.model import PET_DROP, PET_NO_HOTEL
from api.offers import find_offers
from api.optimizer import enumerate_trips, pets_ok
from api.store import SessionStore

KAZAN = "Казань на выходные 12–14 декабря, из Москвы, двое взрослых"
ANTALYA = "Анталия, 5 ночей с 14 ноября, двое взрослых, из Москвы"
SOCHI = "Сочи, 14–19 ноября, двое взрослых, из Москвы"
DOG = "с нами едет собака"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def chosen(r):
    return [o for o in r.plan.offers if o.offer_id in r.plan.selected]


def km_from(place: str, names: list[str]) -> list[float]:
    here = catalog.resolve(place)
    return [catalog.km_between(here, catalog.resolve(n)) for n in names]


def test_trap5_dog_on_kazan_says_beru_puts_no_plan_without_a_hotel(store):
    r = say(store, KAZAN)
    dog = say(store, DOG, r.session_id)
    assert dog.stage == "compare" and not any(o.kind == "hotel" for o in chosen(dog))
    assert PET_NO_HOTEL in dog.reply
    assert [q.options for q in dog.questions][-1] == [PET_DROP]


def test_trap5_beru_without_a_hotel_is_no_same_refusal_but_what_to_do(store):
    r = say(store, KAZAN)
    dog = say(store, DOG, r.session_id)
    again = say(store, "беру", r.session_id)
    assert again.stage == "compare" and not again.cart.items
    assert again.reply != dog.reply
    assert again.reply.startswith("В корзину пока не кладу.")
    assert "Что можно сделать" in again.reply and "без питомца" in again.reply
    assert again.questions[0].options == dog.questions[0].options
    assert say(store, "да", r.session_id).stage != "checkout"


def test_trap5_other_cities_are_the_nearest_where_the_pet_goes(store):
    r = say(store, KAZAN)
    dog = say(store, DOG, r.session_id)
    names = dog.questions[0].options
    assert len(names) == 3 and "Казань" not in names
    km = km_from("Казань", names)
    assert km == sorted(km) and max(km) < 300  # the generic list gave Нижний Новгород, 320+ km
    spec = dog.plan.spec
    for n in names:
        assert any(pets_ok(t) for t in enumerate_trips(find_offers(spec, n, estimate=True)))
    assert [d.name for d in dog.plan.destinations] == names  # «1» picks the first one


def test_trap5_antalya_no_leg_takes_the_pet_nearest_cities_and_no_beru(store):
    r = say(store, ANTALYA)
    dog = say(store, DOG, r.session_id)
    assert dog.stage == "select"  # no plan to take: no «Беру этот план» at this stage
    names = dog.questions[0].options
    assert names and "Анталия" not in names
    km = km_from("Анталия", names)
    assert km == sorted(km)
    again = say(store, "беру", r.session_id)
    assert again.stage == "select" and again.questions[0].options == names


def test_trap5_without_the_pet_brings_the_hotel_back(store):
    r = say(store, KAZAN)
    say(store, DOG, r.session_id)
    back = say(store, PET_DROP, r.session_id)
    assert not back.plan.spec.pets
    assert back.stage == "compare" and any(o.kind == "hotel" for o in chosen(back))


def test_trap5_a_nearest_city_click_plans_it(store):
    r = say(store, KAZAN)
    dog = say(store, DOG, r.session_id)
    city = dog.questions[0].options[0]
    other = say(store, city, r.session_id)
    assert other.plan.spec.destinations == [city] and other.plan.spec.pets
    assert any(o.kind == "hotel" and o.pets_allowed for o in chosen(other))


def test_trap5_date_shift_keeps_the_cheaper_wish_and_the_train(store):
    r = say(store, SOCHI)
    cheap = say(store, "подешевле", r.session_id)
    assert cheap.plan.spec.budget_rub is not None
    assert {o.mode for o in chosen(cheap) if o.kind == "transport"} == {"train"}
    moved = say(store, "перенеси на неделю позже", r.session_id)
    spec = moved.plan.spec
    assert spec.date_from != cheap.plan.spec.date_from
    assert spec.budget_rub is not None and "недорого" in spec.wishes
    assert "Бюджет «подешевле» пересчитан под новые даты" in moved.reply
    assert {o.mode for o in chosen(moved) if o.kind == "transport"} == {"train"}
    total = sum(o.price_rub for o in chosen(moved))
    assert total <= spec.budget_rub


def test_trap5_new_party_still_ends_the_agent_budget(store):
    r = say(store, SOCHI)
    say(store, "подешевле", r.session_id)
    more = say(store, "нас будет трое взрослых", r.session_id)
    assert more.plan.spec.adults == 3 and more.plan.spec.budget_rub is None
