"""Rule extractor and clarification policy (T3, #7): one case per rule, phrases from eval/cases.json."""

from datetime import date, timedelta

import pytest

from api.contracts import ChatRequest
from api.extract import extract
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
    assert found["budget_rub"] == 40_000 and found["meal"] == "BB"
    assert notes == ["бюджет: 40 000 ₽ на всю поездку (сказано про отель)"]


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
    assert "сказано про отель" in r.reply


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
