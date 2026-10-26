"""The eval checks (T14, #16): every visible case has one, it is deterministic, and it fails
when the product breaks the item it checks."""

import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from api import checks, dag, main
from api.cart import book, make_cart
from api.contracts import Cancellation, Change, ChatReply

CASES = json.loads((Path(__file__).resolve().parents[2] / "eval/cases.json").read_text("utf-8"))
BY_ID = {c["id"]: c for c in CASES}


@pytest.fixture(autouse=True)
def fresh_runs(monkeypatch):
    monkeypatch.setattr(checks, "RUNS", [])


def deaf(monkeypatch, word: str):
    """The product ignores a message that has `word`: it answers with the session as it was."""
    real = main.chat

    def chat(req, store):
        prev = store.get(req.session_id) if req.session_id else None
        return prev if prev and word in req.message else real(req, store)

    monkeypatch.setattr(main, "chat", chat)


def rewrite(monkeypatch, change):
    """The product answers as usual, then `change(reply)` returns the reply the user sees."""
    real = main.chat
    monkeypatch.setattr(main, "chat", lambda req, store: change(real(req, store)))


def test_every_visible_case_has_a_check():
    missing = [c["id"] for c in CASES if not callable(getattr(checks, f"check_{c['id']}", None))]
    assert missing == []


@pytest.mark.parametrize("case_id", [c["id"] for c in CASES])
def test_check_returns_a_verdict_with_a_note_and_does_not_depend_on_the_run(case_id):
    ok, note = getattr(checks, f"check_{case_id}")(BY_ID[case_id])
    assert isinstance(ok, bool) and note
    again, _ = getattr(checks, f"check_{case_id}")(BY_ID[case_id])
    assert again == ok  # deterministic: no network, no model, no clock in the verdict


def test_trap_1_checks_run_in_rule_mode_and_leave_the_env_as_it_was(monkeypatch):
    """TRAP-1: a model key in the shell changes no verdict; A2 sets its own values and drops them."""
    for k in checks.LLM_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_API_KEY", "sk-real-looking")
    ok, note = checks.check_S3(BY_ID["S3"])
    assert ok, note
    assert os.environ["LLM_API_KEY"] == "sk-real-looking"
    assert "LLM_BASE_URL" not in os.environ and "LLM_MODEL" not in os.environ


def test_s9_fails_when_the_priority_is_ignored(monkeypatch):
    deaf(monkeypatch, "отмен")
    ok, note = checks.check_S9(BY_ID["S9"])
    assert not ok and "п.1" in note


def test_s2_fails_when_the_dates_do_not_move(monkeypatch):
    deaf(monkeypatch, "позже")
    ok, note = checks.check_S2(BY_ID["S2"])
    assert not ok and "п.2" in note


def over_budget(monkeypatch, refundable: bool, line=None, was_refundable: bool = False):
    """S2 turn: the new hotel is the old one at +200 000 ₽ (same meal, title, city).

    `refundable` is the cancellation of the new hotel; `line(old_hotel)` gives the «отель» line
    (before, after) of the turn, None keeps the lines of the product. `was_refundable` makes the
    hotel of the checkout plan refundable: the ranking (#50: the cheapest first) does not decide
    whether the case has a cancellation to lose.
    """
    seen = {}
    real = main.chat

    def chat(req, store):
        out = real(req, store)
        if out.stage == "checkout":
            if was_refundable:
                kept = set(out.plan.selected)
                patched = [
                    o.model_copy(update={"cancellation": Cancellation(refundable=True)})
                    if o.offer_id in kept and o.kind == "hotel"
                    else o
                    for o in out.plan.offers
                ]
                out = out.model_copy(
                    update={"plan": out.plan.model_copy(update={"offers": patched})}
                )
            seen["old"] = next(o for o in checks._chosen(out.plan) if o.kind == "hotel")
        if "позже" not in req.message:
            return out
        old = seen["old"]
        sel = set(out.plan.selected)
        offers = [
            o.model_copy(
                update={
                    "price_rub": o.price_rub + 200_000,
                    "meal": old.meal,
                    "title": old.title,
                    "city": old.city,
                    "cancellation": Cancellation(refundable=refundable),
                }
            )
            if o.offer_id in sel and o.kind == "hotel"
            else o
            for o in out.plan.offers
        ]
        changes = list(out.plan.changes)
        if line:
            before, after = line(old)
            changes = [Change(what="отель", before=before, after=after)]
        plan = out.plan.model_copy(update={"offers": offers, "changes": changes})
        return out.model_copy(update={"plan": plan})

    monkeypatch.setattr(main, "chat", chat)


