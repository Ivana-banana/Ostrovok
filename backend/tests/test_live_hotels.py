"""Live hotel price level from 101hotels (#511): the city-page parser, the calibration rule, and
the honest fallback on a non-Russian place, too few 3-star hotels or no network (TRAP-6). No
network: _get is monkeypatched, and city pages come from a small inline fixture."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from api import live_hotels as lh
from api import offers
from api.contracts import TripSpec

SOON = date(2026, 11, 14)  # November, SEASON 0.9
MSK = ZoneInfo("Europe/Moscow")


def card(name: str, price: int, stars: int | None, free: bool) -> str:
    stars_html = f'<span class="stars stars_{stars}"></span>' if stars else ""
    cancel = "Бесплатная отмена до 12.11" if free else "Невозвратный тариф"
    return (
        '<li class="item" itemscope itemtype="https://schema.org/Hotel">'
        f"<h3>{name}</h3>{stars_html}"
        f'<span data-price-currency="RUB" data-price-value="{price}"></span>'
        f"<div>{cancel}</div></article>"
    )


# Three 3-star hotels (two refundable) and one 4-star; median of the 3-star «от N» is 2000.
CITY_HTML = "<html>" + "".join(
    [
        card("Гранд Отель", 1900, 3, True),
        card("Морская Звезда", 2100, 3, False),
        card("Уютный Дворик", 2000, 3, True),
        card("Пять Звёзд", 5000, 4, True),
        card("Хостел без звёзд", 900, None, True),
    ]
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    """Each test starts with empty process caches, an empty disk cache and online."""
    monkeypatch.setattr(lh, "CACHE", tmp_path / "live")
    lh._mem.clear()
    lh._robots.clear()
    lh._offline["on"] = False
    lh._last["t"] = -1e9
    yield


def place(country: str = "Россия", name: str = "Сочи") -> lh.Place:
    return {"name": name, "country": country, "slug": "sochi-geo", "level": 9999,
            "free_cancel_share": 0.11}  # fmt: skip


def spec() -> TripSpec:
    return TripSpec(origin="Москва", destinations=["Сочи"], date_from=SOON, nights=2)


class FakeBase:
    data_mode = "синтетика"

    def __init__(self):
        self.seen: list[lh.Place] = []

    def hotels(self, spec, place, now):
        self.seen.append(place)
        return [("offer", [])]


def test_parse_city_keeps_only_numbers():
    rows = lh.parse_city(CITY_HTML)
    assert rows == [
        {"from_rub": 1900, "stars": 3, "free_cancel": True},
        {"from_rub": 2100, "stars": 3, "free_cancel": False},
        {"from_rub": 2000, "stars": 3, "free_cancel": True},
        {"from_rub": 5000, "stars": 4, "free_cancel": True},
        {"from_rub": 900, "stars": None, "free_cancel": True},
    ]
    # Rule 6.5(4)/6.9: no hotel name reaches the facts.
    assert not any("Отель" in str(r) or "Звезда" in str(r) for r in rows)


def test_aggregate_matches_the_calibration_rule():
    agg = lh._aggregate(lh.parse_city(CITY_HTML), month=11)
    # median3 = 2000; 2000 / (STAR_FACTOR[3]=0.95 * SEASON[11]=0.9) = 2339
    assert agg["level"] == round(2000 / (offers.STAR_FACTOR[3] * offers.SEASON[11]))
    assert agg["median3_rub"] == 2000
    assert agg["n3"] == 3
    # free cancellation over the 3+ star hotels only (the no-star hostel is out): 3 of 4.
    assert agg["free_cancel_share"] == 0.75


def test_aggregate_needs_min_n_3_star_hotels():
    cards = [{"from_rub": 2000, "stars": 3, "free_cancel": True}] * (lh.MIN_N - 1)
    assert lh._aggregate(cards, month=11) is None


def test_city_level_skips_a_foreign_place(monkeypatch):
    monkeypatch.setattr(lh, "_get", lambda p: pytest.fail(f"a foreign place must not fetch {p}"))
    assert lh.city_level(place(country="Франция", name="Париж"), SOON) is None


def test_provider_feeds_the_live_level_to_the_base(monkeypatch):
    monkeypatch.setattr(lh, "city_level", lambda p, w: {"level": 2339, "free_cancel_share": 0.75})
    base = FakeBase()
    out = lh.Live101Hotels(base).hotels(spec(), place(), datetime.now(MSK))
    assert out == [("offer", [])]
    assert (base.seen[0]["level"], base.seen[0]["free_cancel_share"]) == (2339, 0.75)
    assert base.seen[0]["name"] == "Сочи"  # the place is copied, other fields kept


def test_falls_back_to_base_when_offline_TRAP_6(monkeypatch):
    def dead(path):
        raise lh.Refused("нет сети")

    monkeypatch.setattr(lh, "_get", dead)
    base = FakeBase()
    out = lh.Live101Hotels(base).hotels(spec(), place(), datetime.now(MSK))
    assert out == [("offer", [])]
    assert base.seen[0]["level"] == 9999  # not overridden: the synthetic level stands
    assert lh._offline["on"] is True  # one failure, then every place falls back fast


def test_snapshot_on_disk_is_served_offline_TRAP_6(monkeypatch, tmp_path):
    monkeypatch.setattr(lh, "CACHE", tmp_path)
    (tmp_path / "sochi-11.json").write_text('{"level": 2339, "free_cancel_share": 0.75}', "utf-8")
    monkeypatch.setattr(lh, "_get", lambda p: pytest.fail("offline: must read the snapshot"))
    lh._offline["on"] = True
    assert lh.city_level(place(), SOON) == {"level": 2339, "free_cancel_share": 0.75}
