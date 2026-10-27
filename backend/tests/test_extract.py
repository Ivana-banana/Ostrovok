"""Rule extractor and clarification policy (T3, #7): one case per rule, phrases from eval/cases.json."""

import itertools
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from api.contracts import ChatRequest
from api.extract import extract, is_chip_message
from api.model import chat, step
from api.store import SessionStore

S3A = "Сочи, 3 ночи с 20 ноября, двое взрослых, отель до 40 тысяч за всё, нужен завтрак."
S3B = "хочу на море в ноябре"


def md(d: date) -> tuple[int, int]:
    return d.month, d.day


def test_s3a_exact_request_parses_every_field():
    found, notes = extract(S3A)
    assert found["destinations"] == ["Сочи"] and md(found["date_from"]) == (11, 20)
    assert found["nights"] == 3 and found["adults"] == 2
    assert found["hotel_budget_rub"] == 40_000 and found["meal"] == "BB"
    assert "budget_rub" not in found  # S3-A: «отель до 40 тысяч» is not a trip budget (#41)
    assert notes == ["бюджет отеля: 40 000 ₽ за все ночи"]


@pytest.mark.parametrize(
    "text,hotel,trip",
    [
        ("отель до 40 тысяч", 40_000, None),
        ("на отель 40к", 40_000, None),
        ("40к на отель", 40_000, None),
        ("отель не дороже 40 000 руб", 40_000, None),
        ("гостиница до 40 тыс", 40_000, None),
        ("до 100 тысяч на всех", None, 100_000),
        ("бюджет 80 тыс", None, 80_000),
        ("отель 4 звезды, до 100к", None, 100_000),  # a comma ends the hotel clause
        ("отель и перелёт до 100к", None, 100_000),
        ("отель + перелёт до 100к", None, 100_000),
        ("перелёт и отель до 100 тысяч", None, 100_000),  # the hotel is one item of the trip
        ("авиа + отель до 30 тысяч", None, 30_000),
        ("билеты и отель до 50к", None, 50_000),
        ("с перелётом и отелем до 90 тыс", None, 90_000),
        ("поездка с отелем до 100к", None, 100_000),
        ("отель 4 звезды до 100к", 100_000, None),  # no comma: the sum is about the hotel
        ("отель с завтраком до 40к", 40_000, None),
        ("отель до 40 тысяч, всего до 100 тысяч", 40_000, 100_000),
        ("всего до 100 тысяч, из них отель до 40к", 40_000, 100_000),
        ("на отель 40к и всего 150 000 руб", 40_000, 150_000),
    ],
)
def test_hotel_and_trip_budgets_are_told_apart(text, hotel, trip):
    found, notes = extract(text)
    assert found.get("hotel_budget_rub") == hotel and found.get("budget_rub") == trip
    assert any(n.startswith("бюджет отеля:") for n in notes) == (hotel is not None)
    assert not any("на всю поездку" in n for n in notes)


def test_hotel_price_per_night_is_multiplied_by_the_nights_when_they_are_named():
    found, notes = extract("5 ночей, отель до 10к за ночь")
    assert found["hotel_budget_rub"] == 50_000 and "budget_rub" not in found
    assert notes == ["бюджет отеля: 10 000 ₽ за ночь, 50 000 ₽ за 5 ноч."]


def test_hotel_price_per_night_without_nights_makes_no_total():
    """The nights are unknown here: no invented total, only the assumption (model resolves it)."""
    found, notes = extract("отель до 8000 руб в сутки")
    assert "hotel_budget_rub" not in found and "budget_rub" not in found
    assert notes == ["бюджет отеля: 8 000 ₽ за ночь"]


def test_s6_day_range_gives_dates_and_nights():
    found, notes = extract(
        "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё."
    )
    assert md(found["date_from"]) == (12, 11) and md(found["date_to"]) == (12, 13)
    assert found["nights"] == 2 and found["origin"] == "Москва" and notes == []


def test_s6_chat_has_no_false_date_assumption(tmp_path):
    r = chat(
        ChatRequest(message="Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч"),
        SessionStore(tmp_path / "s.db"),
    )
    spec = r.plan.spec
    assert (md(spec.date_from), md(spec.date_to), spec.nights) == ((12, 11), (12, 13), 2)
    assert not any(a.startswith("даты:") for a in spec.assumptions)


def test_city_at_message_start_is_kept():
    found, _ = extract("Тбилиси в декабре, вино и еда")
    assert found["destinations"] == ["Тбилиси"] and found["month"] == 12
    assert "вино и еда" in found["wishes"]


@pytest.mark.parametrize(
    "text,budget",
    [
        ("нас 2 к морю", None),
        ("прилёт 10 к 14:00, бюджет 80 тыс", 80_000),
        ("150к на всё", 150_000),
    ],
)
def test_k_after_a_space_is_a_preposition_not_thousands(text, budget):
    assert extract(text)[0].get("budget_rub") == budget


def test_s2_shift_in_days_is_not_a_duration():
    found, _ = extract("А если на два дня позже? И бюджет тысяч 150 максимум на всё.")
    assert found["budget_rub"] == 150_000 and "nights" not in found


