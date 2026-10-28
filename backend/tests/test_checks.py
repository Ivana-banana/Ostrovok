"""The eval checks (T14, #16): every visible case has one, it is deterministic, and it fails
when the product breaks the item it checks."""

import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from api import catalog, checks, dag, fixtures, inspire, main, model
from api.cart import book, make_cart
from api.contracts import Cancellation, Change, ChatReply, Destination
from api.offers import find_offers

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


def test_trap_6_eval_runs_without_the_live_hotel_layer(monkeypatch):
    """TRAP-6, #539: api.main wraps HOTELS in Live101Hotels (LIVE_HOTELS=1); eval takes it off."""
    from api import offers
    from api.live_hotels import Live101Hotels

    base = offers.HOTELS
    monkeypatch.setattr(offers, "HOTELS", Live101Hotels(Live101Hotels(base)))
    with checks._api():
        assert offers.HOTELS is base
    assert isinstance(offers.HOTELS, Live101Hotels)  # restored after the run


def test_trap_1_s3_a2_really_asks_the_model_and_gets_401(monkeypatch):
    """TRAP-1, #490: eval asks for the model (ChatRequest.llm), so A2 goes through the 401 path."""
    calls = []
    real = checks._unauthorized

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(checks, "_unauthorized", counted)
    ok, note = checks.check_S3(BY_ID["S3"])
    assert ok, note
    assert calls, "A2 never called the model: the check of an invalid key is empty"


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


def recorded_answers(monkeypatch, tmp_path):
    """S5: a file of recorded geocoder answers exists, so part b is checked, not skipped."""
    (tmp_path / "answers.json").write_text('{"answers": {}}', "utf-8")
    monkeypatch.setattr(checks, "GEOCODER_FILE", tmp_path / "answers.json")


def test_s5_fails_when_no_query_gets_offers(monkeypatch, tmp_path):
    recorded_answers(monkeypatch, tmp_path)
    rewrite(monkeypatch, lambda r: r.model_copy(update={"stage": "select", "voucher": None}))
    ok, note = checks.check_S5(BY_ID["S5"])
    assert not ok and "до брони 0" in note and "не проверено" not in note


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
    # «привет» asks 2; the exact request of S3 names no origin: 1 question in each walk (#436)
    assert any(x.startswith("уточняющих вопросов: всего 4,") for x in lines)
    assert any("время до первой рекомендации" in x and "нет" not in x for x in lines)
    assert any("ошибки стыковок" in x for x in lines)


def test_walk_notes_a_stage_without_a_scripted_answer_instead_of_raising(monkeypatch):
    """#79: stage «select» has no scripted answer; stop=("compare",) must not raise KeyError."""
    rewrite(monkeypatch, lambda r: r.model_copy(update={"stage": "select"}))
    with checks._api() as (client, _):
        exact = checks._quotes(BY_ID["S3"])[0]
        run = checks._walk(client, "T", "select", exact, stop=("compare",))
    assert run.last.stage == "select" and len(run.replies) == 1 and not run.booked
    assert run.note.startswith("чат не довёл до «compare»") and "«select»" in run.note
    assert any("чат не довёл до" in x for x in checks.metric("visible")["details"])


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


def test_api_context_follows_api_main_after_a_reload(monkeypatch):
    """#442: a test before this one reloads api.main (cors_app of test_partner_api.py). The app
    and get_store are new objects then: checks._api must override the store of the new app,
    not of the old names it imported, or the test above fails by the run order."""
    from fastapi import FastAPI

    monkeypatch.setattr(main, "app", FastAPI())
    monkeypatch.setattr(main, "get_store", lambda: None)
    test_api_context_removes_only_its_own_override_and_its_directory()


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
    assert checks.known_place("Ереван")  # in catalog.json, not in fixtures.CATALOG
    assert "Ереван" not in fixtures.CATALOG
    assert not checks.known_place("Тихвин") and not checks.known_place("Зурбаган")


def test_a_place_the_geocoder_found_is_not_seeded():
    """D-017: only the snapshot is seeded; an online place is the agent's work, not our data."""
    checks.catalog._online[checks.catalog.norm("Зурбаган")] = {
        "name": "Зурбаган",
        "source": "online",
    }
    assert checks.resolve_place("Зурбаган") and not checks.known_place("Зурбаган")


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


