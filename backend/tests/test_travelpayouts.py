"""Real flights from Travelpayouts (#261): mapping to TransportOffer, fallback, cache, gate.

No network: every test passes a fake fetch to the adapter.
"""

import importlib
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from api import model, offers
from api.contracts import TripSpec
from api.offers import SyntheticTransport, find_offers, resolve_place
from api.travelpayouts import TravelpayoutsTransport

SOON = date.today() + timedelta(days=60)
BACK = SOON + timedelta(days=4)
MSK, IST = ZoneInfo("Europe/Moscow"), ZoneInfo("Europe/Istanbul")


def spec(city: str = "Анталия", **kw) -> TripSpec:
    data = {"origin": "Москва", "destinations": [city], "date_from": SOON, "date_to": BACK}
    return TripSpec(nights=4, **{**data, **kw})


def row(src: str, dst: str, at: datetime, price: int = 10735, minutes: int = 275, **kw) -> dict:
    return {"origin_airport": src, "destination_airport": dst, "departure_at": at.isoformat(),
            "price": price, "duration_to": minutes, "transfers": 0, "airline": "PC",
            "flight_number": "1581", **kw}  # fmt: skip


def answer(*rows: dict) -> dict:
    return {"success": True, "currency": "rub", "data": list(rows)}


def at(day: date, h: int, m: int, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz)


THERE = row("VKO", "AYT", at(SOON, 15, 20, MSK))
HOME = row("AYT", "VKO", at(BACK, 20, 5, IST))


class Fake:
    """Answers by direction; an exception instance is raised. Counts the calls."""

    def __init__(self, there=None, back=None):
        self.by_dir = {"MOW-AYT": there or answer(THERE), "AYT-MOW": back or answer(HOME)}
        self.calls: list[str] = []

    def __call__(self, src: str, dst: str, day: date) -> dict:
        self.calls.append(f"{src}-{dst}")
        got = self.by_dir[f"{src}-{dst}"]
        if isinstance(got, Exception):
            raise got
        return got


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    def make(fetch) -> TravelpayoutsTransport:
        tp = TravelpayoutsTransport("token", fetch=fetch, cache_dir=tmp_path)
        monkeypatch.setattr(offers, "TRANSPORT", tp)
        return tp

    return make


def legs(s: TripSpec, city: str = "Анталия"):
    return [o for o in find_offers(s, city) if o.kind == "transport"]


def synthetic(s: TripSpec, city: str = "Анталия"):
    place = offers._complete(resolve_place(city))
    return SyntheticTransport().legs(s, offers._complete(resolve_place(s.origin)), place)


def test_trap4_real_flights_keep_api_prices_zones_and_core_invariants(adapter):
    adapter(Fake())
    s = spec(children_ages=[5])
    got = legs(s)
    there, back = got
    hotels = {o.city for o in find_offers(s, "Анталия") if o.kind == "hotel"}
    assert [x.synthetic for x in got] == [False, False]  # TRAP-7: real legs are not synthetic
    # TRAP-4: the API price x full fares of the party (2 adults + a child of 5 at 0.75)
    assert (there.price_rub, back.price_rub) == (round(10735 * 2.75), round(10735 * 2.75))
    assert there.depart_at == datetime.fromisoformat(THERE["departure_at"])
    assert there.depart_at.tzinfo == MSK and there.arrive_at.tzinfo == IST  # TRAP-5 zones
    assert there.arrive_at - there.depart_at == timedelta(minutes=275)
    assert {there.destination, back.origin} == hotels  # letter by letter (timeline.py)
    assert there.origin == back.destination == s.origin  # there first, back ends at the origin
    assert "рейс PC 1581" in there.title
    assert "детский тариф — оценка" in there.title  # the child share is ours, not the API's


def test_moscow_is_asked_by_its_city_code_and_the_place_by_its_airport(adapter):
    """MOW: the API searches SVO, DME, VKO and ZIA; Анталия stays AYT."""
    fake = Fake()
    adapter(fake)
    legs(spec())
    assert fake.calls == ["MOW-AYT", "AYT-MOW"]


def test_trap6_network_error_gives_synthetic_legs_and_a_pause(adapter):
    fake = Fake(there=OSError("down"), back=OSError("down"))
    adapter(fake)
    s = spec()
    assert legs(s) == synthetic(s)
    assert all(x.synthetic for x in legs(s))  # TRAP-7
    assert fake.calls == ["MOW-AYT"]  # FAILED_S: no request after the first error


def test_trap6_stale_cache_serves_when_the_network_fails(adapter, tmp_path):
    adapter(Fake())
    s = spec()
    legs(s)
    for f in tmp_path.iterdir():
        os.utime(f, (0, 0))  # older than CACHE_S
    fake = Fake(there=OSError("down"), back=OSError("down"))
    adapter(fake)
    assert [x.synthetic for x in legs(s)] == [False, False]
    assert fake.calls == ["MOW-AYT"]