def test_two_destinations_and_days_to_nights():
    found, notes = extract("Казань или Нижний на 3 дня с бабушкой, ей тяжело ходить")
    assert found["destinations"] == ["Казань", "Нижний Новгород"]
    assert found["nights"] == 2 and any(n.startswith("длительность:") for n in notes)
    assert "тяжело ходить" in found["wishes"]


def test_unknown_city_after_preposition_is_kept_as_written():
    found, _ = extract("дёшево в Стамбул на майские")
    assert found["destinations"] == ["Стамбул"] and found["month"] == 5
    assert "недорого" in found["wishes"]


def test_weekend_is_friday_to_sunday_with_an_assumption():
    found, notes = extract("на выходные из Москвы в Питер на поезде, вдвоём, тысяч 30")
    assert found["origin"] == "Москва" and found["destinations"] == ["Санкт-Петербург"]
    assert found["date_from"].weekday() == 4 and found["nights"] == 2
    assert found["date_to"] - found["date_from"] == timedelta(days=2)
    assert found["modes"] == ["train"] and found["adults"] == 2 and found["budget_rub"] == 30_000
    assert any(n.startswith("даты:") for n in notes)


def test_group_size_and_season():
    found, _ = extract("хочу в горы зимой, кататься на лыжах, бюджет средний, нас четверо")
    assert found["adults"] == 4 and found["month"] in (12, 1, 2)
    assert {"горы", "лыжи"} <= set(found["wishes"]) and "budget_rub" not in found


def test_group_size_counts_children_out():
    found, _ = extract("нас четверо, двое детей 3 и 7 лет")
    assert found["adults"] == 2 and found["children_ages"] == [3, 7]


def test_s1_child_age_short_flights_and_origin():
    found, _ = extract(
        "Хочу куда-нибудь к тёплому морю в ноябре, недорого, с ребёнком 5 лет, "
        "без долгих перелётов. Летим из Москвы."
    )
    assert found["children_ages"] == [5] and found["max_travel_hours"] == 5.0
    assert found["origin"] == "Москва" and found["month"] == 11
    assert {"море", "недорого"} <= set(found["wishes"]) and "destinations" not in found


def test_adult_age_is_not_a_child():
    found, _ = extract("Кисловодск, санаторий, мама 70 лет, 10 дней")
    assert found["destinations"] == ["Кисловодск"] and "children_ages" not in found
    assert found["nights"] == 9 and "санаторий" in found["wishes"]


def test_tomorrow_is_a_date():
    found, _ = extract("срочно отель в Екатеринбурге на завтра, командировка")
    assert found["destinations"] == ["Екатеринбург"]
    assert found["date_from"] == date.today() + timedelta(days=1)


def test_near_origin_limits_travel_time():
    found, notes = extract("романтические выходные недалеко от Москвы")
    assert found["origin"] == "Москва" and "destinations" not in found
    assert found["max_travel_hours"] == 3.0 and any(n.startswith("время в пути:") for n in notes)


def test_car_week_with_a_dog():
    found, _ = extract("едем с собакой на машине к морю на неделю")
    assert found["pets"] and found["modes"] == ["car"] and found["nights"] == 7
    assert "море" in found["wishes"]


def test_s8_pet_message_changes_only_pets():
    found, notes = extract("С нами едет собака, 12 кг.")
    assert found == {"pets": True} and notes == []


def test_all_inclusive_and_free_cancellation():
    found, _ = extract("Анталия, всё включено, нужна бесплатная отмена")
    assert found["meal"] == "AI" and found["refundable"] is True


def test_s9_priority_is_not_a_filter():
    found, _ = extract("Мне важнее бесплатная отмена, чем цена.")
    assert found["priorities"][0] == "cancellation" and "refundable" not in found


def test_invalid_range_end_is_skipped_with_an_assumption():
    found, notes = extract("Казань, 13–11 декабря")
    assert md(found["date_from"]) == (12, 13) and "date_to" not in found
    assert any("13–11 декабря" in n for n in notes)


def test_s3b_vague_request_goes_ahead_and_names_assumptions(tmp_path):
    r = chat(ChatRequest(message=S3B), SessionStore(tmp_path / "s.db"))
    assert r.stage == "inspire" and r.questions == []
    assert r.plan.spec.assumptions and "Допущения:" in r.reply


def test_s3a_chat_asks_nothing_and_compares(tmp_path):
    r = chat(ChatRequest(message=S3A), SessionStore(tmp_path / "s.db"))
    assert r.stage == "compare" and r.questions == []
    assert not any(a.startswith(("даты:", "длительность:")) for a in r.plan.spec.assumptions)
    spec = r.plan.spec
    assert spec.hotel_budget_rub == 40_000 and spec.budget_rub is None
    assert "бюджет отеля: 40 000 ₽ за все ночи" in spec.assumptions
    assert "бюджет отеля: 40 000 ₽ за все ночи" in r.reply  # the user sees the assumption
    assert "на всю поездку" not in r.reply