def test_unseen_findings_never_count_what_the_data_cannot_check_as_ok(monkeypatch):
    run = booked_run()
    assert set(verdicts(run, {"cheap_without_sum": True}).values()) == {checks.UNKNOWN}
    # January water of Анталия in catalog.json is 19.5 °C: a violation, not a doubt
    sea = booked_run("Анталия на 5 ночей в январе")
    assert verdicts(sea, {"warm_sea": True, "month": 1})["тёплое море"] == checks.VIOLATED
    # warm sea in a month without the water in the data (a geocoder place): unknown, never OK
    gap = dict(catalog.resolve("Анталия"), source="online")
    gap["climate"] = dict(gap["climate"], sea=[None, *gap["climate"]["sea"][1:]])
    monkeypatch.setattr(checks, "resolve_place", lambda _name: gap)
    assert verdicts(sea, {"warm_sea": True, "month": 1})["тёплое море"] == checks.UNKNOWN
    # a place without a sea is a violation, not a doubt
    assert verdicts(run, {"warm_sea": True})["тёплое море"] == checks.VIOLATED


def test_unseen_findings_check_ski_by_the_tag_of_the_booked_place(monkeypatch):
    assert verdicts(booked_run(), {"ski": True}) == {"лыжи": checks.VIOLATED}  # Казань
    resort = booked_run("Красная Поляна на 3 ночи в феврале, нас двое")
    assert verdicts(resort, {"ski": True}) == {"лыжи": checks.OK}
    monkeypatch.setattr(checks, "resolve_place", lambda _name: None)
    assert verdicts(resort, {"ski": True}) == {"лыжи": checks.UNKNOWN}  # never OK without data


def test_unseen_line_is_a_separate_metric_line_and_leaves_the_main_metric_alone():
    checks.check_S5(BY_ID["S5"])
    before = checks.metric("visible")
    assert before["system"] == 1.0
    lines = [d for d in before["details"] if d.startswith("незнакомые: до брони ")]
    assert len(lines) == 1 and " из 22, без нарушений " in lines[0]
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


def test_same_name_takes_a_place_from_the_geocoder(geo):
    """#107: unseen_findings fell with KeyError 'slug' on a city outside catalog.json."""
    catalog.lookup_online("Блед", geo("Блед"))
    assert checks._same_name("Блед", ["Блед"])
    assert not checks._same_name("Блед", ["Москва"])


def test_s5_is_red_when_no_city_outside_the_data_is_booked_in_that_city(monkeypatch, tmp_path):
    """#92: the honest state of the product today. Part b of S5 is not softened."""
    recorded_answers(monkeypatch, tmp_path)
    monkeypatch.setattr(checks, "_unseen_results", lambda _c=None: [result(kept=False)])
    ok, note = checks.check_S5(BY_ID["S5"])
    assert not ok and "до брони 10 из 10" in note
    assert "бронь в названном городе 0 из 1" in note and "нужна хотя бы одна" in note


def test_s5_is_green_with_one_booking_in_the_named_city_outside_the_data(monkeypatch, tmp_path):
    recorded_answers(monkeypatch, tmp_path)
    monkeypatch.setattr(checks, "_unseen_results", lambda _c=None: [result(), result(kept=False)])
    ok, note = checks.check_S5(BY_ID["S5"])
    assert ok and "бронь в названном городе 1 из 2" in note


def test_s5_b_is_not_checked_without_recorded_geocoder_answers(monkeypatch, tmp_path):
    """TRAP-6: eval has no network; without recorded answers part b is not a pass, even booked."""
    monkeypatch.setattr(checks, "GEOCODER_FILE", tmp_path / "none.json")
    monkeypatch.setattr(checks, "_unseen_results", lambda _c=None: [result()])
    ok, note = checks.check_S5(BY_ID["S5"])
    assert not ok and "не проверено" in note


def test_s5_is_red_when_the_sample_pool_misses_even_with_a_good_unseen_pool(monkeypatch, tmp_path):
    recorded_answers(monkeypatch, tmp_path)
    monkeypatch.setattr(checks, "_unseen_results", lambda _c=None: [result()])
    rewrite(monkeypatch, lambda r: r.model_copy(update={"stage": "select", "voucher": None}))
    ok, note = checks.check_S5(BY_ID["S5"])
    assert not ok and "до брони 0" in note and "не проверено" not in note


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
    """Anti-tuning guard (D-011): no agent, script or web file may read or mention the pool."""
    root = Path(checks.__file__).resolve().parents[3]
    places = [root / "backend/src", root / "scripts", root / "web/src"]
    suffixes = {".py", ".sh", ".ts", ".tsx", ".js", ".json", ".mk"}
    readers = {
        p.relative_to(root).as_posix()
        for base in places
        for p in base.rglob("*")
        if p.is_file() and p.suffix in suffixes and "cases_unseen" in p.read_text("utf-8")
    }
    assert readers == {"backend/src/api/checks.py"}


