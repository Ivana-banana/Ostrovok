"""#549 (TRAP-5): the timeline names the airport a flight lands at, not the far place."""

from datetime import date

from api.contracts import Plan, TripSpec
from api.fixtures import rank
from api.offers import find_offers
from api.optimizer import pick_hotel
from api.timeline import build_timeline


def titles(city: str, origin: str) -> list[str]:
    spec = TripSpec(
        origin=origin,
        destinations=[city],
        date_from=date(2027, 2, 10),
        date_to=date(2027, 2, 17),
        nights=7,
    )
    offers = find_offers(spec, city)
    ranked, _ = rank(spec, offers)
    hotel = next(i for i in ranked if i.startswith("h-"))
    legs = [o for o in offers if o.kind == "transport" and o.mode == "flight"]
    there = next(o for o in legs if o.destination == city)
    back = next(o for o in legs if o.origin == city)
    selected = pick_hotel([there.offer_id, back.offer_id], offers, hotel)
    events, _ = build_timeline(Plan(spec=spec, offers=offers, selected=selected))
    return [e.title for e in events]


def test_trap5_far_airport_is_named_by_its_city_and_code():
    got = titles("Бакуриани", "Санкт-Петербург")
    assert "Прилёт: Кутаиси (KUT)" in got
    assert not any(t.startswith("Прилёт: Бакуриани") for t in got)
    assert not any(t.startswith("Вылет:") and "→ Бакуриани" in t for t in got)
    assert any(t.startswith("Вылет: Кутаиси (KUT) →") for t in got)


def test_trap5_own_airport_keeps_the_place_name():
    got = titles("Казань", "Москва")
    assert "Прилёт: Казань" in got


def test_trap5_closed_airport_names_the_replacement_not_the_catalogue_one():
    # Белгород has VOZ in the catalogue; VOZ is closed, the offers fly through DME
    got = titles("Белгород", "Владивосток")
    assert not any("VOZ" in t for t in got)
    assert any(t.startswith("Прилёт:") and "DME" in t for t in got)
    got = titles("Воронеж", "Владивосток")
    assert not any("VOZ" in t for t in got)
    assert any(t.startswith("Прилёт:") and "DME" in t for t in got)
