"""#190: a new month after named dates moves the dates: the month and the dates never disagree.

Rules only, no model (TRAP-1). Today is fixed: Tuesday 2026-10-27.
"""

from datetime import date

import pytest

from api.contracts import ChatRequest
from api.model import chat
from api.store import SessionStore


@pytest.fixture(autouse=True)
def today(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 27)

    for module in ("extract", "dag", "model"):
        monkeypatch.setattr(f"api.{module}.date", FixedDate)


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def say(store, text, sid=None):
    return chat(ChatRequest(session_id=sid, message=text), store)


def named(store, dates="с 3 по 8 декабря"):
    r = say(store, f"Сочи из Москвы {dates}, двое взрослых")
    assert r.stage == "compare"
    return r


def agree(r):
    s = r.plan.spec
    assert s.month == s.date_from.month
    assert s.nights == (s.date_to - s.date_from).days


def test_next_month_after_named_dates_moves_them_keeping_day_and_length(store):
    first = named(store)
    r = say(store, "лучше в январе", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.nights, s.month) == (
        date(2027, 1, 3),
        date(2027, 1, 8),
        5,
        1,
    )
    agree(r)
    change = next(c for c in r.plan.changes if c.what == "даты")
    assert "03.12" in change.before and "03.01" in change.after
    assert "Было → стало" in r.reply and "03.01.2027–08.01.2027" in r.reply
    assert "→ 03.12" not in r.reply and "03.12–08.12" not in r.reply.split("\n", 2)[2]
    assert not any(a.startswith("даты:") for a in s.assumptions)  # the dates stay named


def test_the_cart_dates_follow_the_new_month(store):
    first = named(store)
    r = say(store, "лучше в январе", first.session_id)
    r = say(store, "беру", r.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3)
    assert all("03.12" not in i.title for i in r.cart.items)


def test_summer_after_named_dates(store):
    first = named(store)
    r = say(store, "давай летом", first.session_id)
    s = r.plan.spec
    assert s.month == 6 and s.date_from == date(2027, 6, 3) and s.nights == 5
    agree(r)


def test_the_move_survives_the_next_message(store):
    first = named(store)
    r = say(store, "лучше в январе", first.session_id)
    r = say(store, "на 4 ночи", r.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3) and r.plan.spec.nights == 4


def test_the_end_of_the_month_is_clamped(store):
    first = named(store, "с 29 по 31 января")
    r = say(store, "лучше в феврале", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.nights) == (date(2027, 2, 28), date(2027, 3, 2), 2)
    agree(r)


def test_the_past_check_in_goes_to_next_year(store):
    first = named(store, "с 3 по 8 декабря")
    r = say(store, "лучше в октябре", first.session_id)  # 03.10.2026 is past
    assert r.plan.spec.date_from == date(2027, 10, 3)


def test_the_explicit_date_wins_over_the_month_move(store):
    first = named(store)
    r = say(store, "лучше в январе, с 10 по 15 января", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to) == (date(2027, 1, 10), date(2027, 1, 15))


def test_the_same_month_changes_nothing(store):
    first = named(store)
    r = say(store, "лучше в декабре", first.session_id)
    assert (r.plan.spec.date_from, r.plan.spec.date_to) == (date(2026, 12, 3), date(2026, 12, 8))


def test_guessed_dates_move_as_before(store):
    first = say(store, "Сочи из Москвы в ноябре, двое взрослых")
    guess = first.plan.spec.date_from
    assert any("точных дат не было" in a for a in first.plan.spec.assumptions)
    r = say(store, "лучше в январе", first.session_id)
    s = r.plan.spec
    assert s.month == 1 and s.date_from.month == 1 != guess.month
    assert any("точных дат не было" in a for a in s.assumptions)  # still a guess
    assert s.date_from.weekday() == 5  # the guess picks a Saturday
    agree(r)


@pytest.mark.parametrize(
    "text",
    [
        "не в январе, а в декабре",
        "только не в январе",
        "а в январе там холодно?",
        "в январе мы уже были в Сочи",
    ],
)
def test_a_month_that_is_no_request_changes_neither_dates_nor_month(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.nights, s.month) == (
        date(2026, 12, 3),
        date(2026, 12, 8),
        5,
        12,
    )
    assert not any(c.what in ("даты", "месяц") for c in r.plan.changes)
    agree(r)


