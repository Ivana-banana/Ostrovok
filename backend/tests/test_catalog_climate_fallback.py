"""TRAP-6: climate service failures must not discard a geocoded city (#118)."""

from urllib.error import HTTPError

import pytest

from api import answers, catalog, checks
from api.contracts import ChatRequest, Plan, TripSpec
from api.inspire import NO_SEA, _rule_breaks
from api.model import chat
from api.store import SessionStore


def test_trap_6_archive_rate_limit_keeps_city_and_marine(geo, monkeypatch, tmp_path):
    base = geo("Блед")

    def fetch(url, params):
        if url == catalog.ARCHIVE:
            raise HTTPError(url, 429, "Too Many Requests", {}, None)
        if url == catalog.MARINE:
            return {
                "latitude": 46.4,
                "longitude": 14.1,
                "daily": {
                    "time": ["2025-07-15"],
                    "sea_surface_temperature_mean": [24.0],
                },
            }
        return base(url, params)

    place = catalog.lookup_online("Блед", fetch)

    assert place is not None
    assert (place["name"], place["lat"], place["lon"], place["tz"]) == (
        "Блед",
        46.4,
        14.1,
        "Europe/Ljubljana",
    )
    assert place["airport"]["iata"]
    assert place["sea"] is True
    assert catalog.month_climate(place, 7) == (None, 24.0)
    assert not catalog.ONLINE_CACHE.exists()
    reply = answers.weather_fact(
        Plan(spec=TripSpec(destinations=["Блед"], month=7)), "какая вода в июле?"
    )
    assert "вода 24 °C" in reply
    assert "днём" not in reply
    assert "температура воздуха и осадки неизвестны" in reply
    monkeypatch.setattr(catalog, "_get", fetch)
    store = SessionStore(tmp_path / "sessions.db")
    start = chat(ChatRequest(session_id=None, message="Хочу в Блед в июле"), store)
    answer = chat(ChatRequest(session_id=start.session_id, message="какая вода в июле?"), store)
    assert "вода 24 °C" in answer.reply
    assert "днём" not in answer.reply
    assert answer.plan == start.plan and answer.stage == start.stage


@pytest.mark.parametrize("failed", [(catalog.MARINE,), (catalog.ARCHIVE, catalog.MARINE)])
def test_trap_6_partial_city_reaches_purchase_with_unknown_sea(geo, monkeypatch, tmp_path, failed):
    base = geo("Тихвин")

    def fetch(url, params):
        if url in failed:
            raise TimeoutError("climate timeout")
        return base(url, params)

    monkeypatch.setattr(catalog, "_get", fetch)
    store = SessionStore(tmp_path / "sessions.db")
    reply = chat(ChatRequest(session_id=None, message="Хочу в Тихвин в марте"), store)
    assert "Данные о море не загрузились" in reply.reply
    answers = {"inspire": "1", "compare": "беру", "assemble": "да", "checkout": "подтверждаю"}
    for _ in range(8):
        if reply.stage == "booked":
            break
        message = (
            " ".join(q.options[0] for q in reply.questions if q.options)
            if reply.stage == "clarify"
            else answers[reply.stage]
        )
        reply = chat(ChatRequest(session_id=reply.session_id, message=message), store)
    assert reply.stage == "booked" and reply.voucher is not None
    assert reply.plan.spec.destinations == ["Тихвин"]
    place = catalog.lookup_online("Тихвин")
    assert "sea" not in place
    assert "sea" not in place["tags"] and "swim" not in place["tags"]
    assert catalog.month_climate(place, 3) == (
        None if catalog.ARCHIVE in failed else 10.0,
        None,
    )
    breaks = _rule_breaks(TripSpec(wishes=["море"]), place, catalog.resolve("Москва"), 3)
    assert any("Наличие моря неизвестно" in x for x in breaks)
    assert NO_SEA not in breaks
    findings = checks.unseen_findings(
        checks.Run(case="TRAP-6", label="climate failure", replies=[reply]), {"warm_sea": True}
    )
    assert ("тёплое море", checks.UNKNOWN) in [(name, verdict) for name, verdict, _ in findings]
    assert not catalog.ONLINE_CACHE.exists()