def test_s3a_plan_has_a_hotel_within_40000_and_reaches_the_booking(tmp_path):
    """S3-A: the hotel is picked within its own budget, the trip total is not limited by it."""
    store = SessionStore(tmp_path / "s.db")
    r = chat(ChatRequest(message=S3A), store)
    hotels = [o for o in r.plan.offers if o.kind == "hotel" and o.offer_id in r.plan.selected]
    assert len(hotels) == 1 and hotels[0].price_rub <= 40_000 and hotels[0].meal != "RO"
    assert not any("бюджет" in x for e in r.plan.explanations for x in e.risks)
    for text in ("беру", "да", "подтверждаю"):
        r = chat(ChatRequest(session_id=r.session_id, message=text), store)
    assert r.stage == "booked" and r.voucher is not None


def test_march_is_not_may():
    found, _ = extract("Сочи с 10 по 15 марта, двое")
    assert md(found["date_from"]) == (3, 10) and md(found["date_to"]) == (3, 15)


def test_lower_floor_is_not_nizhny_novgorod():
    found, _ = extract("номер на нижнем этаже, маме тяжело ходить, Сочи")
    assert found["destinations"] == ["Сочи"]


def test_trip_word_is_not_a_train():
    assert "modes" not in extract("Поездка в Сочи в ноябре")[0]


def test_negated_mode_is_dropped():
    assert extract("не хочу лететь, только поезд")[0]["modes"] == ["train"]


def test_centre_is_location_not_price():
    found, _ = extract("Отель в центре Анталии, важно чтобы был завтрак")
    assert found["priorities"] == ["location", "meal"]


def test_no_priority_named_keeps_old_priorities():
    assert "priorities" not in extract("не важно")[0]


@pytest.mark.parametrize(
    "text,days",
    [("Казань через 3 дня, двое", 3), ("Казань на следующей неделе, двое", None)],
)
def test_start_offset_is_not_a_duration(text, days):
    found, notes = extract(text)
    assert "nights" not in found and found["date_from"] > date.today()
    if days:
        assert found["date_from"] == date.today() + timedelta(days=days)
    else:
        assert found["date_from"].weekday() == 0 and any(n.startswith("даты:") for n in notes)


@pytest.mark.parametrize(
    "text,city",
    [
        ("Иркутск на неделю в декабре, бюджетно, из Москвы", "Иркутск"),
        ("Калининград в мае, на 5 дней", "Калининград"),
        ("Мурманск, северное сияние", "Мурманск"),
        ("Пермь на выходные", "Пермь"),
        ("Омск с ребёнком, на 4 дня", "Омск"),
    ],
)
def test_trap2_city_as_first_word_is_the_destination(text, city):
    found, _ = extract(text)
    assert found["destinations"] == [city]


def test_trap2_first_word_city_keeps_origin_and_other_fields():
    found, _ = extract("Иркутск на неделю в декабре, бюджетно, из Москвы")
    assert found["origin"] == "Москва" and found["destinations"] == ["Иркутск"]
    assert (found["month"], found["nights"]) == (12, 7)
    assert "недорого" in found["wishes"]


@pytest.mark.parametrize(
    "text",
    [
        "иркутск на неделю в декабре",
        "Иркутска на неделю в декабре",
        "хочу в иркутск на неделю",
        "Хочу в Иркутске на неделю",
        "Из Москвы в Иркутск на неделю",
    ],
)
def test_trap3_spelling_and_case_of_a_city_give_one_name(text):
    found, _ = extract(text)
    assert found["destinations"] == ["Иркутск"]


def test_trap3_origin_in_genitive_is_normalised_and_not_a_destination():
    found, _ = extract("Из Иркутска в Сочи на неделю")
    assert found["origin"] == "Иркутск" and found["destinations"] == ["Сочи"]


@pytest.mark.parametrize(
    "text",
    [
        "Тёплое море в ноябре, недорого, с ребёнком",
        "Хочу на море на неделю",
        "Недорого в ноябре",
        "Хочу в декабре, на 5 дней",
        "Подскажи отель на неделю",
        "Ищу, на 3 ночи",
        "Привет",
        "Бюджетно, на выходные",
    ],
)
def test_trap2_ordinary_first_word_is_not_a_destination(text):
    found, _ = extract(text)
    assert "destinations" not in found


@pytest.mark.parametrize(
    "text", ["Нет", "Нет, спасибо", "Спасибо", "Зима, горы, на неделю", "Один, на неделю"]
)
def test_trap2_chat_reply_or_season_is_not_a_destination(text):
    assert "destinations" not in extract(text)[0]


def test_trap2_chat_with_first_word_city_does_not_go_to_inspire(tmp_path):
    text = "Иркутск на неделю в декабре, бюджетно, из Москвы"
    r = chat(ChatRequest(message=text), SessionStore(tmp_path / "s.db"))
    assert r.stage != "inspire"
    assert r.plan.spec.destinations == ["Иркутск"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Отдых на море с детьми в ноябре", []),
        ("Мама с ребёнком в декабре, на море", []),
        ("Неделю на море в ноябре, недорого", []),
        ("Бюджет до 100 тысяч", []),
        ("Перенеси на неделю позже", []),
        ("Только с завтраком", []),
        ("Вылет в пятницу", []),
        ("Неделя в Италии", ["Италия"]),
        ("Музеи в Петербурге", ["Санкт-Петербург"]),
        ("Шопинг в Стамбуле", ["Стамбул"]),
    ],
)
def test_trap2_unknown_first_word_is_never_guessed_from_a_stop_list(text, expected):
    found, _ = extract(text)
    assert found.get("destinations", []) == expected


