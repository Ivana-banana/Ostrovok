"""Live 101hotels data (#511 city level, #528 real offers): the city-page parser, the calibration
rule, the real-offer builder and the honest fallback on a non-Russian place, too few hotels or no
network (TRAP-6). No network: _get is monkeypatched, and city pages come from a small inline
fixture."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from api import live_hotels as lh
from api import offers
from api.contracts import TripSpec

SOON = date(2026, 11, 14)  # November, SEASON 0.9
MSK = ZoneInfo("Europe/Moscow")


def card(name, price, stars, free, hid, km=None, rating=None):
    """A 101hotels city-page card with a name, a hotel id and a RUB «от N» price."""
    stars_html = f'<span class="stars stars_{stars}"></span>' if stars else ""
    cancel = "Бесплатная отмена до 12.11" if free else "Невозвратный тариф"
    km_html = f"<div>До центра {km}</div>" if km else ""
    rating_html = f'<span itemprop="ratingValue">{rating}</span>' if rating else ""
    return (
        '<li class="item" itemscope itemtype="https://schema.org/Hotel">'
        f'<div data-hotel-id="{hid}">'
        f'<span itemprop="name">{name}</span>{stars_html}{rating_html}'
        f'<span data-price-currency="RUB" data-price-value="{price}"></span>'
        f"{km_html}<div>{cancel}</div></article>"
    )


def numbers_card(price, stars, free):
    """A card with the numbers but no name or hotel id (so no real offer is built)."""
    stars_html = f'<span class="stars stars_{stars}"></span>' if stars else ""
    cancel = "Бесплатная отмена" if free else "Невозврат"
    return (
        '<li class="item" itemscope itemtype="https://schema.org/Hotel">'
        f'{stars_html}<span data-price-currency="RUB" data-price-value="{price}"></span>'
        f"<div>{cancel}</div></article>"
    )


# Three 3-star hotels (two refundable) and one 4-star; median of the 3-star «от N» is 2000.
CITY_HTML = "<html>" + "".join(
    [
        card("Гранд Отель", 1900, 3, True, hid="11", km="800 м", rating="9.1"),
        card("Морская Звезда", 2100, 3, False, hid="12"),
        card("Уютный Дворик", 2000, 3, True, hid="13"),
        card("Пять Звёзд", 5000, 4, True, hid="14"),
        card("Хостел без звёзд", 900, None, True, hid="15"),
    ]
)
# The same facts with no names: the level still calibrates, but no real offer can be built.
NUMBERS_HTML = "<html>" + "".join(
    [numbers_card(1900, 3, True), numbers_card(2100, 3, False), numbers_card(2000, 3, True)]
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    """Each test starts with empty process caches, an empty disk cache and online."""
    monkeypatch.setattr(lh, "CACHE", tmp_path / "live")
    lh._mem.clear()
    lh._cards.clear()
    lh._robots.clear()
    lh._offline["on"] = False
    lh._last["t"] = -1e9
    yield


def place(country: str = "Россия", name: str = "Сочи") -> lh.Place:
    return {"name": name, "country": country, "slug": "sochi-geo", "lat": 43.6, "lon": 39.7,
            "tz": "Europe/Moscow", "level": 9999, "free_cancel_share": 0.11}  # fmt: skip


def spec() -> TripSpec:
    return TripSpec(origin="Москва", destinations=["Сочи"], date_from=SOON, nights=2)


class FakeBase:
    data_mode = "синтетика"

    def __init__(self):
        self.seen: list[lh.Place] = []

    def hotels(self, spec, place, now):
        self.seen.append(place)
        return [("offer", [])]


def test_parse_city_reads_facts_and_names():
    rows = lh.parse_city(CITY_HTML)
    assert [(r["from_rub"], r["stars"], r["free_cancel"]) for r in rows] == [
        (1900, 3, True),
        (2100, 3, False),
        (2000, 3, True),
        (5000, 4, True),
        (900, None, True),
    ]
    # #528: the name, the hotel id, the distance and the rating are read for the live offer.
    assert (rows[0]["name"], rows[0]["hotel_id"]) == ("Гранд Отель", "11")
    assert (rows[0]["center_km"], rows[0]["rating"]) == (0.8, 9.1)


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


def test_disk_snapshot_keeps_no_hotel_name_6_5_4(monkeypatch):
    """Rule 6.5(4)/6.9: the cached city level is numbers only, even though the page has names."""
    monkeypatch.setattr(lh, "_get", lambda p: CITY_HTML)
    assert lh.city_level(place(), SOON) is not None
    text = (lh.CACHE / "sochi.json").read_text("utf-8")  # #533: keyed by slug, no trip month
    assert not any(w in text for w in ("Отель", "Звезда", "Дворик"))


def test_city_level_skips_a_foreign_place(monkeypatch):
    monkeypatch.setattr(lh, "_get", lambda p: pytest.fail(f"a foreign place must not fetch {p}"))
    assert lh.city_level(place(country="Франция", name="Париж"), SOON) is None


def test_builds_real_offers_from_the_card_TRAP_4(monkeypatch):
    """Online with named cards: real name, synthetic=False, price anchored to «от N ₽» (TRAP-4)."""
    monkeypatch.setattr(lh, "_get", lambda p: CITY_HTML)
    base = FakeBase()
    out = lh.Live101Hotels(base).hotels(spec(), place(), datetime.now(MSK))
    assert base.seen == []  # real cards were found: the synthetic base is not used
    assert out, "expected real offers"
    assert all(h.synthetic is False for h, _ in out)
    assert "Гранд Отель" in [h.title for h, _ in out]
    # «от 1900» for a 3-star, 2 nights, 2 adults, room-only, refundable: 1900 * 2, no invention.
    grand = [h for h, _ in out if h.title == "Гранд Отель"]
    assert any(h.meal == "RO" and h.cancellation.refundable and h.price_rub == 3800 for h in grand)


def test_real_offer_honours_the_card_free_cancel(monkeypatch):
    """A non-refundable card sells no refundable rate, whatever its stars."""
    monkeypatch.setattr(lh, "_get", lambda p: CITY_HTML)
    out = lh.Live101Hotels(FakeBase()).hotels(spec(), place(), datetime.now(MSK))
    zvezda = [h for h, _ in out if h.title == "Морская Звезда"]  # free_cancel=False in the card
    assert zvezda and not any(h.cancellation.refundable for h in zvezda)


def test_level_feeds_base_when_no_named_cards(monkeypatch):
    """Numbers but no names: no real offer, but the live city level still reaches the base."""
    # #533: the divisor is SEASON[month of the fetch]; freeze the fetch in November.
    monkeypatch.setattr(lh, "_now", lambda: datetime(2026, 11, 1, tzinfo=MSK))
    monkeypatch.setattr(lh, "_get", lambda p: NUMBERS_HTML)
    base = FakeBase()
    out = lh.Live101Hotels(base).hotels(spec(), place(), datetime.now(MSK))
    assert out == [("offer", [])]
    assert base.seen[0]["level"] == round(2000 / (offers.STAR_FACTOR[3] * offers.SEASON[11]))
    assert base.seen[0]["free_cancel_share"] == round(2 / 3, 2)


def test_falls_back_to_base_when_offline_TRAP_6(monkeypatch):
    def dead(path):
        raise lh.Offline("нет сети")  # #533: only a network failure turns the layer off

    monkeypatch.setattr(lh, "_get", dead)
    base = FakeBase()
    out = lh.Live101Hotels(base).hotels(spec(), place(), datetime.now(MSK))
    assert out == [("offer", [])]
    assert base.seen[0]["level"] == 9999  # not overridden: the synthetic level stands
    assert lh._offline["on"] is True  # one failure, then every place falls back fast


def test_foreign_place_is_synthetic_TRAP_6(monkeypatch):
    monkeypatch.setattr(lh, "_get", lambda p: pytest.fail(f"a foreign place must not fetch {p}"))
    base = FakeBase()
    out = lh.Live101Hotels(base).hotels(
        spec(), place(country="Франция", name="Париж"), datetime.now(MSK)
    )
    assert out == [("offer", [])]
    assert base.seen[0]["level"] == 9999


def test_snapshot_on_disk_is_served_offline_TRAP_6(monkeypatch, tmp_path):
    monkeypatch.setattr(lh, "CACHE", tmp_path)
    (tmp_path / "sochi.json").write_text('{"level": 2339, "free_cancel_share": 0.75}', "utf-8")
    monkeypatch.setattr(lh, "_get", lambda p: pytest.fail("offline: must read the snapshot"))
    lh._offline["on"] = True
    assert lh.city_level(place(), SOON) == {"level": 2339, "free_cancel_share": 0.75}


def test_trap7_every_live_hotel_offer_names_101hotels_and_the_fetch_time(monkeypatch):
    """#469: a real offer has source and fetched_at; its modelled add-ons stay «синтетика»."""
    monkeypatch.setattr(lh, "_get", lambda p: CITY_HTML)
    out = lh.Live101Hotels(FakeBase()).hotels(spec(), place(), datetime.now(MSK))
    assert out and all(h.source == "101hotels" and h.fetched_at for h, _ in out)
    assert all(e.source == "синтетика" and e.fetched_at is None for _, es in out for e in es)


def test_live_list_has_at_most_five_hotels_675(monkeypatch):
    """#675: a city page has many cards, but the board picks a hotel by «первый…пятый» only."""
    many = "<html>" + "".join(
        card(f"Отель {i}", 2000 + i, 3, True, hid=str(20 + i)) for i in range(8)
    )
    monkeypatch.setattr(lh, "_get", lambda p: many)
    out = lh.Live101Hotels(FakeBase()).hotels(spec(), place(), datetime.now(MSK))
    assert {h.title for h, _ in out} == {f"Отель {i}" for i in range(lh.MAX_REAL)}
    assert lh.MAX_REAL == 5