def test_over_budget_note_needs_the_optimizer_phrase_not_two_loose_words():
    run = booked_run()
    loose = run.last.model_copy(update={"reply": "бюджет у нас выше, чем вы думаете"})
    assert not checks._over_budget_with_note(loose, 1, 100)
    said = run.last.model_copy(update={"reply": "Не укладывается в бюджет 1 ₽: поездка 100 ₽"})
    assert checks._over_budget_with_note(said, 1, 100)
    assert checks._over_budget_with_note(loose, 100, 100)


def test_sea_temperature_follows_the_booking_month_not_the_asked_one():
    run = booked_run("Анталия на 5 ночей в январе")
    # expect.month says 11, the booking is in January: January water 19.5 °C, not November 23.6 °C
    got = {
        n: (s, why) for n, s, why in checks.unseen_findings(run, {"warm_sea": True, "month": 11})
    }
    assert got["тёплое море"] == (checks.VIOLATED, "вода 19.5 °C")


# ---------------------------------------------------------------- S10


def seven_cities(spec, limit=4, exclude=None):
    """Inspiration before #20: the seven cities of fixtures.CATALOG, no theme, price order."""
    hub, _ = catalog.origin_hub(spec.origin or "Москва")
    out = []
    for name in fixtures.CATALOG:
        place = catalog.resolve(name)
        out.append(
            Destination(
                name=name, country=place["country"], lat=place["lat"], lon=place["lon"],
                why=fixtures.CATALOG[name]["why"], tradeoffs=fixtures.CATALOG[name]["tradeoffs"],
                est_total_rub=fixtures.trip_cost(spec, find_offers(spec, name)),
                travel_hours=fixtures.travel_hours(spec, hub, place),
            )
        )  # fmt: skip
    return sorted(out, key=lambda d: d.est_total_rub)[:limit]


def test_s10_is_green_on_the_catalogue_inspiration():
    ok, note = checks.check_S10(BY_ID["S10"])
    assert ok, note


def test_s10_fails_on_the_inspiration_before_20_seven_cities(monkeypatch):
    """Jury: «в горы летом с собакой» gave Казань, Петербург, Калининград (#20)."""
    monkeypatch.setattr(model, "suggest_destinations", seven_cities)
    ok, note = checks.check_S10(BY_ID["S10"])
    assert not ok and "п.2" in note and "mountains" in note, note


def test_s10_fails_when_a_card_that_breaks_the_request_stands_above_a_fitting_one(monkeypatch):
    real = inspire.suggest_destinations

    def broken_first(spec, limit=4, exclude=None):
        cards = real(spec, limit, exclude)
        bad = cards[0].model_copy(update={"violates": ["не горнолыжный курорт"]})
        return [bad, *cards[1:]]

    monkeypatch.setattr(model, "suggest_destinations", broken_first)
    ok, note = checks.check_S10(BY_ID["S10"])
    assert not ok and "п.3" in note, note


def test_s10_fails_when_a_card_without_the_theme_has_no_mark(monkeypatch):
    real = inspire.suggest_destinations

    def unmarked(spec, limit=4, exclude=None):
        return [d.model_copy(update={"violates": []}) for d in real(spec, len(catalog.places()))
                if "city" in checks._tags(d.name)][:limit]  # fmt: skip

    monkeypatch.setattr(model, "suggest_destinations", unmarked)
    ok, note = checks.check_S10(BY_ID["S10"])
    assert not ok and "п.2" in note, note


def test_s10_fails_when_the_product_does_not_extract_the_dog(monkeypatch):
    """The dog is fixed by the check, not read from the spec the product extracted."""
    real = model.extract

    def blind(*args, **kwargs):
        found, notes = real(*args, **kwargs)
        return {k: v for k, v in found.items() if k != "pets"}, notes

    monkeypatch.setattr(model, "extract", blind)
    ok, note = checks.check_S10(BY_ID["S10"])
    assert not ok and "п.4" in note, note


def test_s10_fails_when_non_pet_places_are_on_top(monkeypatch):
    real = inspire.suggest_destinations

    def mestia_first(spec, limit=4, exclude=None):
        cards = real(spec, len(catalog.places()), exclude)
        bad = [d.model_copy(update={"violates": []}) for d in cards if d.name == "Местиа"]
        return [*bad, *[d for d in cards if d.name != "Местиа"]][:limit]

    monkeypatch.setattr(model, "suggest_destinations", mestia_first)
    ok, note = checks.check_S10(BY_ID["S10"])
    assert not ok and "п.4" in note and "Местиа" in note, note