def test_fresh_cache_answers_without_a_request(adapter):
    adapter(Fake())
    legs(spec())
    fake = Fake()
    adapter(fake)
    assert [x.synthetic for x in legs(spec())] == [False, False]
    assert fake.calls == []


def test_empty_answer_falls_back_to_synthetic_and_logs(adapter, caplog):
    adapter(Fake(there=answer()))
    s = spec()
    with caplog.at_level(logging.WARNING, logger="api.travelpayouts"):
        got = legs(s)
    there = [x for x in got if x.origin == s.origin]
    assert there and all(x.synthetic for x in there)
    assert [x.synthetic for x in got if x.origin != s.origin] == [False]
    assert "no flight" in caplog.text


def test_trap5_back_flight_before_check_out_is_dropped(adapter):
    early = row("AYT", "VKO", at(BACK, 8, 0, IST))
    adapter(Fake(back=answer(early)))
    s = spec()
    back = [x for x in legs(s) if x.origin != s.origin]
    assert back and all(x.synthetic for x in back)


def test_trap5_there_flight_after_check_in_day_is_dropped(adapter):
    late = row("VKO", "AYT", at(SOON, 23, 0, MSK))  # 275 min: lands after midnight in AYT
    adapter(Fake(there=answer(late)))
    s = spec()
    there = [x for x in legs(s) if x.origin == s.origin]
    assert there and all(x.synthetic for x in there)


def test_trap6_origin_hub_sweep_does_not_call_the_api(adapter):
    """far_origin_question prices Москва and 9 hubs: an estimate, synthetic (ADR-0002)."""
    fake = Fake()
    adapter(fake)
    s = TripSpec(destinations=["Анталия"], date_from=SOON, date_to=BACK, nights=4,
                 budget_rub=150_000)  # fmt: skip
    model.far_origin_question(s)
    assert fake.calls == []


def test_trap6_tests_and_eval_never_see_the_token(monkeypatch):
    """conftest drops the token before api.main is imported. With a token api.main only builds
    the real adapter; a search outside a real_data request (make eval) stays synthetic (#490)."""
    import api.checks
    import api.main

    assert "TRAVELPAYOUTS_TOKEN" not in os.environ
    assert offers.REAL_TRANSPORT is None
    monkeypatch.setattr(offers, "REAL_TRANSPORT", None)  # restored after the test
    monkeypatch.setenv("TRAVELPAYOUTS_TOKEN", "token")
    importlib.reload(api.main)
    importlib.reload(api.checks)
    assert isinstance(offers.REAL_TRANSPORT, TravelpayoutsTransport)
    assert isinstance(offers.transport(), SyntheticTransport)
    monkeypatch.delenv("TRAVELPAYOUTS_TOKEN")
    importlib.reload(api.main)


def test_inspiration_place_is_not_asked_live(adapter):
    """Only the chosen place goes to the API: inspiration prices dozens (ADR-0002)."""
    fake = Fake()
    adapter(fake)
    s = spec(destinations=[])
    assert legs(s) == synthetic(s)
    assert fake.calls == []


def test_place_without_iata_is_not_asked(tmp_path):
    fake = Fake()
    tp = TravelpayoutsTransport("token", fetch=fake, cache_dir=tmp_path)
    s = spec()
    origin = offers._complete(resolve_place("Москва"))
    place = {**offers._complete(resolve_place("Анталия")), "airport": None}
    assert tp.legs(s, origin, place) == SyntheticTransport().legs(s, origin, place)
    assert fake.calls == []


def test_trap1_without_token_transport_stays_synthetic(monkeypatch):
    import api.main

    monkeypatch.setattr(offers, "REAL_TRANSPORT", None)  # restored after the test
    monkeypatch.delenv("TRAVELPAYOUTS_TOKEN", raising=False)
    importlib.reload(api.main)
    assert offers.REAL_TRANSPORT is None
    with offers.real_data(True):
        assert isinstance(offers.transport(), SyntheticTransport)
    monkeypatch.setenv("TRAVELPAYOUTS_TOKEN", "token")
    importlib.reload(api.main)
    assert isinstance(offers.REAL_TRANSPORT, TravelpayoutsTransport)
    assert isinstance(offers.TRANSPORT, SyntheticTransport)  # no swap at start-up (#490)
    monkeypatch.delenv("TRAVELPAYOUTS_TOKEN")
    importlib.reload(api.main)  # leave api.main as the other tests import it


def test_spec_without_date_gives_no_legs_and_no_request(tmp_path):
    fake = Fake()
    tp = TravelpayoutsTransport("token", fetch=fake, cache_dir=tmp_path)
    origin, place = (offers._complete(resolve_place(c)) for c in ("Москва", "Анталия"))
    assert tp.legs(spec(date_from=None, date_to=None), origin, place) == []
    assert fake.calls == []