def test_a_move_drops_the_old_notes_about_the_dates(store):
    first = named(store, "с 20 по 25 октября")  # a slip: the day passed a week ago
    assert any("уже прошло" in a for a in first.plan.spec.assumptions)
    r = say(store, "лучше в январе", first.session_id)
    s = r.plan.spec
    assert s.date_from == date(2027, 1, 20)
    assert not any("уже прошло" in a for a in s.assumptions)
    assert not any(a.startswith("даты:") for a in s.assumptions)


def test_a_move_drops_the_shift_note(store):
    first = named(store)
    r = say(store, "на неделю позже", first.session_id)
    assert any("сдвинуты" in a for a in r.plan.spec.assumptions)
    r = say(store, "лучше в январе", r.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 10)
    assert not any("сдвинуты" in a for a in r.plan.spec.assumptions)


def test_a_slip_into_the_past_by_a_move_says_so(store):
    first = named(store)
    r = say(store, "лучше в октябре", first.session_id)  # 03.10 passed three weeks ago
    s = r.plan.spec
    assert s.date_from == date(2027, 10, 3)
    assert "даты: 03.10 уже прошло — взят 2027 год" in s.assumptions
    r = say(store, "на 4 ночи", r.session_id)  # the named dates stay named
    assert r.plan.spec.date_from == date(2027, 10, 3)


def stays(r, first, stage="compare"):
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.nights, s.month) == (
        date(2026, 12, 3),
        date(2026, 12, 8),
        5,
        12,
    )
    assert not any(c.what in ("даты", "месяц") for c in r.plan.changes)
    assert r.stage == stage and not r.cart.items and r.plan.spec == first.plan.spec


@pytest.mark.parametrize(
    "text", ["январь не подходит", "в январе не получится", "в январе нельзя", "кроме января"]
)
def test_a_rejection_after_the_month_keeps_the_dates_and_is_no_consent(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    stays(r, first)
    assert "03.12–08.12" in r.reply and "остаются" in r.reply


@pytest.mark.parametrize(
    "text",
    [
        "давай в январе?",
        "может, в январе?",
        "можно в январе?",
        "в январе мы уже были в Сочи",
        "не в декабре, лучше в январе",
        "а в январе там холодно?",
    ],
)
def test_an_unclear_month_asks_and_is_no_consent(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    stays(r, first)
    assert "03.12–08.12 вы назвали сами" in r.reply and "Перенести на январь" in r.reply
    assert r.stage != "assemble"


def test_the_question_has_a_chip_that_moves_the_dates(store):
    first = named(store)
    r = say(store, "давай в январе?", first.session_id)
    assert "Перенести на январь (тот же день и длина) или назовёте даты?" in r.reply
    chip = r.questions[0].options[0]
    r = say(store, chip, r.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3) and r.plan.spec.nights == 5


def test_a_contrast_asks_about_the_month_after_the_comma(store):
    first = named(store)
    r = say(store, "не в январе, а в феврале", first.session_id)
    stays(r, first)
    assert "Перенести на февраль" in r.reply and "январь" not in r.reply


def test_a_contrast_with_the_own_month_keeps_the_dates(store):
    first = named(store)
    r = say(store, "не в январе, а в декабре", first.session_id)
    stays(r, first)
    assert "остаются" in r.reply


def test_a_month_among_other_words_is_still_a_request(store):
    first = named(store)
    r = say(store, "давай без пересадок, в январе", first.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3) and r.stage == "compare"


def test_dates_that_reach_into_the_month_are_not_moved(store):
    first = named(store, "с 30 декабря по 4 января")
    r = say(store, "лучше в январе", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to) == (date(2026, 12, 30), date(2027, 1, 4))
    assert "заходят в январь" in r.reply and r.stage == "compare"


@pytest.mark.parametrize("text", ["отпуск до января", "после января"])
def test_a_limit_word_before_the_month_is_no_request(store, text):
    first = named(store)
    r = say(store, f"бюджет 100 тысяч, {text}", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2026, 12, 3), date(2026, 12, 8), 12)


@pytest.mark.parametrize(
    "text", ["давай с января", "хорошо, с января", "давай после января", "хорошо, после января"]
)
def test_a_limit_word_before_the_month_is_no_consent(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2026, 12, 3), date(2026, 12, 8), 12)
    assert r.stage == "compare" and not r.cart.items