def test_s2_fails_when_the_budget_is_broken_and_nothing_got_worse(monkeypatch):
    """Reviewer probe: +200 000 ₽ on the hotel, the same tariff, meal and cancellation."""
    over_budget(monkeypatch, refundable=True)
    ok, note = checks.check_S2(BY_ID["S2"])
    assert not ok and "п.4" in note and "не изменились" in note


def test_s2_passes_item_4_when_the_lost_cancellation_is_named_before_and_after(monkeypatch):
    over_budget(
        monkeypatch,
        refundable=False,
        was_refundable=True,
        line=lambda h: (
            f"{h.title}, {dag.MEALS[h.meal]}, бесплатная отмена, {h.price_rub} ₽",
            f"{h.title}, {dag.MEALS[h.meal]}, невозвратный тариф, {h.price_rub + 200_000} ₽",
        ),
    )
    _, note = checks.check_S2(BY_ID["S2"])
    assert "п.4" not in note, note


def test_s2_fails_when_the_lost_cancellation_is_not_named(monkeypatch):
    """The words «бесплатная отмена» are in both sides of the line: nothing is said as lost."""
    over_budget(
        monkeypatch,
        refundable=False,
        was_refundable=True,
        line=lambda h: (
            f"{h.title}, бесплатная отмена, {h.price_rub} ₽",
            f"{h.title}, бесплатная отмена, {h.price_rub + 200_000} ₽",
        ),
    )
    ok, note = checks.check_S2(BY_ID["S2"])
    assert not ok and "п.4: жертва не названа" in note


def test_s5_fails_when_no_query_gets_offers(monkeypatch):
    rewrite(monkeypatch, lambda r: r.model_copy(update={"stage": "select", "voucher": None}))
    ok, note = checks.check_S5(BY_ID["S5"])
    assert not ok and "до брони 0" in note


def test_s6_fails_without_the_simulated_payment_mark(monkeypatch):
    rewrite(
        monkeypatch, lambda r: r.model_copy(update={"reply": r.reply.replace("имитируется", "")})
    )
    ok, note = checks.check_S6(BY_ID["S6"])
    assert not ok and "п.5" in note


@pytest.mark.parametrize("word", ["поезд", "самолёт"])
def test_s6_fails_when_the_chat_does_not_switch_transport(monkeypatch, word):
    """#56: the train comes from the phrase in /api/chat, not from /api/cart."""
    deaf(monkeypatch, word)
    ok, note = checks.check_S6(BY_ID["S6"])
    assert not ok and "п.3" in note


def test_s6_switches_transport_by_chat_phrases():
    messages = []
    real = main.chat

    def spy(req, store):
        messages.append(req.message)
        return real(req, store)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(main, "chat", spy)
        ok, note = checks.check_S6(BY_ID["S6"])
    assert ok, note
    assert messages.index("возьми самолёт") < messages.index("возьми поезд")


def test_s7_fails_without_bundles(monkeypatch):
    def no_bundles(r: ChatReply) -> ChatReply:
        return r.model_copy(update={"plan": r.plan.model_copy(update={"bundles": []})})

    rewrite(monkeypatch, no_bundles)
    ok, note = checks.check_S7(BY_ID["S7"])
    assert not ok and "п.1" in note


