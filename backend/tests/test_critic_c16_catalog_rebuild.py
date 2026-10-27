"""#386: C16 keeps the named country; catalog rebuild retains the C10 ferry route."""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from api import catalog
from api.contracts import ChatRequest
from api.extract import extract
from api.model import chat
from api.store import SessionStore

C16 = "Мальдивы на неделю вдвоём в декабре, бюджет 50 тысяч на всё"


@pytest.mark.parametrize("text", [C16, C16.lower(), "  " + C16])
def test_C16_TRAP_3_country_at_message_start_is_a_destination(text):
    found, _ = extract(text)
    assert found["destinations"] in (["Мальдивы"], ["Мале"])
    assert found["month"] == 12 and found["budget_rub"] == 50_000


def test_C16_TRAP_1_offline_reply_explains_the_named_trip_is_over_budget(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GIGACHAT_AUTH_KEY", raising=False)
    store = SessionStore(tmp_path / "sessions.db")
    response = chat(ChatRequest(message=C16), store)
    assert response.plan.spec.destinations in (["Мальдивы"], ["Мале"])
    if response.stage == "clarify":
        response = chat(ChatRequest(session_id=response.session_id, message="из Москвы"), store)
    assert response.stage == "compare"
    assert "Мальдив" in response.reply or "Мале" in response.reply
    assert "не укладывается" in response.reply or "больше бюджета" in response.reply
    assert re.search(r"\d[\d ]+ ₽", response.reply)
    assert "Воронеж" not in response.reply


@pytest.mark.parametrize(
    "roads", [{}, {"Хужир": {"IKT": 256}}, {"Хужир": {"UUD": 156, "IKT": 256}}]
)
def test_C10_TRAP_6_rebuild_preserves_irkutsk_road_and_ferry(roads):
    spec = importlib.util.spec_from_file_location(
        "build_catalog", Path(__file__).parents[2] / "scripts" / "build_catalog.py"
    )
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    place = catalog.resolve("Хужир")
    row = {"ru": place["name"], **{k: place[k] for k in ("lat", "lon", "cc")}}
    ports = [a + ["RU"] for a in catalog.snapshot()["airports"] if a[0] in ("UUD", "IKT")]
    rebuilt = build.airport_fields(row, ports, roads, None)
    assert rebuilt["airport"] == place["airport"]
    assert rebuilt["airport_km"] == 256


def test_C10_TRAP_6_airports_only_rebuild_retains_ferry(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "build_catalog", Path(__file__).parents[2] / "scripts" / "build_catalog.py"
    )
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    place = catalog.resolve("Хужир")
    snapshot = {
        "_source": "",
        "countries": {"Россия": ["россия"]},
        "places": [place],
        "airports": [a for a in catalog.snapshot()["airports"] if a[0] in ("UUD", "IKT")],
        "roads": {},
    }
    out = tmp_path / "catalog.json"
    out.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    csv = tmp_path / "airports.csv"
    csv.write_text(
        "scheduled_service,iata_code,iso_country\nyes,UUD,RU\nyes,IKT,RU\n", encoding="utf-8"
    )
    monkeypatch.setattr(build, "download", lambda *_: csv)
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_catalog.py", "--data", str(tmp_path), "--out", str(out), "--airports-only"],
    )
    build.main()
    rebuilt = json.loads(out.read_text(encoding="utf-8"))["places"][0]
    assert rebuilt["airport"] == place["airport"]
    assert rebuilt["airport_km"] == 256
