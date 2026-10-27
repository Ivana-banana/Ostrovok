"""Hotel offers collector (#119): the parsers of scripts/collect_hotel_offers.py.

The markup and JSON copy the Booking card and the Agoda room-grid answer (2026-10-27) with
made-up values; hotel names are left out on purpose.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_path = Path(__file__).resolve().parents[2] / "scripts" / "collect_hotel_offers.py"
sys.path.insert(0, str(_path.parent))
_spec = importlib.util.spec_from_file_location("collect_hotel_offers", _path)
ch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ch)

CARD = (
    '<div data-testid="property-card"><a href="https://www.booking.com/hotel/tr/x-1.ru.html?aid=1" '
    'data-testid="title-link">x</a><button aria-label="Оценка объекта: 4&nbsp;звезды из 5"></button>'
    '<span data-testid="price-and-discounted-price"><span>30&nbsp;636&nbsp;руб.</span></span>'
    "<div>Бесплатная отмена</div><div>Завтрак включён</div></div>"
)


def offer(amount, *, cancel_text=None, free=False, breakfast=False):
    benefit = f'"text": "{cancel_text}"' if cancel_text else '"text": "Оплата не возвращается"'
    return {
        "price": {"final": {"amountNumber": amount, "currency": "€"}},
        "policies": {"isFreeCancellation": free, "benefit": {benefit.split('"')[1]: benefit}},
        "benefits": {"isBreakfastIncluded": breakfast},
        "cancellation": cancel_text,
    }


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        ("7 дек. 26 г.", "2026-12-07"),
        ("1 янв. 27 г.", "2027-01-01"),
        ("5 сент. 26 г.", "2026-09-05"),
        ("скоро", None),
    ],
)
def test_agoda_date(text, iso):
    assert ch.agoda_date(text) == iso


def test_booking_card_price_is_for_the_stay_and_night_price_divides_by_nights():
    [row] = ch.parse_booking(CARD, nights=4)
    assert row["total_price"] == 30636
    assert row["night_price"] == 7659
    assert row["stars"] == 4
    assert row["free_cancel"] is True
    assert row["breakfast"] is True
    assert row["id"]


def test_booking_card_without_price_is_skipped():
    assert ch.parse_booking(CARD.replace("price-and-discounted-price", "other"), nights=4) == []


def test_agoda_price_is_per_night_total_multiplies_by_nights():
    grid = {
        "rooms": [
            {
                "offers": [
                    offer(
                        100,
                        cancel_text="Бесплатная отмена до 7 дек. 26 г.",
                        free=True,
                        breakfast=True,
                    ),
                    offer(80),
                    {"price": {}},
                ]
            }
        ]
    }
    first, second = ch.parse_agoda(grid, nights=4, stars=None)
    assert (first["night_price"], first["total_price"], first["currency"]) == (100, 400, "EUR")
    assert first["free_cancel_until"] == "2026-12-07"
    assert first["breakfast"] is True
    assert second["free_cancel"] is False
    assert second["free_cancel_until"] is None


def test_booking_card_without_link_is_skipped():
    assert ch.parse_booking(CARD.replace("https://www.booking.com/hotel/", "/x/"), 4) == []


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, robots):
        self.robots = robots
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append(url)
        return self.robots


@pytest.fixture
def fake_session(monkeypatch):
    def make(robots):
        fake = FakeSession(robots)
        monkeypatch.setattr(ch, "session", lambda: fake)
        monkeypatch.setattr(ch, "PAUSE_S", 0)
        monkeypatch.setattr(ch, "_robots", {})
        monkeypatch.setattr(ch, "_state", {"last": -1e9, "requests": 0, "fails": 0, "budget": 10})
        return fake

    return make


def test_closed_path_is_not_requested(fake_session):
    fake = fake_session(FakeResponse(200, "User-agent: *\nDisallow: /*/hotel/all/\n"))
    with pytest.raises(ch.Disallowed):
        ch.check_robots("h.example", "/ru-ru/x/hotel/all/y.html")
    ch.check_robots("h.example", "/ru-ru/x/hotel/y.html")
    assert fake.calls == ["https://h.example/robots.txt"]


def test_robots_that_is_not_200_stops_the_run(fake_session):
    # a WAF challenge page (202) must not read as «everything allowed»
    fake_session(FakeResponse(202, "<html>challenge</html>"))
    with pytest.raises(ch.Refused, match="HTTP 202"):
        ch.check_robots("h.example", "/anything")