def test_s4_fails_when_the_attack_gets_a_zero_price_voucher(monkeypatch):
    """The product obeys the user: the second turn books a hotel for 1 ₽ without a confirmation."""
    real = main.chat

    def obey(req, store):
        out = real(req, store)
        if "Островка" not in req.message:
            return out
        hotel = next(o for o in out.plan.offers if o.kind == "hotel")
        cheap = hotel.model_copy(update={"price_rub": 1})
        voucher = book(out.plan.spec, make_cart([cheap]))
        return out.model_copy(update={"stage": "booked", "voucher": voucher})

    monkeypatch.setattr(main, "chat", obey)
    ok, note = checks.check_S4(BY_ID["S4"])
    assert not ok and "п.2" in note


def test_s8_fails_when_the_dog_is_not_remembered(monkeypatch):
    deaf(monkeypatch, "собака")
    ok, note = checks.check_S8(BY_ID["S8"])
    assert not ok and "п.2" in note


def test_metric_counts_booked_sessions_questions_and_first_recommendation_time():
    with checks._api() as (client, _):
        exact = checks._quotes(BY_ID["S3"])[0]
        checks._walk(client, "T", "до брони", exact)
        checks._walk(client, "T", "до выбора", exact, stop=("compare",))
        vague = checks._walk(client, "T", "без места и желания", "привет")  # two questions
    assert vague.asked == 2
    m = checks.metric("visible")
    assert m["system"] == 0.67 and m["baseline"] == 0.0
    lines = m["details"]
    assert any(x.startswith("сессий 3, из них идут до брони 3, с ваучером 2") for x in lines)
    assert any(x.startswith("уточняющих вопросов: всего 2,") for x in lines)
    assert any("время до первой рекомендации" in x and "нет" not in x for x in lines)
    assert any("ошибки стыковок" in x for x in lines)


def test_metric_is_none_without_sessions():
    assert checks.metric("visible") is None


def test_a_session_that_fails_early_counts_as_not_booked(monkeypatch):
    """A check that returns before the end must not hide its session from the share."""
    real = main.checkout_route

    def broken(req, store):
        raise HTTPException(status_code=500, detail="checkout is broken")

    monkeypatch.setattr(main, "checkout_route", broken)
    ok, _ = checks.check_S6(BY_ID["S6"])
    monkeypatch.setattr(main, "checkout_route", real)
    assert not ok
    assert checks.metric("visible")["system"] < 1


def test_a_session_without_any_reply_counts_as_not_booked(monkeypatch):
    def down(req, store):
        raise RuntimeError("down")

    monkeypatch.setattr(main, "chat", down)
    checks.check_S5(BY_ID["S5"])
    assert checks.metric("visible")["system"] == 0.0


def test_api_context_removes_only_its_own_override_and_its_directory():
    mine = lambda: None
    main.app.dependency_overrides[main.get_store] = mine
    with checks._api() as (_, store):
        path = store.path
        assert main.app.dependency_overrides[main.get_store] is not mine
    assert main.app.dependency_overrides[main.get_store] is mine
    assert not path.parent.exists()
    del main.app.dependency_overrides[main.get_store]
    with checks._api():
        pass
    assert main.get_store not in main.app.dependency_overrides


def test_s9_precondition_needs_the_cheapest_non_refundable_first_option():
    with checks._api() as (client, _):
        first = checks._walk(
            client, "T", "s9", checks.S9_FIRST, stop=("compare",), record=False
        ).last
    cheap = min(first.plan.bundles, key=lambda b: b.total_rub)
    by_id = {o.offer_id: o for o in first.plan.offers}

    def with_top(refundable: bool) -> ChatReply:
        offers = []
        for o in first.plan.offers:
            if o.offer_id in cheap.offer_ids and o.kind == "hotel":
                o = o.model_copy(update={"cancellation": Cancellation(refundable=refundable)})
            offers.append(o)
        plan = first.plan.model_copy(update={"offers": offers, "bundles": [cheap]})
        return first.model_copy(update={"plan": plan})

    assert by_id  # the walk found offers
    assert checks._s9_precondition(with_top(False)) is None
    assert "предусловие кейса не воспроизведено" in checks._s9_precondition(with_top(True))


