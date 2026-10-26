"""Offline price collector (#119): parsers and the robots.txt matcher of scripts/collect_prices.py.

The HTML below copies the markup of the real pages (101hotels city page, tutu route page,
2026-10-27) with made-up values; names and descriptions are left out on purpose.
"""

import importlib.util
from pathlib import Path

import pytest

_path = Path(__file__).resolve().parents[2] / "scripts" / "collect_prices.py"
_spec = importlib.util.spec_from_file_location("collect_prices", _path)
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)

CARD = (
    '<li class="item preload_hotel_item"  itemscope itemtype="https://schema.org/Hotel">'
    '<div class="stars stars_{stars}"></div>'
    '<span class="distance tooltip">До центра 9.7 км</span>'
    '<span class="distance tooltip"><span class="important">До Черного моря 1 200 м</span></span>'
    "{cancel}"
    '<span class="d-block rating-value" itemprop="ratingValue">9,4<span class="out-of">'
    '<meta itemprop="reviewCount" content="132" />'
    "{price}</article></li>"
)
PRICE = '<span class="price-value" data-price-currency="{}" data-price-value="{}"> </span>'


def test_101hotels_card_facts_and_metres():
    html = CARD.format(
        stars=4, cancel="<span>Бесплатная отмена</span>", price=PRICE.format("RUB", 5200)
    ) + CARD.format(stars=3, cancel="", price="")
    assert cp.parse_101hotels(html) == [
        {
            "from_rub": 5200,
            "stars": 4,
            "center_km": 9.7,
            "sea_km": 1.2,
            "free_cancel": True,
            "rating": 9.4,
            "reviews": 132,
        }
    ]


def test_tutu_fares_from_aggregate_offer():
    html = (
        '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
        '<script type="application/ld+json">{"@type":"Product","offers":{"@type":"AggregateOffer",'
        '"lowPrice":801,"highPrice":39241,"offers":[{"@type":"Offer","itemOffered":{"@type":'
        '"TrainTrip","departureTime":"23:00:00","arrivalTime":"08:00:00","trainNumber":"001А"},'
        '"price":1457,"category":"Купе"}]}}</script>'
    )
    assert cp.parse_tutu(html) == {
        "low_rub": 801,
        "high_rub": 39241,
        "trains": [
            {
                "number": "001А",
                "depart": "23:00",
                "arrive": "08:00",
                "arrive_next_day": True,
                "category": "Купе",
                "price_rub": 1457,
            }
        ],
    }


def test_robots_longest_rule_wins_and_star_matches():
    robots = "User-agent: *\nDisallow: /search\nDisallow: /*?\nAllow: /main/cities/\n"
    assert cp.allowed(robots, "/main/cities/sochi")
    assert not cp.allowed(robots, "/search/sochi")
    assert not cp.allowed(robots, "/hotel?id=1")


def test_captcha_word_is_a_refusal_only_on_a_small_page():
    assert cp.refused("<script src=captcha.js></script>")
    assert not cp.refused("<script src=captcha.js></script>" + "x" * cp.CHALLENGE_MAX)
    assert cp.refused("Проверяем, что вы не бот" + "x" * cp.CHALLENGE_MAX)


def test_101hotels_last_card_ends_at_its_article():
    html = CARD.format(stars=4, cancel="", price=PRICE.format("RUB", 900)) + "Бесплатная отмена"
    assert cp.parse_101hotels(html)[0]["free_cancel"] is False


def test_101hotels_price_not_in_rub_is_an_error():
    with pytest.raises(ValueError):
        cp.parse_101hotels(CARD.format(stars=4, cancel="", price=PRICE.format("EUR", 35)))


def test_robots_own_group_beats_star():
    robots = "User-agent: *\nDisallow: /\n\nUser-agent: nedovost-price-probe\nAllow: /\n"
    assert cp.allowed(robots, "/main/cities/sochi")


@pytest.fixture
def host(monkeypatch, tmp_path):
    """Fake network: robots.txt and one page; returns the dict to set the page answer."""
    answer = {}
    monkeypatch.setattr(cp, "OUT", tmp_path)
    monkeypatch.setattr(cp, "_robots", {})

    def get(h, path):
        if path == "/robots.txt":
            return answer.get("robots", (200, "", ""))
        return answer["page"]

    monkeypatch.setattr(cp, "_get", get)
    return answer


def test_fetch_redirect_is_a_refusal(host):
    host["page"] = (200, "https://x.ru/search", "<html></html>")
    with pytest.raises(cp.Refused):
        cp.fetch("x.ru", "/main/cities/nowhere", cp.parse_101hotels)


def test_fetch_robots_not_200_is_a_refusal(host):
    host["robots"] = (403, "https://x.ru/robots.txt", "")
    with pytest.raises(cp.Refused):
        cp.fetch("x.ru", "/a", cp.parse_101hotels)


def test_fetch_200_without_facts_is_a_refusal_and_not_cached(host, tmp_path):
    host["page"] = (200, "https://x.ru/a", "<html>" + "x" * cp.CHALLENGE_MAX + "</html>")
    with pytest.raises(cp.Refused):
        cp.fetch("x.ru", "/a", cp.parse_101hotels)
    assert not (tmp_path / "cache").exists()


def test_tutu_price_not_in_rub_is_an_error():
    html = (
        '<script type="application/ld+json">{"@type":"Product","offers":{"@type":"AggregateOffer",'
        '"lowPrice":10,"highPrice":90,"priceCurrency":"EUR","offers":[]}}</script>'
    )
    with pytest.raises(ValueError):
        cp.parse_tutu(html)