@pytest.mark.parametrize("text", ["Работа в декабре", "Отдых в ноябре"])
def test_trap2_first_word_pattern_needs_no_plan_destination(text):
    assert "destinations" not in extract(text, has_destination=True)[0]


@pytest.mark.parametrize(
    "follow",
    [
        "Перенеси на неделю позже",
        "Только с завтраком",
        "Вылет в пятницу",
        "Бюджет до 100 тысяч",
        "Работа в декабре",
    ],
)
def test_trap2_followup_message_keeps_the_destination_of_the_plan(follow):
    first = step(None, "s", "Сочи на неделю в декабре")
    r = step(first, "s", follow)
    assert r.plan.spec.destinations == ["Сочи"] and r.stage != "select"


# --- #68: tail of the PR #62 review (TRAP-3: spelling and case of a place) ---


@pytest.mark.parametrize(
    "text",
    [
        "Хочу в малом отеле у моря, в ноябре",
        "Хочу в малом отеле",
        "Номер в малом корпусе, на неделю",
        "Мала ли комната",
        "Номера малы, нужен побольше",
    ],
)
@pytest.mark.parametrize("has_destination", [False, True])
def test_trap3_ordinary_adjective_is_not_the_place_male(text, has_destination):
    assert "Мале" not in extract(text, has_destination)[0].get("destinations", [])


@pytest.mark.parametrize("text", ["Мале на 10 ночей", "Хочу в Мале", "хочу в мале на неделю"])
def test_trap3_male_is_still_found_by_its_name(text):
    assert extract(text)[0]["destinations"] == ["Мале"]


@pytest.mark.parametrize(
    "text, dests",
    [
        ("Дела такие: хочу на море в ноябре", []),
        ("Себе хочу тёплое море", []),
        ("Губы обветрились, хочу на море", []),
        ("Бару нашли, теперь отель", []),
        ("Кашу не любим, хотим в Италию", ["Италия"]),
    ],
)
def test_trap3_case_form_of_a_short_name_at_the_start_is_no_place(text, dests):
    """#102: «Дела» is a word; a short name at the start counts only in its exact form."""
    assert extract(text)[0].get("destinations", []) == dests


@pytest.mark.parametrize(
    "text, city", [("Дели в ноябре", "Дели"), ("хочу в Себу", "Себу"), ("Бар, 3 ночи", "Бар")]
)
def test_trap3_short_name_is_found_by_its_name_or_after_a_preposition(text, city):
    assert extract(text)[0]["destinations"] == [city]


def test_trap3_small_hotel_after_a_plan_keeps_the_city():
    first = step(None, "s", "Сочи на неделю в декабре")
    r = step(first, "s", "Хочу в малом отеле")
    assert r.plan.spec.destinations == ["Сочи"]


def test_trap3_small_hotel_in_the_first_message_does_not_go_to_male():
    r = step(None, "s", "Хочу в малом отеле у моря, в ноябре")
    assert r.plan.spec.destinations != ["Мале"]


@pytest.mark.parametrize(
    "text, origin, destination",
    [
        ("Из Тулы в Сочи", "Тула", "Сочи"),
        ("Из Уфы в Казань на неделю", "Уфа", "Казань"),
        ("Из Вены в Прагу", "Вена", "Прага"),
        ("из тулы в сочи", "Тула", "Сочи"),
    ],
)
def test_trap3_genitive_in_y_gives_the_nominative_origin(text, origin, destination):
    found, _ = extract(text)
    assert found["origin"] == origin and found["destinations"] == [destination]


@pytest.mark.parametrize("text", ["В Аляске на неделю", "Хочу в Аляску на неделю"])
def test_trap3_alaska_keeps_its_nominative_form(text):
    assert extract(text)[0]["destinations"] == ["Аляска"]


@pytest.mark.parametrize(
    "text, city",
    [
        ("В Омске на неделю", "Омск"),
        ("Хочу в Иркутске", "Иркутск"),
        ("Едем в Калининграде", "Калининград"),
    ],
)
def test_trap3_masculine_cities_in_sk_still_lose_their_ending(text, city):
    assert extract(text)[0]["destinations"] == [city]


@pytest.mark.parametrize(
    "word, city",
    [
        ("Новокузнецке", "Новокузнецк"),
        ("Донецке", "Донецк"),
        ("Полоцке", "Полоцк"),
        ("Норильске", "Норильск"),
        ("Брянске", "Брянск"),
        ("Аляске", "Аляска"),
    ],
)
def test_trap3_unknown_city_outside_the_catalogue_gets_the_right_nominative(word, city):
    assert extract(f"Хочу в {word} на неделю")[0]["destinations"] == [city]


