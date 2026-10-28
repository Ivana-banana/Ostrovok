"""#185: a holiday instead of dates («на новый год», «каникулы») and a party of friends.

Without a model (TRAP-1). «Today» is fixed: 2026-10-27.
"""

from datetime import date

import pytest
from talk import say

from api.extract import extract
from api.store import SessionStore


@pytest.fixture
def today(monkeypatch):
    def fix(y=2026, m=10, d=27):
        class Fixed(date):
            @classmethod
            def today(cls):
                return cls(y, m, d)

        monkeypatch.setattr("api.extract.date", Fixed)
        monkeypatch.setattr("api.dag.date", Fixed)
        monkeypatch.setattr("api.model.date", Fixed)

    fix()
    return fix


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


@pytest.mark.parametrize(
    "text",
    [
        "Тбилиси на новый год",
        "Тбилиси на новогодние праздники",
        "Тбилиси на новогодние каникулы",
        "Тбилиси, хочу встретить Новый год",
        "Тбилиси на зимние каникулы",
    ],
)
def test_new_year_gives_dates_and_a_note(today, text):
    found, notes = extract(text)
    assert found["date_from"] == date(2026, 12, 30) and found["date_to"] == date(2027, 1, 8)
    assert found["nights"] == 9 and found["month"] == 12
    assert len(notes) == 1 and notes[0].startswith("даты: 30.12–08.01")
    assert "праздники 1–8 января" in notes[0] and "допущение" in notes[0]


def test_new_year_reply_does_not_say_no_term(today, store):
    r = say(store, "Тбилиси на новый год из Москвы, двое взрослых")
    spec = r.plan.spec
    assert spec.date_from == date(2026, 12, 30) and spec.date_to == date(2027, 1, 8)
    assert spec.nights == 9
    assert not any("не назван" in a or "точных дат не было" in a for a in spec.assumptions)
    assert sum(a.startswith("даты:") for a in spec.assumptions) == 1
    assert "30.12" in r.reply


def test_new_year_with_named_length_keeps_the_start(today):
    found, notes = extract("Тбилиси на новый год на 5 ночей")
    assert found["date_from"] == date(2026, 12, 30) and found["date_to"] == date(2027, 1, 4)
    assert found["nights"] == 5 and notes[0].startswith("даты: 30.12–04.01")
    found, _ = extract("Тбилиси на новогодние праздники на неделю")
    assert found["date_to"] == date(2027, 1, 6)


def test_new_year_with_winter_word_has_no_second_note(today):
    _, notes = extract("Тбилиси зимой на новый год")
    assert len(notes) == 1 and notes[0].startswith("даты:")


def test_new_year_in_the_first_week_of_january_is_this_break(today):
    today(2027, 1, 3)
    found, _ = extract("Тбилиси на новогодние праздники")
    assert found["date_from"] == date(2027, 1, 4) and found["date_to"] == date(2027, 1, 8)
    today(2027, 1, 20)
    assert extract("Тбилиси на новый год")[0]["date_from"] == date(2027, 12, 30)


def test_new_year_edge_days_never_stretch_the_window(today):
    for day, start in [((2026, 12, 31), date(2027, 1, 1)), ((2027, 1, 6), date(2027, 1, 7))]:
        today(*day)
        found, _ = extract("Тбилиси на новый год")
        assert found["date_from"] == start and found["date_to"] == date(2027, 1, 8)
    for day in [(2027, 1, 7), (2027, 1, 8)]:  # the break is over: the next one
        today(*day)
        found, _ = extract("Тбилиси на новый год")
        assert found["date_from"] == date(2027, 12, 30) and found["date_to"] == date(2028, 1, 8)


@pytest.mark.parametrize(
    "text",
    [
        "Сочи после нового года, из Москвы, вдвоём",
        "Сочи до нового года",
        "Сочи, кроме новогодних праздников",
        "Сочи перед новым годом",
        "Сочи не на новый год",
        "Сочи, к новому году вернуться домой",
        "Сочи, к новому году надо быть дома",
        "Сочи после новогодних праздников",
    ],
)
def test_around_the_new_year_is_no_holiday_trip(today, text):
    found, notes = extract(text)
    assert "date_from" not in found and "nights" not in found
    assert not [n for n in notes if n.startswith("даты")]


def test_to_the_new_year_without_a_return_is_a_holiday_trip(today):
    assert extract("Хочу в Сочи к новому году")[0]["date_from"] == date(2026, 12, 30)


def test_a_stated_date_wins_over_the_holiday(today):
    found, _ = extract("Тбилиси с 20 по 25 декабря, потом на новый год домой")
    assert found["date_from"] == date(2026, 12, 20) and found["date_to"] == date(2026, 12, 25)


def test_holiday_is_dropped_when_another_month_is_named(today):
    found, _ = extract("Сочи в марте, новогодние фотографии покажи")
    assert found["month"] == 3 and "date_from" not in found