# #198: a negation in a neighbouring list item is no refusal of the month.


@pytest.mark.parametrize(
    "text",
    [
        "не дорого, в январе",
        "недорого, в январе",
        "не дорого и в январе",
        "не очень дорого, в январе",
    ],
)
def test_a_negation_of_a_neighbour_does_not_refuse_the_month(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2027, 1, 3), date(2027, 1, 8), 1)
    assert "Они остаются" not in r.reply and r.stage == "compare"


def test_a_month_before_a_neighbouring_negation_is_no_refusal(store):
    first = named(store)
    r = say(store, "лучше в январе, не дорого", first.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3)


@pytest.mark.parametrize("text", ["не в январе, недорого", "в январе не получится, недорого"])
def test_the_refusal_before_a_neighbour_stays_a_refusal(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2026, 12, 3), date(2026, 12, 8), 12)
    assert "Они остаются" in r.reply and r.stage == "compare"


def test_a_shift_into_the_refused_month_is_not_applied(store):
    first = named(store)
    r = say(store, "не в январе, на месяц позже", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2026, 12, 3), date(2026, 12, 8), 12)
    assert "Сдвиг не применён" in r.reply and "январь" in r.reply
    assert "Перенести на декабрь" not in r.reply and not r.cart.items
    assert r.stage == "compare"


def test_a_shift_out_of_the_refused_month_is_applied(store):
    first = named(store)
    r = say(store, "не в январе, на два дня позже", first.session_id)
    assert r.plan.spec.date_from == date(2026, 12, 5)


def test_the_other_fields_stay_applied_when_the_shift_is_refused(store):
    first = named(store)
    r = say(store, "не в январе, на месяц позже, бюджет 100 тысяч", first.session_id)
    assert r.plan.spec.budget_rub == 100_000 and r.plan.spec.date_from == date(2026, 12, 3)
    assert "Сдвиг не применён" in r.reply


def test_a_shift_into_the_asked_month_is_told_as_a_shift(store):
    first = named(store)
    r = say(store, "может в январе? на месяц позже", first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2027, 1, 3), date(2027, 1, 8), 1)
    assert "Даты сдвинуты по вашей просьбе: 03.01.2027–08.01.2027." in r.reply
    assert "вы назвали сами" not in r.reply and "Перенести" not in r.reply
    assert not r.questions and r.stage == "compare" and not r.cart.items


# #198 review: without a comma a «не» after the month is a refusal, as it was. The verbs of
# refusing only decide the part after a comma, «но», «и» or «а».

REFUSALS = [
    "в январе не поедем",
    "январь не",
    "в январе не летим",
    "в январе не едем",
    "январь не рассматриваем",
    "январь не нужен",
    "в январе не будет отпуска",
    "в январе — не",
    "в январе не очень удобно",
    "в январе не, лучше в феврале",
    "в январе не дорого",  # no comma: the «не» stays with the month, as on main
    "в январе и феврале не поедем",
    "январь и февраль не подходят",
    "в январе, увы, не получится",
    "в январе, но не получится",
    "январь, не",
    "в январе, не",
    # #215: a bare «нет» and the like
    "в январе нет",
    "январь — нет",
    "январь нет",
    "в январе никак",
    "январь точно нет",
    "январь мимо",
    "январь отменяется",
    "в январе неудобно",
    "январь, к сожалению, не подходит",
    "в январе и в феврале не можем",
    "в январе, не летаем",
    "в январе, не поедешь",
    "январь, увы, нельзя",
    "в январе, нам нельзя",
    "в январе, никак нельзя",
    "январь, никак нельзя",
    "в январе, нельзя никак",
    "в январе, нельзя: работа",
    "январь, это невозможно",
    "в январе, невозможно по работе",
    "в январе, к сожалению, исключено",
    "январь, увы, отпадает",
    "январь, отпадает к сожалению",
    "январь, отпадает совсем",
]


@pytest.mark.parametrize("text", REFUSALS)
def test_a_refusal_after_the_month_keeps_the_dates(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2026, 12, 3), date(2026, 12, 8), 12)
    assert r.stage == "compare" and "Они остаются" in r.reply