@pytest.mark.parametrize(
    "text",
    [
        "Владимир, добрый день, хочу на море",
        "Владимир, привет! Хочу на море",
        "Владимир, здравствуйте, хочу в ноябре на море",
    ],
)
def test_trap3_name_with_a_greeting_is_an_address(text):
    assert "destinations" not in extract(text)[0]


@pytest.mark.parametrize("has_destination", [False, True])
def test_trap3_name_with_comma_is_an_address_not_the_city_vladimir(has_destination):
    found, _ = extract("Владимир, хочу на море в ноябре", has_destination)
    assert "destinations" not in found


def test_trap3_name_with_comma_in_the_first_message_goes_to_inspire():
    r = step(None, "s", "Владимир, хочу на море в ноябре")
    assert r.stage == "inspire" and r.plan.spec.destinations == []


@pytest.mark.parametrize(
    "text",
    ["Владимир на выходные", "Владимир на неделю", "Владимир, 3 ночи", "Во Владимир на неделю"],
)
def test_trap3_vladimir_as_a_city_still_works(text):
    assert extract(text)[0]["destinations"] == ["Владимир"]


@pytest.mark.parametrize("text", ["Ухта, 3 ночи", "Тихвин, 3 ночи"])
def test_trap3_unknown_city_in_a_plan_gets_an_honest_note(text):
    found, notes = extract(text, has_destination=True)
    assert "destinations" not in found
    assert any(n.startswith("направление:") and "нет в каталоге" in n for n in notes)


@pytest.mark.parametrize(
    "text",
    [
        "Бюджет, 50 тысяч", "Работа в декабре", "Взрослых, 3", "Отпуск, 3 ночи",
        "Перенеси на 2 дня позже", "Сократи на 2 ночи", "Продли на 2 дня", "Поменяй на 3 ночи",
        "Только на 3 ночи", "Тогда на 3 ночи", "Номер на 3 ночи", "Отложи на 2 недели",
        "Окей, 3 ночи", "Ок, 3 ночи", "Ухта на 3 дня", "Поездка на 5 дней",
    ],
)  # fmt: skip
def test_trap3_ordinary_first_word_in_a_plan_gets_no_city_note(text):
    assert not any("каталоге" in n for n in extract(text, has_destination=True)[1])


def test_trap3_unknown_city_without_a_plan_is_the_destination_and_has_no_note():
    found, notes = extract("Ухта, 3 ночи")
    assert found["destinations"] == ["Ухта"] and not any("каталоге" in n for n in notes)


def test_trap3_unknown_city_after_a_plan_says_so_and_changes_only_the_nights():
    first = step(None, "s", "Сочи на неделю в декабре")
    r = step(first, "s", "Ухта, 3 ночи")
    spec = r.plan.spec
    assert spec.destinations == ["Сочи"] and spec.nights == 3
    assert any("Ухта" in a and "нет в каталоге" in a for a in spec.assumptions)
    assert "Ухта" in r.reply


def test_trap3_city_note_goes_away_when_a_city_is_stated_later():
    first = step(None, "s", "Сочи на неделю в декабре")
    second = step(first, "s", "Ухта, 3 ночи")
    r = step(second, "s", "Лучше Казань")
    assert r.plan.spec.destinations == ["Казань"]
    assert not any("каталоге" in a for a in r.plan.spec.assumptions)


def _format_ts() -> str:
    return (Path(__file__).parents[2] / "web/src/format.ts").read_text(encoding="utf-8")


def _chip_names() -> dict[str, str]:
    """Priority names as the web chips say them: read from web/src/format.ts, no copy here."""
    body = _format_ts().split("export const PRIORITIES", 1)[1].split("};", 1)[0]
    return dict(re.findall(r'(\w+):\s*"([^"]+)"', body))


def _chip_prefix() -> str:
    """The start of the phrase priorityMessage builds, read from web/src/format.ts."""
    body = _format_ts().split("export function priorityMessage", 1)[1]
    return re.search(r"return `([^$`]+)\$\{", body).group(1)


def test_s9_chip_phrase_of_every_order_gives_only_priorities():
    names, prefix = _chip_names(), _chip_prefix()
    assert len(names) == 6 and prefix.startswith("Мои приоритеты")
    for size in range(1, len(names) + 1):
        for order in itertools.permutations(names, size):
            phrase = f"{prefix}{', '.join(names[p] for p in order)}."
            for has_destination in (False, True):
                found, _ = extract(phrase, has_destination)
                assert found == {"priorities": list(order)}, phrase
            assert is_chip_message(phrase), phrase


@pytest.mark.parametrize(
    "text",
    [
        "Для меня главное рейтинг, и чтобы можно было с животными",
        "Главное — бесплатная отмена и чтобы был бассейн",
        "Важнее цена, и с питанием",
        "Мои приоритеты по важности: отмена, цена, и ещё хочу бассейн.",
    ],
)
def test_s9_free_text_is_not_the_chip_phrase(text):
    assert not is_chip_message(text)


# --- #82: a two-word city first in the phrase (TRAP-3) ---


