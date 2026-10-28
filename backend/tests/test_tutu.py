"""Real trains from tutu.ru (#569): the page parser, the timetable in zones, the price, the
fallback and the gate.

No network: the adapter gets a fake `get`; the pages are trimmed real tutu.ru route pages
(tests/fixtures/tutu, saved 2026-10-28)."""

import importlib
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from api import offers
from api.contracts import TripSpec
from api.offers import SyntheticTransport, find_offers, resolve_place
from api.tutu import FAILED_S, Offline, Refused, TutuTrains, parse_route, slug_of

PAGES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "tutu"
ROBOTS = "User-Agent: *\nDisallow: /*date=\nDisallow: /poezda/order/\n"
THERE, BACK = date(2026, 11, 14), date(2026, 11, 17)
MSK, EKB = ZoneInfo("Europe/Moscow"), ZoneInfo("Asia/Yekaterinburg")


def page(name: str) -> str:
    return (PAGES / f"{name}.html").read_text(encoding="utf-8")


class Fake:
    """tutu.ru by path; an exception instance is raised. Counts the page calls."""

    def __init__(self, **over):
        self.pages = {
            "/robots.txt": ROBOTS,
            "/poezda/Moskva/Ekaterinburg/": page("Moskva-Ekaterinburg"),
            "/poezda/Ekaterinburg/Moskva/": page("Ekaterinburg-Moskva"),
            **over,
        }
        self.calls: list[str] = []

    def __call__(self, path: str) -> str:
        self.calls.append(path)
        got = self.pages.get(path, Refused(f"HTTP 404 {path}"))
        if isinstance(got, Exception):
            raise got
        return got


def spec(city: str = "Екатеринбург", **kw) -> TripSpec:
    data = {"origin": "Москва", "destinations": [city], "date_from": THERE, "date_to": BACK}
    return TripSpec(nights=3, **{**data, **kw})


def ends(city: str = "Екатеринбург") -> tuple[dict, dict]:
    return offers._complete(resolve_place("Москва")), offers._complete(resolve_place(city))


def run(fake: Fake, s: TripSpec, tmp_path: Path, city: str = "Екатеринбург"):
    return TutuTrains(SyntheticTransport(), get=fake, cache_dir=tmp_path).legs(s, *ends(city))


def base(s: TripSpec, city: str = "Екатеринбург"):
    return SyntheticTransport().legs(s, *ends(city))


def test_parse_route_reads_trains_zones_and_the_cheapest_seat():
    rows = {r["number"]: r for r in parse_route(page("Moskva-Ekaterinburg"))}
    assert set(rows) == {"070Я", "012Я", "056М", "010Н"}
    r = rows["070Я"]
    assert (r["from"], r["to"]) == ("Москва Ярославская", "Екатеринбург Пасс.")
    assert (r["from_tz"], r["to_tz"]) == ("Europe/Moscow", "Asia/Yekaterinburg")
    assert datetime.fromisoformat(r["depart"]) == datetime(2026, 10, 28, 13, 20, tzinfo=MSK)
    assert r["minutes"] == 1797  # 13:20 MSK -> 21:17 +05 the next day
    assert (r["class"], r["price"]) == ("плацкарт", 4019.7)  # «от» of the cheapest car class
    assert THERE.isoformat() in r["days"]


def test_parse_route_without_trains_or_json():
    assert parse_route(page("Moskva-Ekaterinburg").replace('"routes":[', '"routes":[],"x":[')) == []
    with pytest.raises(ValueError):
        parse_route("<html>404</html>")


def test_slug_follows_tutu_urls():
    assert slug_of("Казань") == "Kazan"
    assert slug_of("Ростов-на-Дону") == "Rostov-na-Donu"
    assert slug_of("Зелёный Дол") == "Zelenyy_Dol"
    assert slug_of("Хабаровск") == "Habarovsk"
    assert slug_of("Нижний Новгород") == "Nizhnij-Novgorod"  # the site misses the rule


