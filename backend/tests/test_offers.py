"""Offer providers (T5, #9) through the public functions of api.offers."""

import socket
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from api import offers
from api.contracts import ChatRequest, TripSpec
from api.fixtures import rank
from api.guards import label_synthetic
from api.model import chat
from api.offers import find_offers, offers_for_place, resolve_place, transfer_id
from api.store import SessionStore

SOON = date.today() + timedelta(days=60)


def spec(**kw) -> TripSpec:
    data = {"origin": "Москва", "date_from": SOON, "date_to": SOON + timedelta(days=3)}
    return TripSpec(nights=3, **{**data, **kw})


def kinds(items, kind):
    return [o for o in items if o.kind == kind]


def kazan_weekend() -> list:
    """S6: Moscow -> Kazan, 2 adults, 11–13 December."""
    s = spec(destinations=["Казань"], date_from=date(2026, 12, 11), date_to=date(2026, 12, 13))
    return find_offers(s, "Казань")


def test_trap_2_any_coordinates_get_hotels_transport_transfers_and_extras():
    """TRAP-2: a place outside every snapshot still gets offers by its coordinates."""
    place = {"name": "Зурбаган", "lat": 61.0, "lon": 69.0, "tz": "Asia/Yekaterinburg"}
    items = offers_for_place(spec(), place, resolve_place("Москва"))
    hotels = kinds(items, "hotel")
    assert len(hotels) >= 3 and all(h.city == "Зурбаган" for h in hotels)
    legs = kinds(items, "transport")
    assert any(o.destination == "Зурбаган" for o in legs)
    assert any(o.origin == "Зурбаган" for o in legs)
    assert kinds(items, "transfer") and kinds(items, "extra")


def test_trap_2_city_outside_the_old_catalogue_reaches_booking(tmp_path):
    """TRAP-2, S5: Istanbul was not in the 7-city fixture; now the chat books it."""
    store = SessionStore(tmp_path / "s.db")
    r = chat(ChatRequest(message="Хочу в Стамбул, 4 ночи с 10 декабря, двое взрослых"), store)
    assert r.stage == "compare" and len(kinds(r.plan.offers, "hotel")) >= 3
    for text in ("беру", "да", "подтверждаю"):
        r = chat(ChatRequest(session_id=r.session_id, message=text), store)
    assert r.stage == "booked"
    assert r.voucher.total_rub == sum(o.price_rub for o in r.voucher.items) > 0


@pytest.mark.parametrize(
    "written, name",
    [
        ("Анталья", "Анталия"),
        ("antalya", "Анталия"),
        ("Нижнем Новгороде", "Нижний Новгород"),
        ("Екатеринбурге", "Екатеринбург"),
        ("Питер", "Санкт-Петербург"),
        ("Грузию", "Тбилиси"),
        ("Стамбуле", "Стамбул"),
        ("Санкт-Петербурге", "Санкт-Петербург"),
        ("Турцию", "Анталия"),
        ("Турции", "Анталия"),
        ("Египте", "Хургада"),
        ("Египта", "Хургада"),
    ],
)
def test_trap_3_spelling_and_case_variants_resolve(written, name):
    """TRAP-3: spelling variants and Russian case endings find the same place."""
    assert resolve_place(written)["name"] == name


def test_unknown_name_has_no_offers_and_no_false_match():
    assert find_offers(spec(), "Зурбаган") == []
    assert resolve_place("Малага") is None  # not «Мале»


def test_same_trip_gives_the_same_offers():
    s = spec(destinations=["Сочи"], children_ages=[5])
    assert find_offers(s, "Сочи") == find_offers(s, "Сочи")


def test_new_dates_change_prices_but_keep_the_hotels():
    a = kinds(find_offers(spec(), "Сочи"), "hotel")
    later = SOON + timedelta(days=30)
    b = kinds(
        find_offers(spec(date_from=later, date_to=later + timedelta(days=3)), "Сочи"), "hotel"
    )
    assert [h.title for h in a] == [h.title for h in b]
    assert [h.price_rub for h in a] != [h.price_rub for h in b]


def test_more_guests_cost_more():
    two = kinds(find_offers(spec(), "Казань"), "hotel")
    three = kinds(find_offers(spec(children_ages=[7]), "Казань"), "hotel")
    assert all(b.price_rub > a.price_rub for a, b in zip(two, three, strict=True))
    legs = sorted(kinds(find_offers(spec(), "Казань"), "transport"), key=lambda o: o.offer_id)
    more = sorted(
        kinds(find_offers(spec(adults=3), "Казань"), "transport"), key=lambda o: o.offer_id
    )
    assert all(b.price_rub > a.price_rub for a, b in zip(legs, more, strict=True))