def test_summer_holidays_are_a_month_with_a_note(today):
    found, notes = extract("Сочи на летние каникулы")
    assert found["month"] == 6 and "date_from" not in found
    assert notes == ["когда: июнь («летние каникулы»: первый месяц сезона)"]


def test_summer_holidays_in_summer_take_this_month(today):
    today(2027, 7, 3)
    assert extract("Сочи на летние каникулы")[0]["month"] == 7


def test_bare_holidays_take_the_nearest_month_and_say_so(today, store):
    found, notes = extract("Сочи на каникулы")
    assert found["month"] == 11 and "date_from" not in found
    assert notes == ["когда: ноябрь («каникулы»: сезон не назван, взят ближайший месяц)"]
    r = say(store, "Сочи на каникулы из Москвы, двое взрослых")
    assert sum(a.startswith("когда:") for a in r.plan.spec.assumptions) == 1
    assert not any("срок не назван" in a for a in r.plan.spec.assumptions)


def test_named_month_beats_the_holiday_word(today):
    found, notes = extract("Сочи на летние каникулы в июле")
    assert found["month"] == 7 and notes == []


@pytest.mark.parametrize(
    "text",
    [
        "Новый Уренгой в ноябре",
        "Новый Афон в ноябре",
        "Хочу в новый отель в Сочи",
        "Сочи, друзья посоветовали",
        "Сочи в ноябре, отель пять звёзд",
        "Сочи в ноябре, отель пяти звёзд",
    ],
)
def test_other_words_are_no_holiday_and_no_friends(today, text):
    found, notes = extract(text)
    assert "date_from" not in found and "adults" not in found
    assert not [n for n in notes if n.startswith(("даты", "взрослых"))]
    if "Уренгой" in text or "Афон" in text:
        assert found.get("month") == 11


@pytest.mark.parametrize(
    "text,adults",
    [
        ("Сочи в ноябре, трое друзей", 4),
        ("Сочи в ноябре, я и трое друзей", 4),
        ("Сочи в ноябре, с двумя друзьями", 3),
        ("Сочи в ноябре, едем с тремя подругами", 4),
        ("Сочи в ноябре, с четырьмя друзьями", 5),
        ("Сочи в ноябре, двое друзей и я", 3),
        ("Сочи в ноябре, с 3 друзьями", 4),
    ],
)
def test_friends_are_extra_adults_with_a_note(text, adults):
    found, notes = extract(text)
    assert found["adults"] == adults
    assert f"взрослых: {adults} (вы и " in notes[0]


@pytest.mark.parametrize(
    "text,adults",
    [
        ("Мы две подруги, хотим на море в ноябре из Москвы", 2),
        ("Мы три подруги, Сочи в ноябре", 3),
        ("Мы трое друзей, Сочи в ноябре", 3),
        ("Трое друзей едем в Казань", 3),
        ("Едем трое друзей в Казань", 3),
        ("Сочи в ноябре, мы с тремя друзьями", 4),
    ],
)
def test_friends_who_are_the_whole_party(text, adults):
    found, _ = extract(text)
    assert found["adults"] == adults


def test_the_note_keeps_the_users_word():
    assert extract("Сочи, с тремя подругами")[1] == ["взрослых: 4 (вы и три подруги)"]
    assert extract("Сочи, с двумя коллегами")[1] == ["взрослых: 3 (вы и двое коллег)"]
    assert extract("Сочи, мы две подруги")[1] == ["взрослых: 2 (две подруги едут вместе)"]


@pytest.mark.parametrize("text,kids", [("с пятью детьми", 5), ("с четырьмя детьми", 4)])
def test_many_children_in_the_instrumental(text, kids):
    found, _ = extract(f"Сочи в ноябре, {text}")
    assert len(found["children_ages"]) == kids


def test_three_friends_note_text():
    assert extract("Сочи, трое друзей")[1] == ["взрослых: 4 (вы и трое друзей)"]


def test_friends_with_a_child_keep_the_child():
    found, _ = extract("Сочи в ноябре, трое друзей и дочка 5 лет")
    assert found["adults"] == 4 and found["children_ages"] == [5]


def test_the_group_word_beats_the_friends():
    found, notes = extract("Сочи в ноябре, нас пятеро, трое друзей и я")
    assert found["adults"] == 5 and not notes


def test_we_are_five_means_five_adults(store):
    found, _ = extract("Сочи в ноябре, нас пятеро")
    assert found["adults"] == 5
    r = say(store, "Сочи в ноябре из Москвы, нас пятеро")
    assert r.plan.spec.adults == 5 and not r.plan.spec.children_ages


def test_three_friends_reach_the_plan(store):
    r = say(store, "Сочи в ноябре из Москвы, трое друзей")
    assert r.plan.spec.adults == 4
    assert "взрослых: 4 (вы и трое друзей)" in r.plan.spec.assumptions
    assert "взрослых: 2 (состав не назван)" not in r.plan.spec.assumptions


def test_demo_phrase_is_unchanged(today):
    found, _ = extract("Тёплое море в ноябре, недорого, с ребёнком")
    assert found["month"] == 11 and "date_from" not in found and "adults" not in found