def test_trap5_real_trains_keep_station_zones_and_the_check_in_day(tmp_path):
    s = spec()
    got = run(Fake(), s, tmp_path)
    trains = [x for x in got if x.mode == "train"]
    assert trains and all(not x.synthetic and x.offer_id.endswith("-tutu") for x in trains)
    there = [x for x in trains if x.destination == "Екатеринбург"]
    back = [x for x in trains if x.origin == "Екатеринбург"]
    assert there and back
    for x in there:  # arrives on the check-in day by the clock of Екатеринбург, not at night
        assert x.depart_at.tzinfo == MSK and x.arrive_at.tzinfo == EKB
        assert x.arrive_at.date() == THERE and 5 <= x.arrive_at.hour < 23
    by_train = {x.title.split()[1]: x for x in there}
    t = by_train["070Я"]  # 13.11 13:20 MSK, 29 h 57 min, 14.11 21:17 by the local clock
    assert t.depart_at == datetime(2026, 11, 13, 13, 20, tzinfo=MSK)
    assert t.arrive_at == datetime(2026, 11, 14, 21, 17, tzinfo=EKB)
    assert t.arrive_at - t.depart_at == timedelta(minutes=1797)
    assert "010Н" not in by_train  # arrives 14.11 02:12: night, the reception may be closed
    earliest = offers._back_from(ends()[1], "train", BACK)
    assert all(x.depart_at.date() == BACK and x.depart_at >= earliest for x in back)
    assert {x.title.split()[1] for x in back} == {"009Н"}  # 055Е leaves at 00:15, 077Ы not 17.11
    assert [x.mode for x in got if x.mode != "train"] == [
        x.mode for x in base(s) if x.mode != "train"
    ]


def test_trap4_price_is_tutu_times_fares_and_the_child_share_is_named(tmp_path):
    s = spec(children_ages=[7])
    t = next(x for x in run(Fake(), s, tmp_path) if "070Я" in x.title)
    assert t.price_rub == round(4019.7 * 2.5)  # 2 adults + a child of 7 at half fare
    assert "цена «от» tutu.ru" in t.title
    assert "детский тариф — оценка" in t.title
    plain = next(x for x in run(Fake(), spec(), tmp_path) if "070Я" in x.title)
    assert plain.price_rub == round(4019.7 * 2) and "оценка" not in plain.title


def test_trap5_real_trains_keep_core_invariants(tmp_path, monkeypatch):
    monkeypatch.setattr(offers, "TRANSPORT", TutuTrains(SyntheticTransport(), get=Fake(),
                                                        cache_dir=tmp_path))  # fmt: skip
    s = spec()
    found = find_offers(s, "Екатеринбург")
    legs = [o for o in found if o.kind == "transport"]
    hotels = {o.city for o in found if o.kind == "hotel"}
    assert any(not x.synthetic for x in legs)
    assert {x.destination for x in legs if x.origin == s.origin} == hotels
    assert {x.destination for x in legs if x.origin != s.origin} == {s.origin}
    assert [x.origin == s.origin for x in legs] == sorted((x.origin == s.origin for x in legs),
                                                          reverse=True)  # fmt: skip


def test_trap6_offline_keeps_synthetic_trains_and_waits(tmp_path):
    fake = Fake(**{"/robots.txt": Offline("сеть")})
    tutu = TutuTrains(SyntheticTransport(), get=fake, cache_dir=tmp_path)
    s = spec()
    assert tutu.legs(s, *ends()) == base(s)
    asked = len(fake.calls)
    assert tutu.legs(s, *ends()) == base(s)
    assert len(fake.calls) == asked  # not asked again for FAILED_S
    tutu.failed_at -= FAILED_S
    tutu.legs(s, *ends())
    assert len(fake.calls) > asked


def test_trap6_stale_cache_serves_offline(tmp_path):
    run(Fake(), spec(), tmp_path)  # writes data/live/tutu/<From>-<To>.json
    for f in tmp_path.glob("*.json"):
        os.utime(f, (0, 0))  # stale
    got = run(Fake(**{"/robots.txt": Offline("сеть")}), spec(), tmp_path)
    assert any(x.mode == "train" and not x.synthetic for x in got)