def test_meal_and_refund_wishes_shape_the_tariff():
    hotels = kinds(find_offers(spec(meal="BB", refundable=True), "Казань"), "hotel")
    assert all(h.meal == "BB" for h in hotels)
    assert all(h.cancellation.refundable and h.cancellation.free_until for h in hotels)


def test_passed_cancellation_deadline_is_not_refundable():
    today = date.today()
    s = spec(date_from=today, date_to=today + timedelta(days=1), refundable=True)
    assert not any(h.cancellation.refundable for h in kinds(find_offers(s, "Казань"), "hotel"))


def test_trap_7_every_offer_is_synthetic_and_labelled():
    """TRAP-7: every price is marked synthetic; hotel text says so too."""
    items = find_offers(spec(), "Сочи")
    assert items and all(o.synthetic for o in items)
    assert all(label_synthetic(o.model_dump())["label"] == "синтетика" for o in items)
    assert all("синтетика" in h.description for h in kinds(items, "hotel"))


def test_trap_4_prices_times_and_ids_come_only_from_the_provider():
    """TRAP-4: ids are unique and link up; the ranker picks only provider ids."""
    s = spec(destinations=["Казань"])
    items = find_offers(s, "Казань")
    ids = [o.offer_id for o in items]
    assert len(ids) == len(set(ids)) and all(o.price_rub > 0 for o in items)
    hotels = kinds(items, "hotel")
    legs = [o for o in kinds(items, "transport") if o.mode != "car"]
    assert {x.for_offer_id for x in kinds(items, "extra")} == {h.offer_id for h in hotels}
    links = {transfer_id(h.offer_id, leg.offer_id) for h in hotels for leg in legs}
    assert {t.offer_id for t in kinds(items, "transfer")} == links
    selected, _ = rank(s, items)
    assert selected and set(selected) <= set(ids)


def test_trap_5_times_are_local_and_transfers_dock():
    """TRAP-5: arrival is in the zone of the place; transfers start after arrival and
    reach the airport 2 h before the flight back."""
    items = find_offers(spec(), "Екатеринбург")
    tz = ZoneInfo("Asia/Yekaterinburg")
    assert all(h.tz == "Asia/Yekaterinburg" for h in kinds(items, "hotel"))
    flights = [o for o in kinds(items, "transport") if o.mode == "flight"]
    there = [o for o in flights if o.destination == "Екатеринбург"]
    back = [o for o in flights if o.origin == "Екатеринбург"]
    assert there and back
    for o in there:
        assert o.arrive_at.utcoffset() == o.arrive_at.astimezone(tz).utcoffset()
        assert o.depart_at.utcoffset() == timedelta(hours=3)  # Moscow
        assert o.arrive_at > o.depart_at
    by_id = {t.offer_id: t for t in kinds(items, "transfer")}
    hotels = kinds(items, "hotel")

    def of(leg):  # the transfers of this leg, one per hotel
        return [by_id[transfer_id(h.offer_id, leg.offer_id)] for h in hotels]

    for leg in there:
        assert all(t.depart_at >= leg.arrive_at + timedelta(minutes=40) for t in of(leg))
    for leg in back:
        assert all(t.arrive_at == leg.depart_at - timedelta(hours=2) for t in of(leg))


def test_s6_kazan_has_morning_train_and_flight_and_a_rich_comparison():
    """S6: night train arrives 07:10, flight 10:30; ≥ 3 hotels with ≥ 5 features."""
    items = kazan_weekend()
    arrivals = {
        (o.mode, o.arrive_at.strftime("%d %H:%M"))
        for o in kinds(items, "transport")
        if o.destination == "Казань"
    }
    assert ("train", "11 07:10") in arrivals and ("flight", "11 10:30") in arrivals
    hotels = kinds(items, "hotel")
    assert len(hotels) >= 3
    for h in hotels:
        assert h.price_rub and h.meal and h.distance_center_km is not None
        assert h.stars and h.rating and h.cancellation is not None
    early = [x for x in kinds(items, "extra") if x.offer_id.endswith("-early")]
    assert len(early) == len(hotels)


def test_osm_snapshot_names_kazan_hotels():
    """Kazan hotels come from the OpenStreetMap snapshot, not from synthetic names."""
    hotels = kinds(kazan_weekend(), "hotel")
    assert any(h.title.startswith("Корстон") and h.stars == 5 for h in hotels)
    assert all("OpenStreetMap" in h.description for h in hotels)
    assert any("wheelchair" in h.amenities for h in hotels)  # DoubleTree: wheelchair=yes