@pytest.mark.parametrize(
    "text, city",
    [
        ("Великий Новгород в мае", "Великий Новгород"),
        ("Минеральные Воды на 3 дня", "Минеральные Воды"),
        ("Нижний Новгород на выходные", "Нижний Новгород"),
        ("Санкт-Петербург в июле", "Санкт-Петербург"),
        ("санкт-петербург в июле", "Санкт-Петербург"),
        ("Петербург в июле", "Санкт-Петербург"),
        ("хочу в великий новгород в мае", "Великий Новгород"),
        ("из минеральных вод в казань", "Казань"),
        ("Великий Новгород, 3 ночи", "Великий Новгород"),
        ("Хочу в Великом Новгороде на 3 дня", "Великий Новгород"),
        ("Поездка в Минеральные Воды", "Минеральные Воды"),
        ("Отдых в Сочи", "Сочи"),
    ],
)
def test_trap3_two_word_city_in_any_place_of_the_phrase(text, city):
    assert extract(text)[0]["destinations"] == [city]


def test_trap3_two_word_city_as_origin_and_as_destination():
    found, _ = extract("Из Москвы в Великий Новгород")
    assert found["origin"] == "Москва" and found["destinations"] == ["Великий Новгород"]
    found, _ = extract("Из Великого Новгорода в Сочи")
    assert found["origin"] == "Великий Новгород" and found["destinations"] == ["Сочи"]


@pytest.mark.parametrize(
    "text",
    [
        "Хочу на море в мае",
        "Великий отпуск в мае",
        "Минеральная вода на 3 дня",
        "Хочу санаторий с минеральными водами в мае",
        "великий новгород, 3 ночи",  # lower case, no preposition: dropped on purpose
    ],
)
def test_trap3_ordinary_capitalised_first_words_are_no_city(text):
    assert "destinations" not in extract(text)[0]


def test_trap3_mineral_waters_in_a_sanatorium_wish_stay_a_wish():
    found, _ = extract("Санаторий в Кисловодске, лечение минеральными водами")
    assert found["destinations"] == ["Кисловодск"]
    assert extract("из минеральных вод в казань")[0]["origin"] == "Минеральные Воды"


@pytest.fixture
def tuesday(monkeypatch):
    """Fix «today» for the extractor: Tuesday 2026-10-27."""

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 27)

    monkeypatch.setattr("api.extract.date", FixedDate)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("в Минск на поезде в пятницу", date(2026, 10, 30)),
        ("в Минск на поезде во вторник", date(2026, 11, 3)),
        ("в Казань в понедельник, двое", date(2026, 11, 2)),
        ("в Казань через неделю, двое", date(2026, 11, 3)),
        ("в Казань в эти выходные, двое", date(2026, 10, 30)),
    ],
)
def test_relative_date_is_the_nearest_from_today(tuesday, text, expected):
    found, _ = extract(text)
    assert found["date_from"] == expected


@pytest.mark.parametrize(
    "text",
    ["отдых в средней полосе в ноябре, двое", "Казань по субботам, двое", "Казань в среде, двое"],
)
def test_weekday_lookalikes_are_not_dates(tuesday, text):
    found, _ = extract(text)
    assert "date_from" not in found
    if "ноябре" in text:
        assert found["month"] == 11


def test_weekday_respects_the_named_month(tuesday):
    found, _ = extract("в Сочи в ноябре в пятницу, двое")
    assert found["date_from"] == date(2026, 11, 6) and found["month"] == 11


def test_hotel_budget_survives_a_follow_up_with_a_pet(tmp_path):
    """#41: «отель до 40 тысяч» stays on the hotel after the pet changes the pick."""
    store = SessionStore(tmp_path / "s.db")
    r = chat(ChatRequest(message="Отель до 40 тысяч, Сочи на неделю"), store)
    r = chat(ChatRequest(session_id=r.session_id, message="с собакой"), store)
    hotels = [o for o in r.plan.offers if o.kind == "hotel" and o.offer_id in r.plan.selected]
    assert r.plan.spec.hotel_budget_rub == 40_000 and r.plan.spec.budget_rub is None
    assert len(hotels) == 1 and hotels[0].price_rub <= 40_000 and hotels[0].pets_allowed


def test_flight_and_hotel_sum_is_the_trip_budget_and_a_dearer_plan_is_flagged(tmp_path):
    """#41: «перелёт и отель до 100 тысяч» limits the trip; a dearer booking carries the risk."""
    store = SessionStore(tmp_path / "s.db")
    r = chat(ChatRequest(message="Бали на 12 ночей вдвоём, перелёт и отель до 100 тысяч"), store)
    if r.stage == "clarify":  # #302: a far trip with a whole budget asks the origin first
        r = chat(ChatRequest(session_id=r.session_id, message="Из Москвы"), store)
    spec = r.plan.spec
    assert spec.budget_rub == 100_000 and spec.hotel_budget_rub is None
    total = sum(o.price_rub for o in r.plan.offers if o.offer_id in r.plan.selected)
    assert total > 100_000
    risks = [x for e in r.plan.explanations for x in e.risks]
    assert any("не укладывается в бюджет" in x for x in risks)