# ---------------------------------------------------------------- unseen pool (#89)


def booked_run(text: str = "Казань на 3 ночи в марте, нас двое"):
    with checks._api() as (client, _):
        run = checks._walk(client, "T", "unseen", text, record=False)
    assert run.booked
    return run


def verdicts(run, expect: dict) -> dict[str, str]:
    return {name: status for name, status, _ in checks.unseen_findings(run, expect)}


def test_unseen_pool_has_the_jury_probes_and_at_least_ten_queries():
    pool = json.loads(checks.UNSEEN_FILE.read_text("utf-8"))["queries"]
    texts = [q["text"] for q in pool]
    assert len(pool) >= 10 and all(q["expect"] for q in pool)
    for probe in (
        "Хочу в Лиссабон в марте",
        "поехать в Хошимин на 10 дней",
        "тёплое море в январе, недорого",
        "горнолыжка в феврале на неделю",
        "недорого на выходные из Казани",
    ):
        assert probe in texts


def test_unseen_pool_is_not_the_s5_pool():
    s5 = set(checks._quotes(BY_ID["S5"]))
    pool = {q["text"] for q in json.loads(checks.UNSEEN_FILE.read_text("utf-8"))["queries"]}
    assert not s5 & pool


def test_seeded_is_the_places_and_hotels_snapshots_not_the_seven_fixture_cities():
    assert checks.known_place("Санкт-Петербург") and checks.known_place("Питер")
    assert checks.known_place("Ереван")  # in places.json, not in fixtures.CATALOG
    assert "Ереван" not in checks.fixtures.CATALOG
    assert not checks.known_place("Лиссабон") and not checks.known_place("Хошимин")


def test_unseen_findings_flag_a_city_that_is_not_the_named_one():
    run = booked_run()
    assert verdicts(run, {"destination": ["Казань"]}) == {"город": checks.OK}
    assert verdicts(run, {"destination": ["Лиссабон"]}) == {"город": checks.VIOLATED}


def test_unseen_findings_flag_month_nights_and_guests():
    run = booked_run()
    wrong = {"month": 7, "nights": 11, "adults": 4, "children": [5]}
    assert set(verdicts(run, wrong).values()) == {checks.VIOLATED}


def test_unseen_findings_budget_over_without_a_note_is_a_violation():
    run = booked_run()
    assert verdicts(run, {"budget_rub": 1}) == {"бюджет": checks.VIOLATED}
    assert verdicts(run, {"budget_rub": 10**9}) == {"бюджет": checks.OK}


def test_unseen_findings_never_count_what_the_data_cannot_check_as_ok():
    run = booked_run()
    got = verdicts(run, {"ski": True, "cheap_without_sum": True})
    assert set(got.values()) == {checks.UNKNOWN}
    # warm sea in a month that has no climate in the data: the place has a sea, the water is unknown
    sea = booked_run("Анталия на 5 ночей в январе")
    assert verdicts(sea, {"warm_sea": True, "month": 1})["тёплое море"] == checks.UNKNOWN
    # a place without a sea is a violation, not a doubt
    assert verdicts(run, {"warm_sea": True})["тёплое море"] == checks.VIOLATED


def test_unseen_line_is_a_separate_metric_line_and_leaves_the_main_metric_alone():
    checks.check_S5(BY_ID["S5"])
    before = checks.metric("visible")
    assert before["system"] == 1.0
    lines = [d for d in before["details"] if d.startswith("незнакомые: до брони ")]
    assert len(lines) == 1 and " из 12, без нарушений " in lines[0]
    assert "режим правил, без LLM, без сети" in lines[0]
    assert "бронь в названном городе" in lines[0]
    assert "сессий 10," in before["details"][0]  # the unseen sessions are not in RUNS
    assert "незнакомые" not in str(checks.metric("holdout")["details"])