def test_trap6_refusal_bad_page_and_robots_fall_back(tmp_path):
    s = spec()
    refused = run(Fake(**{"/poezda/Moskva/Ekaterinburg/": Refused("HTTP 404")}), s, tmp_path)
    assert [x for x in refused if x.destination == "Екатеринбург" and x.mode == "train"] == [
        x for x in base(s) if x.destination == "Екатеринбург" and x.mode == "train"
    ]
    bad = Fake(**{"/poezda/Moskva/Ekaterinburg/": "<html>", "/poezda/Ekaterinburg/Moskva/": "{"})
    assert run(bad, s, tmp_path / "bad") == base(s)
    closed = Fake(**{"/robots.txt": "User-Agent: *\nDisallow: /poezda/\n"})
    assert run(closed, s, tmp_path / "closed") == base(s)
    assert closed.calls == ["/robots.txt"]  # the page itself is not fetched
    captcha = Fake(**{"/poezda/Moskva/Ekaterinburg/": "<html>Подтвердите, что вы не робот</html>"})
    assert [x for x in run(captcha, s, tmp_path / "captcha") if x.origin == s.origin] == [
        x for x in base(s) if x.origin == s.origin
    ]


class Answer:
    """What urllib.request.urlopen gives: status 200, the body, no gzip."""

    status, headers = 200, {}

    def __init__(self, body: str):
        self.body = body.encode()

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_trap6_the_real_robots_txt_is_obeyed_though_it_names_captcha(tmp_path, monkeypatch):
    """tutu robots.txt has «Disallow: /captcha»: it is the rules, not a refusal page (#569)."""
    robots = (PAGES / "robots.txt").read_text(encoding="utf-8")  # the file of 2026-10-28
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: Answer(robots))
    tutu = TutuTrains(SyntheticTransport(), cache_dir=tmp_path)  # the real _get, no network
    assert not tutu._allowed("/poezda/Moskva/Kazan/?date=05.11.2026")  # «Disallow: /*date=»
    assert tutu.robots == robots
    fake = Fake(**{"/robots.txt": robots + "Disallow: /poezda/Ekaterinburg/\n"})
    got = run(fake, spec(), tmp_path)
    assert "/poezda/Ekaterinburg/Moskva/" not in fake.calls  # disallowed: not fetched
    assert "/poezda/Moskva/Ekaterinburg/" in fake.calls  # the route page is allowed
    assert any(not x.synthetic for x in got if x.origin == "Москва")


def test_trap6_no_train_that_day_keeps_synthetic(tmp_path):
    s = spec(date_from=date(2027, 6, 1), date_to=date(2027, 6, 4))  # past the sales horizon
    assert run(Fake(), s, tmp_path) == base(s)


def test_only_the_chosen_russian_place_is_asked(tmp_path):
    fake = Fake()
    s = spec(destinations=[])  # inspiration prices dozens of places (ADR-0002)
    assert run(fake, s, tmp_path) == base(s)
    s = spec(city="Анталия")
    assert run(fake, s, tmp_path, city="Анталия") == base(s, city="Анталия")
    assert fake.calls == []


def test_trap6_tests_and_eval_never_see_live_trains(monkeypatch):
    """conftest sets LIVE_TRAINS=0; with it on api.main builds the adapter, a request without
    real_data and make eval stay synthetic (#490)."""
    import api.checks
    import api.main

    assert offers.REAL_TRANSPORT is None
    monkeypatch.setattr(offers, "REAL_TRANSPORT", None)  # restored after the test
    monkeypatch.setenv("LIVE_TRAINS", "1")
    importlib.reload(api.main)
    assert isinstance(offers.REAL_TRANSPORT, TutuTrains)
    assert isinstance(offers.REAL_TRANSPORT.base, SyntheticTransport)  # no token: no flights
    assert isinstance(offers.transport(), SyntheticTransport)  # no real_data in the request
    importlib.reload(api.checks)
    assert offers.REAL_TRANSPORT is None
    monkeypatch.setenv("LIVE_TRAINS", "0")
    importlib.reload(api.main)
    assert offers.REAL_TRANSPORT is None
