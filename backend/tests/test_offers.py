"""Offer providers (T5, #9) through the public functions of api.offers."""

import re
import socket
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from talk import say

from api import catalog, offers
from api.contracts import ChatRequest, TripSpec
from api.fixtures import rank, travel_hours
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
    r = say(store, "Хочу в Стамбул, 4 ночи с 10 декабря, двое взрослых")
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


def test_meal_and_refund_wishes_do_not_change_the_offers():
    """The re-plan DAG answers meal and refund edits by re-ranking, without a new search."""
    plain = find_offers(spec(), "Сочи")
    assert find_offers(spec(meal="AI", refundable=True), "Сочи") == plain
    hotels = kinds(plain, "hotel")
    assert {"AI", "BB"} <= {h.meal for h in hotels}  # a sea place: breakfast and all inclusive
    assert any(h.cancellation.refundable for h in hotels)


def hotel_tariffs(items) -> dict[str, list]:
    """Hotel tariffs by hotel title."""
    out: dict[str, list] = {}
    for h in kinds(items, "hotel"):
        out.setdefault(h.title, []).append(h)
    return out


@pytest.mark.parametrize("place", ["Сочи", "Казань", "Анапа", "Рим"])
def test_every_hotel_has_two_to_four_tariffs_with_meal_and_cancellation(place):
    """#39: a hotel sells RO or BB (HB, AI at the sea), with and without free cancellation."""
    items = find_offers(spec(), place)
    by_title = hotel_tariffs(items)
    assert len(by_title) >= 3
    for tariffs in by_title.values():
        assert 2 <= len(tariffs) <= 4
        assert len({(t.meal, t.cancellation.refundable) for t in tariffs}) == len(tariffs)
        assert len({t.offer_id for t in tariffs}) == len(tariffs)
        assert len({t.meal for t in tariffs}) == 2
        # one hotel, one room: the same facts, only the tariff differs
        assert len({(t.stars, t.rating, t.distance_center_km, t.date_in) for t in tariffs}) == 1
    assert any(t.cancellation.refundable for ts in by_title.values() for t in ts)
    assert any(not t.cancellation.refundable for ts in by_title.values() for t in ts)
    assert any(t.meal == "BB" for ts in by_title.values() for t in ts)
    assert any(t.meal == "RO" for ts in by_title.values() for t in ts)


def test_tariff_prices_follow_meal_and_cancellation():
    """Same hotel: a meal plan with more food costs more; free cancellation costs more."""
    for tariffs in hotel_tariffs(find_offers(spec(), "Сочи")).values():
        by_key = {(t.meal, t.cancellation.refundable): t.price_rub for t in tariffs}
        for (meal, refundable), price in by_key.items():
            if refundable:
                assert price > by_key[(meal, False)]
        plain = sorted((p, m) for (m, r), p in by_key.items() if not r)
        assert plain[0][1] in ("RO", "BB")  # the cheapest rate has the lightest meal plan


def test_tariffs_are_the_same_in_every_process():
    """Determinism: ids and prices are pinned, so a seed that depends on the process
    (hash(), random) fails here."""
    s = spec(date_from=date(2026, 12, 11), date_to=date(2026, 12, 14))
    hotels = kinds(find_offers(s, "Сочи"), "hotel")
    first = [(h.offer_id, h.price_rub) for h in hotels[:2]]
    assert first == [
        ("h-aer-s0-ROn-20261211-3n-2a", 4_700),  # Сочи level 2200 from 101hotels (#119)
        ("h-aer-s0-BBn-20261211-3n-2a", 6_400),
    ]
    assert hotels == kinds(find_offers(s, "Сочи"), "hotel")