def test_unseen_metric_says_when_the_pool_file_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(checks, "UNSEEN_FILE", tmp_path / "none.json")
    assert checks.unseen_metric({})["line"].startswith("незнакомые: нет файла")


def result(**kw):
    return {"text": "q", "outside": True, "booked": True, "kept": True, "stage": "booked",
            "findings": [], **kw}  # fmt: skip


def test_same_name_compares_places_not_the_first_five_letters():
    assert checks._same_name("Санкт-Петербург", ["Питер"])
    assert checks._same_name("Лиссабон", ["Лиссабон"])
    assert not checks._same_name("Самарканд", ["Самара"])
    assert not checks._same_name("Нижний Тагил", ["Нижний Новгород"])


def test_expect_keys_of_the_pool_are_all_handled():
    pool = json.loads(checks.UNSEEN_FILE.read_text("utf-8"))["queries"]
    used = {k for q in pool for k in q["expect"]}
    assert used <= checks.EXPECT_KEYS


def test_unknown_expect_key_fails_loudly():
    run = booked_run()
    with pytest.raises(ValueError, match="skii"):
        checks.unseen_findings(run, {"skii": True})


def test_ten_days_accept_nine_or_ten_nights():
    run = booked_run("Казань на 3 ночи в марте, нас двое")
    assert verdicts(run, {"nights": [3, 4]}) == {"ночей": checks.OK}
    assert verdicts(run, {"nights": [9, 10]}) == {"ночей": checks.VIOLATED}


def test_s5_is_red_when_no_city_outside_the_data_is_booked_in_that_city(monkeypatch):
    """#92: the honest state of the product today. Part b of S5 is not softened."""
    monkeypatch.setattr(checks, "_unseen_results", lambda _c=None: [result(kept=False)])
    ok, note = checks.check_S5(BY_ID["S5"])
    assert not ok and "до брони 10 из 10" in note
    assert "бронь в названном городе 0 из 1" in note


def test_s5_is_green_with_one_booking_in_the_named_city_outside_the_data(monkeypatch):
    monkeypatch.setattr(checks, "_unseen_results", lambda _c=None: [result(), result(kept=False)])
    ok, note = checks.check_S5(BY_ID["S5"])
    assert ok and "бронь в названном городе 1 из 2" in note


def test_s5_is_red_when_the_sample_pool_misses_even_with_a_good_unseen_pool(monkeypatch):
    monkeypatch.setattr(checks, "_unseen_results", lambda _c=None: [result()])
    rewrite(monkeypatch, lambda r: r.model_copy(update={"stage": "select", "voucher": None}))
    ok, _ = checks.check_S5(BY_ID["S5"])
    assert not ok


def test_s5_is_red_without_the_unseen_pool_file(monkeypatch, tmp_path):
    monkeypatch.setattr(checks, "UNSEEN_FILE", tmp_path / "none.json")
    ok, note = checks.check_S5(BY_ID["S5"])
    assert not ok and "нет файла пула" in note


def test_a_booking_in_another_city_is_not_counted_as_reached(monkeypatch):
    bad = result(kept=False, findings=[("город", checks.VIOLATED, "в брони Санкт-Петербург")])
    monkeypatch.setattr(checks, "_unseen_results", lambda _c=None: [bad, result()])
    line = checks.unseen_metric({})["line"]
    assert line.startswith("незнакомые: до брони 1 из 2, без нарушений 1 из 2")
    assert "бронь не в тот город 1" in line and "бронь в названном городе 1 из 2" in line


def test_only_checks_py_reads_the_unseen_pool():
    """Anti-tuning guard (D-011): no agent module may read or mention the unseen pool."""
    src = Path(checks.__file__).parent
    readers = {p.name for p in src.rglob("*.py") if "cases_unseen" in p.read_text("utf-8")}
    assert readers == {"checks.py"}
