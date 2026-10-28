"""#533: the live 101hotels layer without network, the season divisor and a refusal for one slug.

The network is urllib.request.urlopen, patched: it raises, or it answers from a dict path -> HTML
or HTTP code. time.sleep and the urlopen timeout are counted on a fake clock, so the tests are fast
and still measure the wait the user would see (TRAP-6)."""

import io
import logging
import urllib.error
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from api import live_hotels as lh
from api import offers
from api.contracts import TripSpec

MSK = ZoneInfo("Europe/Moscow")
JULY = datetime(2026, 7, 10, 9, 0, tzinfo=UTC)
CARD = (
    '<li class="item" itemscope itemtype="https://schema.org/Hotel"><div data-hotel-id="{hid}">'
    '<span itemprop="name">Отель {hid}</span><span class="stars stars_3"></span>'
    '<span data-price-currency="RUB" data-price-value="{price}"></span><div>Невозврат</div>'
    "</article>"
)
# Three named 3-star cards; the median «от N» is 2000.
CITY = "<html>" + "".join(CARD.format(hid=h, price=p) for h, p in ((1, 1900), (2, 2000), (3, 2100)))


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    """Empty process caches, an empty disk cache, online; a fake clock for sleep and timeouts."""
    monkeypatch.setattr(lh, "CACHE", tmp_path / "live")
    lh._mem.clear()
    lh._cards.clear()
    lh._robots.clear()
    lh._offline["on"] = False
    lh._last["t"] = -1e9
    clock = {"waited": 0.0, "sleeps": []}

    def sleep(s):
        clock["sleeps"].append(s)
        clock["waited"] += s

    monkeypatch.setattr(lh.time, "sleep", sleep)
    return clock


class Net:
    """A fake urlopen: `pages` maps a path to HTML or an HTTP code; `fail` raises for every URL."""

    def __init__(self, pages=None, fail=None, clock=None):
        self.pages, self.fail, self.clock, self.calls = pages or {}, fail, clock, []

    def __call__(self, req, timeout):
        path = req.full_url.removeprefix(f"https://{lh.HOST}")
        self.calls.append(path)
        if self.fail is TimeoutError:  # dropped packets: the socket waits the whole timeout
            self.clock["waited"] += timeout
            raise TimeoutError("timed out")
        if self.fail is not None:
            raise self.fail
        page = self.pages.get(path, 404)
        if isinstance(page, int):
            raise urllib.error.HTTPError(req.full_url, page, "err", {}, io.BytesIO(b""))
        return Resp(page)


class Resp:
    status = 200

    def __init__(self, text):
        self.text = text

    def read(self):
        return self.text.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeBase:
    def __init__(self):
        self.seen = []

    def hotels(self, spec, place, now):
        self.seen.append(place)
        return [("synthetic", [])]


def place(name="Сочи"):
    return {"name": name, "country": "Россия", "slug": "geo", "lat": 43.6, "lon": 39.7, "tz": "Europe/Moscow",
            "level": 9999, "free_cancel_share": 0.5}  # fmt: skip


def spec(day=date(2026, 12, 14)):
    return TripSpec(origin="Москва", destinations=["Сочи"], date_from=day, nights=2)


@pytest.mark.parametrize(
    "fail",
    [ConnectionRefusedError("refused"), urllib.error.URLError("no dns"), TimeoutError],
    ids=["refused", "no-dns", "dropped-packets"],
)
def test_offline_first_reply_adds_under_4_s_TRAP_6(monkeypatch, _reset, fail):
    """No network: the robots.txt failure turns live off at once — no pause, no city fetch."""
    net = Net(fail=fail, clock=_reset)
    monkeypatch.setattr(lh.urllib.request, "urlopen", net)
    base = FakeBase()
    out = lh.Live101Hotels(base).hotels(spec(), place(), datetime.now(MSK))
    assert out == [("synthetic", [])]
    assert net.calls == ["/robots.txt"]  # the city page is not tried after a network failure
    assert _reset["sleeps"] == []
    assert _reset["waited"] < 4
    assert lh._offline["on"] is True
    # The next city answers without the network at all.
    lh.Live101Hotels(base).hotels(spec(), place("Казань"), datetime.now(MSK))
    assert net.calls == ["/robots.txt"]