def test_s3_breakfast_within_the_budget_picks_a_breakfast_tariff():
    """S3-A: «до 40 000, нужен завтрак» — a tariff with breakfast, the plan in the budget.
    The offers are the same as without the wishes; the ranking picks the tariff."""
    plain = spec(destinations=["Сочи"], date_from=date(2026, 11, 20), date_to=date(2026, 11, 23))
    wishes = plain.model_copy(update={"meal": "BB", "budget_rub": 40_000})
    items = find_offers(wishes, "Сочи")
    assert items == find_offers(plain, "Сочи")
    selected, _ = rank(wishes, items)
    by_id = {o.offer_id: o for o in items}
    hotel = next(by_id[i] for i in selected if by_id[i].kind == "hotel")
    assert hotel.meal in ("BB", "HB", "FB", "AI")
    assert sum(by_id[i].price_rub for i in selected) <= 40_000
    refundable = plain.model_copy(update={"refundable": True})
    selected, _ = rank(refundable, items)
    hotel = next(by_id[i] for i in selected if by_id[i].kind == "hotel")
    assert hotel.cancellation.refundable


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


@pytest.mark.parametrize("name, iata", [("Домбай", "MRV"), ("Сочи", "AER")])
def test_flight_and_transfer_titles_show_the_arrival_airport_code(name, iata):
    items = find_offers(spec(), name)
    there = [o for o in kinds(items, "transport") if o.mode == "flight" and o.destination == name]
    # Домбай is 201 km from MRV: no airport of its own, the flight goes through MRV (#123)
    label = f"→ {name} через {iata}," if name == "Домбай" else f"→ {name} ({iata}),"
    assert there and all(label in o.title for o in there)
    ports = [t.title for t in kinds(items, "transfer") if "аэропорт" in t.title]
    assert ports and all(f"аэропорт {iata}" in t for t in ports)


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

    catalog.snapshot.cache_clear()
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


def test_clock_only_removes_the_free_cancellation_rate_after_its_deadline():
    """Before the deadline a hotel sells both rates; after it, only the non-refundable one,
    with the same ids and prices. No hotel sells two tariffs with the same terms."""
    s = spec()
    early = datetime(2000, 1, 1, tzinfo=ZoneInfo("UTC"))
    late = datetime.combine(s.date_from, datetime.min.time(), ZoneInfo("UTC"))
    a = kinds(find_offers(s, "Казань", now=early), "hotel")
    b = kinds(find_offers(s, "Казань", now=late), "hotel")
    assert any(h.cancellation.refundable for h in a)
    assert not any(h.cancellation.refundable for h in b)
    assert [h for h in a if not h.cancellation.refundable] == b  # the same ids, prices, terms
    assert 2 <= len(b) < len(a)
    assert find_offers(s, "Казань", now=early) == find_offers(s, "Казань", now=early)


@pytest.mark.parametrize("days", [0, 1, 2, 5])
def test_near_dates_do_not_double_a_non_refundable_tariff(days):
    """After the deadline a hotel has no 'refundable' tariff that is really non-refundable."""
    day = date.today() + timedelta(days=days)
    s = spec(date_from=day, date_to=day + timedelta(days=2))
    for tariffs in hotel_tariffs(find_offers(s, "Сочи")).values():
        terms = [(t.meal, t.cancellation.refundable) for t in tariffs]
        assert len(terms) == len(set(terms)) and 2 <= len(terms) <= 4
        for t in tariffs:  # the id says «r» only for a really refundable tariff
            assert (re.search(r"-(RO|BB|HB|AI)r-", t.offer_id) is not None) == (
                t.cancellation.refundable
            )


def test_travel_hours_is_the_fastest_leg_there_and_zero_at_home():
    """No date in the spec: the 15th of the month is the probe; the origin itself is 0 hours."""
    s = TripSpec(month=11, origin="Москва")
    msk, kzn = catalog.resolve("Москва"), catalog.resolve("Казань")
    assert 0 < travel_hours(s, msk, kzn) < 3 and travel_hours(s, msk, msk) == 0


@pytest.mark.parametrize("share, refundable", [(0.0, False), (1.0, True)])
def test_free_cancel_share_of_the_place_sets_the_refundable_hotels(share, refundable):
    """#119: a place with the 101hotels share sells free cancellation in that share of its
    3+ star hotels; 1-2 star hotels never sell it."""
    place = {"name": "Тест", "lat": 55.0, "lon": 37.0, "tz": "Europe/Moscow",
             "free_cancel_share": share}  # fmt: skip
    by_title = hotel_tariffs(offers_for_place(spec(), place))
    sold = {
        any(t.cancellation.refundable for t in ts) for ts in by_title.values() if ts[0].stars >= 3
    }
    assert sold == {refundable}
