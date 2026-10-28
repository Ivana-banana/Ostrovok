"""#468: the compare answer names the proposed trip item by item, from the selected offers."""

from zoneinfo import ZoneInfo

import pytest
from talk import say

from api.model import _offer_lines, _rub
from api.store import SessionStore

KAZAN = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё."
OWN = (
    "Казань 11–13 декабря, 2 взрослых, билеты уже есть, "
    "прилетаю 11 декабря в 10:00, улетаю 13 декабря в 18:00"
)


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def points(reply: str) -> list[str]:
    lines = reply.split("\n")
    at = lines.index("Предлагаю:")
    end = next(i for i in range(at, len(lines)) if lines[i].startswith("Итого "))
    return lines[at + 1 : end + 1]


def test_one_line_per_item_with_prices_of_the_selected_offers(store):
    """TRAP-4: every price and time of the list is a field of a selected offer."""
    r = say(store, KAZAN)
    assert r.stage == "compare"
    chosen = [o for o in r.plan.offers if o.offer_id in r.plan.selected]
    hotel = next(o for o in chosen if o.kind == "hotel")
    there, back = sorted((o for o in chosen if o.kind == "transport"), key=lambda o: o.depart_at)
    rides = [o for o in chosen if o.kind == "transfer"]
    lines = points(r.reply)
    assert [line.split(":")[0] for line in lines[:4]] == ["Отель", "Туда", "Обратно", "Трансферы"]
    assert lines[0].startswith(f"Отель: {hotel.title}, ")
    assert lines[0].endswith(f" — {_rub(hotel.price_rub)}")
    for line, leg in ((lines[1], there), (lines[2], back)):
        assert f"{leg.depart_at:%d.%m %H:%M} → {leg.arrive_at:%d.%m %H:%M}" in line
        assert line.endswith(f" — {_rub(leg.price_rub)}")
    assert lines[3].endswith(f" — {_rub(sum(o.price_rub for o in rides))}")
    assert lines[-1] == f"Итого {_rub(sum(o.price_rub for o in chosen))}."


def test_own_tickets_give_no_legs_in_the_list(store):
    r = say(store, OWN)
    assert r.plan.spec.own_transport
    assert not any(line.startswith(("Туда:", "Обратно:")) for line in points(r.reply))


def test_included_names_only_what_the_hotel_offer_says(store):
    """«Включено» comes from the meal and the cancellation of the hotel; none of them — no line."""
    r = say(store, "Сочи из Москвы, 2 взрослых, 10–15 декабря, с завтраком, с бесплатной отменой")
    hotel = next(o for o in r.plan.offers if o.kind == "hotel" and o.offer_id in r.plan.selected)
    assert hotel.meal == "BB" and hotel.cancellation.refundable
    until = hotel.cancellation.free_until.astimezone(ZoneInfo(hotel.tz))
    assert f"Включено: завтраки, бесплатная отмена до {until:%d.%m}." in points(r.reply)
    assert not any(line.startswith("Включено:") for line in points(say(store, KAZAN).reply))


def test_paid_extras_are_items_so_the_items_sum_to_the_total(store):
    """A selected extra («Ранний заезд») is in «Итого», so it is an item with its price."""
    r = say(store, KAZAN)
    hotel = next(o for o in r.plan.offers if o.kind == "hotel" and o.offer_id in r.plan.selected)
    extra = next(o for o in r.plan.offers if o.kind == "extra" and o.for_offer_id == hotel.offer_id)
    plan = r.plan.model_copy(update={"selected": [*r.plan.selected, extra.offer_id]})
    lines = _offer_lines(plan)[1:]
    assert f"Доп. услуга: {extra.title} — {_rub(extra.price_rub)}" in lines
    prices = [int(line.rsplit(" — ", 1)[1][:-2].replace(" ", "")) for line in lines[:-1]]
    assert lines[-1] == f"Итого {_rub(sum(prices))}."
