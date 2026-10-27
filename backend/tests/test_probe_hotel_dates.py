"""Dated hotel offers probe (#119): the rate-row parser of scripts/probe_hotel_dates.py.

The markup copies the 101hotels `get_rooms_variants` answer (2026-10-27) with made-up values;
hotel names and descriptions are left out on purpose.
"""

import importlib.util
from pathlib import Path

import pytest

_path = Path(__file__).resolve().parents[2] / "scripts" / "probe_hotel_dates.py"
_spec = importlib.util.spec_from_file_location("probe_hotel_dates", _path)
pd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pd)

POLICY = (
    "Для гарантии бронирования производится списание денежных средств с&amp;nbsp;вашей банковской "
    "карты в&amp;nbsp;размере {charge}.&lt;p&gt;Бесплатная отмена до&amp;nbsp;23:59:59&amp;nbsp;MSK "
    "07.12.2026.&lt;br/&gt;В случае отмены позднее этого срока стоимость не возвращается.&lt;/p&gt;"
)
ROW = (
    '<td class="meal-type-cell"><div class="without-meals">{meal}</div>'
    '<div class="booking-guarantee" data-policy="{policy}">Бесплатная отмена</div></td>'
    '<td class="room-price-cell right"><div class="room-price">'
    '<span class="price-value" id="price_1_0" data-price-value="{price}" data-price-currency="RUB">x</span>'
    "</div></td>"
)


def row(meal, price, policy=""):
    return ROW.format(meal=meal, price=price, policy=policy)


def test_offer_price_meal_cancel_and_prepay():
    html = row("Без&nbsp;питания", 12400, POLICY.format(charge="стоимости первых суток")) + row(
        "Включён завтрак и ужин", 20000, POLICY.format(charge="полной стоимости номера")
    )
    first, second = pd.parse_offers(html)
    assert first == {
        "price_rub": 12400,
        "meal": "none",
        "free_cancel_until": "2026-12-07",
        "prepay": "first_night",
    }
    assert second["meal"] == "half_board"
    assert second["prepay"] == "full"


@pytest.mark.parametrize(
    ("text", "meal"),
    [
        ("Завтрак включён", "breakfast"),
        ("Включён завтрак, обед и ужин", "full_board"),
        ("Включён завтрак и обед", "half_board"),
        ("1 700 руб. с человека. Данную услугу можно заказать в отеле.", "other"),
    ],
)
def test_meal_kinds(text, meal):
    assert pd.meal_of(text) == meal


def test_price_is_for_the_whole_stay_so_night_price_divides_by_nights():
    offers = pd.parse_offers(row("Без питания", 12400) + row("Без питания", 16000))
    facts = pd.hotel_facts(offers, nights=4)
    assert facts["min_night_rub"] == 3100
    assert facts["median_night_rub"] == 3550
    assert facts["free_cancel_share"] == 0.0


def test_foreign_currency_is_an_error():
    html = row("Без питания", 100).replace("RUB", "USD")
    with pytest.raises(ValueError, match="USD"):
        pd.parse_offers(html)