@pytest.mark.parametrize(
    "text", ["в январе, не дорого", "в январе, не в феврале", "в январе, не на самолёте"]
)
def test_a_negated_neighbour_after_a_comma_is_no_refusal(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3) and "Они остаются" not in r.reply


@pytest.mark.parametrize(
    "text",
    [
        "в январе не поедем, подтверждаю",
        "январь не, оформляй",
        "в январе и феврале не поедем, подтверждаю",
        "в январе, увы, не получится, подтверждаю",
        "январь, не, подтверждаю",
        "январь и февраль не подходят, беру",
    ],
)
@pytest.mark.parametrize("stages", [1, 2])
def test_a_refusal_is_no_consent_at_assemble_and_checkout(store, text, stages):
    r = named(store)
    for _ in range(stages):
        r = say(store, "беру", r.session_id)
    stage, cart = r.stage, r.cart
    assert stage == ("assemble", "checkout")[stages - 1]
    r = say(store, text, r.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to) == (date(2026, 12, 3), date(2026, 12, 8))
    assert r.stage == stage and r.cart == cart and not r.voucher


def test_no_shift_means_no_refused_shift_line(store):
    first = named(store, "с 27 декабря по 3 января")
    for text in ("не в январе, бюджет 100 тысяч", "январь не подходит, бюджет 100 тысяч"):
        r = say(store, text, first.session_id)
        s = r.plan.spec
        assert (s.date_from, s.date_to) == (date(2026, 12, 27), date(2027, 1, 3))
        assert s.budget_rub == 100_000 and "Сдвиг не применён" not in r.reply


@pytest.mark.parametrize(
    "text",
    [
        "в январе, не в феврале",
        "в январе а не в феврале",
        "январь, а не декабрь",
        "лучше в январе, но не дорого",
        "в январе, но не поедем на самолёте",
        "в январе, не дороже 100 тысяч",
        "в январе и не дороже 100 тысяч",
        "в январе и подешевле",
    ],
)
def test_a_negated_neighbour_after_the_month_is_still_a_move(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    s = r.plan.spec
    assert (s.date_from, s.date_to, s.month) == (date(2027, 1, 3), date(2027, 1, 8), 1)
    assert "Они остаются" not in r.reply and r.stage == "compare"


@pytest.mark.parametrize("text", ["в январе, не летом", "в январе, не надолго"])
def test_a_stem_of_a_refusal_verb_does_not_catch_other_words(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3) and "Они остаются" not in r.reply


@pytest.mark.parametrize(
    "text",
    [
        "в январе",
        "лучше в январе",
        "в январе, нет проблем",
        "лучше в январе, детей нет, двое взрослых",
        "лучше в январе, в декабре никак",
        "в январе, в декабре нет",
        "в январе, багажа нет",
        "в январе, неудобно в декабре",
        "в январе, отменяется только завтрак",
        "январь или никак",
        "в январе или никак",
        "только январь или никак",
        "январь либо никак",
        "лучше в январе декабрь нет",
        "давай январь декабрь никак",
        "переносим на январь декабрь отменяется",
        "январь подходит декабрь отменяется",
    ],
)
def test_a_plain_request_still_moves(store, text):
    first = named(store)
    r = say(store, text, first.session_id)
    assert r.plan.spec.date_from == date(2027, 1, 3)


@pytest.mark.parametrize(
    "text",
    [
        "январь нет, подтверждаю",
        "январь отменяется, беру",
        "в январе никак, оформляй",
        "в январе, не летаем, подтверждаю",
        "в январе, не поедешь, подтверждаю",
        "январь, увы, нельзя, подтверждаю",
    ],
)
@pytest.mark.parametrize("stages", [1, 2])
def test_a_bare_no_is_no_consent_at_assemble_and_checkout(store, text, stages):
    r = named(store)
    for _ in range(stages):
        r = say(store, "беру", r.session_id)
    stage, cart = r.stage, r.cart
    r = say(store, text, r.session_id)
    assert (r.plan.spec.date_from, r.plan.spec.date_to) == (date(2026, 12, 3), date(2026, 12, 8))
    assert r.stage == stage and r.cart == cart and not r.voucher
