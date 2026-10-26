"""The eval checks (T14, #16): every visible case has one, it is deterministic, and it fails
when the product breaks the item it checks."""

import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from api import checks, main
from api.cart import book, make_cart
from api.contracts import Cancellation, ChatReply

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