@pytest.mark.parametrize(
    "text",
    [
        "отель с перелётом до 60 тысяч",
        "отель с билетами до 60 тысяч",
        "отель с авиа до 60 тысяч",
        "отель с трансфером до 60 тысяч",
        "60 тысяч на отель и перелёт",
        "тур с отелем до 60 тысяч",
        "перелёт и отель до 60 тысяч",
        "перелёт + отель до 60 тысяч",
        "самолёт и отель до 60 тысяч",
        "самолет + отель до 60 тысяч",
        "такси и отель до 60 тысяч",
        "экскурсии и отель до 60 тысяч",  # a word no list knows: «и отель» is enough
        "виза плюс отель до 60 тысяч",
        "ж/д и отель до 60 тысяч",
    ],
)
def test_hotel_with_another_expense_item_is_the_trip_budget(text):
    """#41: «отель» and another expense item in one fragment: the sum is for the whole trip."""
    found, notes = extract(text)
    assert found.get("budget_rub") == 60_000 and "hotel_budget_rub" not in found
    assert not any(n.startswith("бюджет отеля:") for n in notes)


@pytest.mark.parametrize(
    "text,hotel",
    [
        ("отель до 40 тысяч", 40_000),
        ("40 тысяч на отель", 40_000),
        ("отель до 40 тысяч, перелёт любой", 40_000),
        ("5 ночей, отель до 5000 за ночь", 25_000),
    ],
)
def test_hotel_alone_in_its_fragment_is_the_hotel_budget(text, hotel):
    found, _ = extract(text)
    assert found.get("hotel_budget_rub") == hotel and "budget_rub" not in found


def test_hotel_price_per_night_without_nights_is_kept_in_the_assumption():
    found, notes = extract("отель до 5000 за ночь")
    assert "hotel_budget_rub" not in found and "budget_rub" not in found
    assert notes == ["бюджет отеля: 5 000 ₽ за ночь"]


def test_hotel_with_flight_sum_is_the_trip_budget_and_a_dearer_cart_is_flagged(tmp_path):
    """#41: «отель с перелётом до 60 тысяч» limits the trip; a dearer cart shows the risk."""
    store = SessionStore(tmp_path / "s.db")
    r = chat(ChatRequest(message="Бали на 12 ночей вдвоём, отель с перелётом до 60 тысяч"), store)
    if r.stage == "clarify":  # #302: a far trip with a whole budget asks the origin first
        r = chat(ChatRequest(session_id=r.session_id, message="Из Москвы"), store)
    spec = r.plan.spec
    assert spec.budget_rub == 60_000 and spec.hotel_budget_rub is None
    total = sum(o.price_rub for o in r.plan.offers if o.offer_id in r.plan.selected)
    assert total > 60_000
    risks = [x for e in r.plan.explanations for x in e.risks]
    assert any("не укладывается в бюджет" in x for x in risks)


@pytest.mark.parametrize("text", ["отель до 1500 м от моря", "отель до 2027 года"])
def test_hotel_with_a_bare_number_that_is_not_money_has_no_budget(text):
    found, notes = extract(text)
    assert "hotel_budget_rub" not in found and "budget_rub" not in found and notes == []


# #275: adults are counted from the family words, not guessed as 2.
@pytest.mark.parametrize(
    "text, adults, kids",
    [
        ("Едем я, муж, двое детей 6 и 9 лет и бабушка с дедушкой, в Анапу на 7 ночей", 4, [6, 9]),
        ("Едем с ребёнком 5 лет, с дедушкой и бабушкой в Сочи", 3, [5]),
        ("Мы с мужем, моими родителями и сыном 5 лет летим в Сочи", 4, [5]),
        ("Поедем я, мама, папа и сестра с дочкой 3 года в Казань", 4, [3]),
        ("Едем я, жена, друг и подруга в Сочи на 5 ночей", 4, None),
        ("Едем с мужем и ребёнком 4 года в Сочи", 2, [4]),
    ],
)
def test_adults_are_counted_from_family_words(text, adults, kids):
    found, _ = extract(text)
    assert found["adults"] == adults
    assert found.get("children_ages") == kids


# --- #274: colloquial names and typos in a message (TRAP-3) ---


def test_trap_3_colloquial_origin_and_typo_destination():
    found, _ = extract("из Ебурга в Кисловоцк на неделю в мае")
    assert found["origin"] == "Екатеринбург"
    assert found["destinations"] == ["Кисловодск"]


@pytest.mark.parametrize(
    "text", ["в Минводы в июне", "хочу в Мин Воды в июне", "в Минеральные Воды в июне",
             "из Москвы в Минводы на неделю"],
)  # fmt: skip
def test_trap_3_min_vody_is_one_place(text):
    found, _ = extract(text)
    assert found["destinations"] == ["Минеральные Воды"]


def test_trap_3_no_place_in_ordinary_sentences():
    for text in ("Хорошо бы недорого, с ребенком на 5 дней", "Привет! Хотим отдохнуть в мае"):
        found, _ = extract(text)
        assert not found.get("destinations"), text