def test_trap_6_offers_need_no_network(monkeypatch):
    """TRAP-6: snapshots are files; offers work with the network down."""

    def no_network(*args, **kwargs):
        raise OSError("network is down")

    offers.places.cache_clear()
    offers.osm_hotels.cache_clear()
    monkeypatch.setattr(socket.socket, "connect", no_network)
    assert kinds(find_offers(spec(), "Казань"), "hotel")


def test_modes_filter_and_a_car_for_a_dog():
    trains = kinds(find_offers(spec(modes=["train"]), "Казань"), "transport")
    assert trains and all(o.mode == "train" for o in trains)
    cars = kinds(find_offers(spec(modes=["car"], pets=True), "Сочи"), "transport")
    assert cars and all(o.mode == "car" and o.pets_allowed for o in cars)
    assert not kinds(find_offers(spec(modes=["car"]), "Сочи"), "transfer")


def test_sea_place_has_beach_and_pet_friendly_hotels():
    hotels = kinds(find_offers(spec(), "Анапа"), "hotel")
    assert any("beach" in h.amenities for h in hotels)
    assert any(h.pets_allowed for h in hotels)


def test_no_direct_flight_to_rome_has_a_change():
    flights = [o for o in kinds(find_offers(spec(), "Рим"), "transport") if o.mode == "flight"]
    assert flights and all(o.changes == 1 for o in flights)


def test_return_leg_is_on_the_last_day():
    s = spec()
    back = [o for o in kinds(find_offers(s, "Казань"), "transport") if o.origin == "Казань"]
    assert back and all(o.depart_at.date() == s.date_to for o in back)
    assert all(isinstance(o.depart_at, datetime) for o in back)


def test_trap_5_overnight_train_across_a_dst_change_keeps_real_duration():
    """TRAP-5: the night of 28 March 2027 is one hour shorter in Berlin; the trip is not."""
    a = {"name": "Альфа", "lat": 52.52, "lon": 13.40, "tz": "Europe/Berlin", "station_km": 2}
    b = {"name": "Бета", "lat": 48.14, "lon": 11.58, "tz": "Europe/Berlin", "station_km": 2}

    def night_train(day):
        s = spec(date_from=day, date_to=day + timedelta(days=2), modes=["train"])
        legs = kinds(offers_for_place(s, b, a), "transport")
        return next(o for o in legs if o.destination == "Бета" and o.arrive_at.hour == 7)

    dst, plain = night_train(date(2027, 3, 28)), night_train(date(2027, 3, 21))
    utc = ZoneInfo("UTC")

    def ride(o):
        return o.arrive_at.astimezone(utc) - o.depart_at.astimezone(utc)

    assert ride(dst) == ride(plain)
    assert dst.arrive_at.utcoffset() == timedelta(hours=2)
    assert dst.depart_at.utcoffset() == timedelta(hours=1)


def test_trap_7_real_osm_hotel_has_no_made_up_amenities():
    """TRAP-7: an OSM hotel gets a feature only from its tag; a synthetic one says so."""
    hotels = {h.title.split(",")[0]: h for h in kinds(kazan_weekend(), "hotel")}
    korston = hotels["Корстон"]  # OSM: no wheelchair, pets or other amenity tags
    assert korston.amenities == [] and not korston.pets_allowed
    assert hotels["DoubleTree"].amenities == ["wheelchair"]  # wheelchair=yes in OSM
    synthetic = kinds(find_offers(spec(), "Анапа"), "hotel")
    assert all("удобства — синтетика" in h.description for h in synthetic)


def test_clock_changes_only_the_cancellation_state():
    """The same request gives the same ids and prices before and after a deadline."""
    s = spec(refundable=True)
    early = datetime(2000, 1, 1, tzinfo=ZoneInfo("UTC"))
    late = datetime.combine(s.date_from, datetime.min.time(), ZoneInfo("UTC"))
    a = kinds(find_offers(s, "Казань", now=early), "hotel")
    b = kinds(find_offers(s, "Казань", now=late), "hotel")
    assert [(h.offer_id, h.price_rub) for h in a] == [(h.offer_id, h.price_rub) for h in b]
    assert all(h.cancellation.refundable for h in a)
    assert not any(h.cancellation.refundable for h in b)
    assert find_offers(s, "Казань", now=early) == find_offers(s, "Казань", now=early)