def test_no_pause_after_a_failed_robots_TRAP_6(monkeypatch, _reset):
    """robots.txt 404 is not a successful request: the city page goes out without PAUSE_S."""
    net = Net(pages={"/main/cities/sochi": CITY}, clock=_reset)
    monkeypatch.setattr(lh.urllib.request, "urlopen", net)
    out = lh.Live101Hotels(FakeBase()).hotels(spec(), place(), datetime.now(MSK))
    assert net.calls == ["/robots.txt", "/main/cities/sochi"]
    assert _reset["sleeps"] == []
    assert out and all(h.synthetic is False for h, _ in out)


def test_season_divisor_is_the_fetch_month_TRAP_6(monkeypatch):
    """Fetched in July for a December trip: divide by SEASON[7], not SEASON[12]."""
    monkeypatch.setattr(lh, "_now", lambda: JULY)
    monkeypatch.setattr(lh, "_get", lambda p: "" if p == "/robots.txt" else CITY)
    agg = lh.city_level(place(), date(2026, 12, 14))
    assert agg["level"] == round(2000 / (offers.STAR_FACTOR[3] * offers.SEASON[7]))
    assert agg["level"] != round(2000 / (offers.STAR_FACTOR[3] * offers.SEASON[12]))


def test_cache_key_has_no_trip_month_TRAP_6(monkeypatch):
    """One fetch serves every trip month; the disk snapshot is per slug."""
    monkeypatch.setattr(lh, "_now", lambda: JULY)
    fetched = []

    def get(p):
        fetched.append(p)
        return "" if p == "/robots.txt" else CITY

    monkeypatch.setattr(lh, "_get", get)
    july = lh.city_level(place(), date(2026, 7, 20))
    december = lh.city_level(place(), date(2026, 12, 20))
    assert july == december
    assert fetched == ["/robots.txt", "/main/cities/sochi"]
    assert (lh.CACHE / "sochi.json").exists()


def test_a_404_for_one_slug_keeps_the_next_city_live_TRAP_6(monkeypatch, _reset, caplog):
    """«Ейск» → eysk is 404: that slug is cached as None, the layer stays on for Сочи."""
    net = Net(pages={"/robots.txt": "", "/main/cities/sochi": CITY}, clock=_reset)
    monkeypatch.setattr(lh.urllib.request, "urlopen", net)
    base = FakeBase()
    with caplog.at_level(logging.WARNING, logger=lh.__name__):
        assert lh.Live101Hotels(base).hotels(spec(), place("Ейск"), datetime.now(MSK)) == [
            ("synthetic", [])
        ]
    assert lh._offline["on"] is False
    assert lh._cards["eysk"] is None
    assert any("eysk" in r.getMessage() for r in caplog.records)
    out = lh.Live101Hotels(base).hotels(spec(), place(), datetime.now(MSK))
    assert out and all(h.synthetic is False for h, _ in out)
    # The refused slug is not asked again.
    lh.Live101Hotels(base).hotels(spec(), place("Ейск"), datetime.now(MSK))
    assert net.calls.count("/main/cities/eysk") == 1


def test_network_failure_is_logged_TRAP_6(monkeypatch, _reset, caplog):
    monkeypatch.setattr(lh.urllib.request, "urlopen", Net(fail=OSError("down"), clock=_reset))
    with caplog.at_level(logging.WARNING, logger=lh.__name__):
        lh.Live101Hotels(FakeBase()).hotels(spec(), place(), datetime.now(MSK))
    assert any("выключен" in r.getMessage() for r in caplog.records)


def test_layer_bug_is_logged_and_falls_back_TRAP_6(monkeypatch, caplog):
    """Any exception inside the layer: the base answers, and the log says why."""

    def broken(place, when):
        raise KeyError("lat")

    monkeypatch.setattr(lh, "city_level", broken)
    base = FakeBase()
    with caplog.at_level(logging.WARNING, logger=lh.__name__):
        out = lh.Live101Hotels(base).hotels(spec(), place(), datetime.now(MSK))
    assert out == [("synthetic", [])]
    assert any(r.levelno == logging.WARNING and r.exc_info for r in caplog.records)