@pytest.mark.parametrize("name", ["Коломбо", "Чиангмай", "Чианграй"])
def test_trap_3_exact_catalogue_name_is_not_respelled(name):
    found, _ = extract(f"хочу в {name} в мае")
    assert found["destinations"] == [name]


C15 = "Из Ебурга в Кисловоцк через Минводы, с мамой 70 лет в санаторий на 10 ночей в мае"


def test_trap_3_transit_point_is_no_destination():
    found, _ = extract(C15)
    assert found["origin"] == "Екатеринбург"
    assert found["destinations"] == ["Кисловодск"]
    found, _ = extract("Хочу в Сочи с пересадкой в Москве на неделю")
    assert found["destinations"] == ["Сочи"]


def test_trap_3_c15_full_turn_has_no_missing_catalogue_reply(tmp_path):
    r = chat(ChatRequest(session_id=None, message=C15), SessionStore(tmp_path / "s.db"))
    assert "нет в каталоге" not in r.reply.lower(), r.reply


@pytest.mark.parametrize(
    ("text", "dests"),
    [("из Москвы в Казань, потом через Нижний в Сочи", ["Казань", "Нижний Новгород", "Сочи"]),
     ("хочу проехать через Карелию и Мурманск", ["Карелия", "Мурманск"]),
     ("из Москвы через Казань в Екатеринбург, по 3 ночи в каждом", ["Казань", "Екатеринбург"]),
     ("маршрут через Ярославль, Кострому и Плёс в июле", ["Ярославль"]),
     ("из Москвы в Санкт-Петербург через Великий Новгород на 5 дней",
      ["Санкт-Петербург", "Великий Новгород"])],
)  # fmt: skip
def test_trap_3_route_stop_after_cherez_stays_a_destination(text, dests):
    found, _ = extract(text)
    assert found["destinations"] == dests


# #293: a range by weekdays («со среды по пятницу») is a date range, not an assumption.
@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("Казань со среды по пятницу", date(2026, 10, 28), date(2026, 10, 30)),
        ("Казань с пятницы по воскресенье", date(2026, 10, 30), date(2026, 11, 1)),
        ("Казань с понедельника до среды", date(2026, 11, 2), date(2026, 11, 4)),
        ("Казань с пятницы по понедельник", date(2026, 10, 30), date(2026, 11, 2)),
        ("Казань с четверга по субботу, двое", date(2026, 10, 29), date(2026, 10, 31)),
    ],
)
def test_weekday_range_gives_the_nearest_dates(tuesday, text, start, end):
    found, notes = extract(text)
    assert found["date_from"] == start and found["date_to"] == end
    assert found["nights"] == (end - start).days and found["month"] == start.month
    assert notes == []


def test_weekday_range_from_today_weekday_starts_next_week(tuesday):
    found, _ = extract("Казань со вторника по четверг")
    assert (found["date_from"], found["date_to"]) == (date(2026, 11, 3), date(2026, 11, 5))


def test_weekday_range_turn_has_no_date_assumption(tuesday, tmp_path):
    r = chat(
        ChatRequest(session_id=None, message="Казань со среды по пятницу"),
        SessionStore(tmp_path / "s.db"),
    )
    spec = r.plan.spec
    assert (spec.date_from, spec.date_to, spec.nights) == (
        date(2026, 10, 28),
        date(2026, 10, 30),
        2,
    )
    assert not [a for a in spec.assumptions if "дат" in a.lower()], spec.assumptions


@pytest.mark.parametrize(
    ("text", "start", "end", "month"),
    [
        ("Казань в декабре со среды по пятницу", date(2026, 12, 2), date(2026, 12, 4), 12),
        ("Казань летом со среды по пятницу", date(2027, 6, 2), date(2027, 6, 4), 6),
        (
            "Казань на следующей неделе со среды по пятницу",
            date(2026, 11, 4),
            date(2026, 11, 6),
            11,
        ),
        ("Казань со среды по пятницу следующей недели", date(2026, 11, 4), date(2026, 11, 6), 11),
        ("Казань через неделю со среды по пятницу", date(2026, 11, 4), date(2026, 11, 6), 11),
    ],
)
def test_weekday_range_respects_month_season_and_week_shift(tuesday, text, start, end, month):
    found, notes = extract(text)
    assert (found["date_from"], found["date_to"], found["month"]) == (start, end, month)
    assert not [n for n in notes if n.startswith("даты:")]


def test_weekday_range_names_a_conflicting_length(tuesday):
    found, notes = extract("Казань со среды по пятницу, на 5 ночей")
    assert found["nights"] == 2
    assert any(n.startswith("даты:") and "5 ночей" in n for n in notes), notes


@pytest.mark.parametrize(
    "text",
    [
        "с понедельника по пятницу работаю, поехать на выходные в Казань",
        "Казань, я работаю со среды по пятницу",
    ],
)
def test_weekday_range_of_work_days_is_no_trip_range(tuesday, text):
    found, _ = extract(text)
    if "выходные" in text:  # the trip is the weekend: Friday to Sunday
        assert (found["date_from"], found["date_to"]) == (date(2026, 10, 30), date(2026, 11, 1))
    else:
        assert "date_to" not in found
