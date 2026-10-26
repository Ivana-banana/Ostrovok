"""Тест на каждую ловушку из eval/traps.json (id в имени теста)."""

from datetime import datetime, timedelta, timezone

import pytest

from api.guards import (
    check_arrival_vs_checkin,
    label_synthetic,
    llm_available,
    normalize_toponym,
    resolve_destination,
    rule_extract,
    unknown_prices,
)

S1 = "Хочу куда-нибудь к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов"


def test_trap_1_no_llm_key_rules_still_parse():
    """TRAP-1."""
    assert llm_available({}) is False
    assert llm_available({"LLM_API_KEY": "  "}) is False
    spec = rule_extract(S1)
    assert spec["month"] == 11 and spec["kids"] and spec["sea"] and spec["cheap"]


def test_trap_2_unknown_destination_goes_to_geocoder():
    """TRAP-2."""
    calls = []

    def geocode(name):
        calls.append(name)
        return {"lat": 41.6, "lon": 41.6}

    got = resolve_destination("Батуми", catalog={}, geocode=geocode)
    assert got["source"] == "geocoder" and calls == ["Батуми"]


def test_trap_3_misspelled_toponym_normalized():
    """TRAP-3."""
    assert normalize_toponym("Анталья") == "Анталия"
    assert normalize_toponym("  antalya ") == "Анталия"
    catalog = {"Анталия": {"lat": 36.9, "lon": 30.7}}
    assert resolve_destination("Анталья", catalog, geocode=lambda n: None)["source"] == "catalog"


def test_trap_4_made_up_price_detected():
    """TRAP-4."""
    offers = [{"offer_id": "h1", "price_rub": 84500}]
    assert unknown_prices("Отель за 84 500 ₽ — лучший вариант", offers) == []
    assert unknown_prices("Отель за 84 500 ₽, перелёт 12 300 руб", offers) == [12300]


def test_trap_5_night_arrival_before_checkin():
    """TRAP-5."""
    msk = timezone(timedelta(hours=3))
    assert check_arrival_vs_checkin(datetime(2026, 12, 11, 7, 10, tzinfo=msk))
    assert check_arrival_vs_checkin(datetime(2026, 12, 11, 15, 0, tzinfo=msk)) == []
    with pytest.raises(ValueError):
        check_arrival_vs_checkin(datetime(2026, 12, 11, 7, 10))


def test_trap_6_network_down_uses_snapshot():
    """TRAP-6."""

    def geocode(name):
        raise OSError("timeout")

    snap = {"Сочи": {"lat": 43.6, "lon": 39.7}}
    assert resolve_destination("sochi", {}, geocode, snapshot=snap)["source"] == "snapshot"
    assert resolve_destination("Тбилиси", {}, geocode, snapshot=snap) is None


def test_trap_7_synthetic_offer_is_labeled():
    """TRAP-7."""
    assert label_synthetic({"offer_id": "h1"})["label"] == "синтетика"
    assert label_synthetic({"offer_id": "h2", "synthetic": False})["synthetic"] is False
