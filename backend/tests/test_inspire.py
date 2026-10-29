"""#20: inspiration over the whole catalog.json — theme, region, climate, order, price calls.

Rules only, no model (TRAP-1). The requests are the jury and operator comments of the issue.
"""

import pytest
from talk import say

from api import catalog, inspire
from api.contracts import ChatRequest, TripSpec
from api.extract import extract
from api.fixtures import WARM_WATER_C, future_date
from api.model import chat
from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def inspired(store, text):
    r = say(store, text)
    assert r.stage == "inspire" and r.plan.spec.destinations == [], r.reply
    assert 2 <= len(r.plan.destinations) <= 4
    return r.plan.destinations


def tags(name: str) -> list[str]:
    return catalog.resolve(name)["tags"]


def test_trap_2_warm_sea_in_january_has_no_cold_sea(store):
    """Jury: «тёплое море в январе» gave Сочи and Калининград without a violation."""
    dests = inspired(store, "тёплое море в январе, недорого")
    assert not {"Сочи", "Калининград"} & {d.name for d in dests}
    assert all(d.water_c >= WARM_WATER_C and not d.violates for d in dests)


def test_trap_2_ski_in_february_gives_ski_resorts(store):
    """Jury: «горнолыжка в феврале» gave Казань and Петербург."""
    dests = inspired(store, "горнолыжка в феврале")
    assert sum("ski" in tags(d.name) for d in dests) >= 2


def test_trap_2_mountains_in_summer_with_a_dog(store):
    """Jury: «в горы летом с собакой» gave Казань, Петербург, Калининград."""
    dests = inspired(store, "в горы летом с собакой")
    assert all("mountains" in tags(d.name) for d in dests)


def test_europe_is_a_region_not_a_city(store):
    """Operator (#88): «в Европу» is inspiration, not «нет в каталоге»."""
    dests = inspired(store, "хочу в Европу в мае на неделю")
    assert all(d.country in inspire.EUROPE for d in dests)


def test_south_is_south_of_the_departure_city(store):
    dests = inspired(store, "на юг в ноябре, недорого")
    moscow = catalog.resolve("Москва")
    assert all(d.lat <= moscow["lat"] - inspire.SOUTH_DEG for d in dests)


def test_south_in_a_cold_month_means_warm(store):
    """#235: «на юг в ноябре» gave Белгород 4.9 °C and Волгоград 5.7 °C: south by latitude only."""
    dests = inspired(store, "на юг в ноябре, недорого")
    assert all(d.air_c >= inspire.WARM_AIR_C or d.violates for d in dests)
    spec = TripSpec(origin="Москва", month=11, wishes=["юг"])
    every = inspire.suggest_destinations(spec, 16)
    assert all(d.air_c >= inspire.WARM_AIR_C or d.violates for d in every)
    assert any(not d.violates for d in every)


def test_sea_in_july_does_not_give_a_sea_without_swimming():
    """#224: Санкт-Петербург has `sea`, but the water of its coast is warm for 2 months only."""
    spec = TripSpec(origin="Москва", month=7, wishes=["море"])
    every = inspire.suggest_destinations(spec, 16)
    assert all("swim" in tags(d.name) for d in every if not d.violates)
    spb = [d for d in every if d.name == "Санкт-Петербург"]
    assert all(inspire.NO_SWIM in d.violates for d in spb)
    assert not any(d.why.startswith("море для купания") for d in spb)
    spb = catalog.resolve("Санкт-Петербург")
    hub, _ = catalog.origin_hub("Москва")
    assert inspire.NO_SWIM in inspire._rule_breaks(spec, spb, hub, 7)


def test_cards_that_break_the_request_go_below():
    """Operator: a card marked «Нарушает требования» goes below the fitting ones."""
    spec = TripSpec(origin="Москва", month=11, wishes=["море"], max_travel_hours=4)
    every = inspire.suggest_destinations(spec, 10)
    marks = [bool(d.violates) for d in every]
    assert marks == sorted(marks) and not marks[0]
    # Белек and Анталия are 30 km apart, yet both fit: no breaking card takes the slot of one
    fitting = sum(not d.violates for d in every)
    assert sum(not d.violates for d in inspire.suggest_destinations(spec)) == min(fitting, 4)


def test_card_over_the_budget_is_marked():
    spec = TripSpec(origin="Москва", month=11, wishes=["море"], budget_rub=1000)
    spec = spec.model_copy(update={"date_from": future_date(11, 15)})
    for d in inspire.suggest_destinations(spec):
        assert any(v.startswith("дороже бюджета") for v in d.violates), d.name


def test_shown_places_are_apart_and_two_per_country_at_most():
    spec = TripSpec(origin="Москва", month=1, wishes=["море"])
    dests = inspire.suggest_destinations(spec)
    for i, a in enumerate(dests):
        assert sum(b.country == a.country for b in dests) <= inspire.SHOWN_PER_COUNTRY
        for b in dests[i + 1 :]:
            assert catalog.km_between(vars(a), vars(b)) >= inspire.SPREAD_KM, (a.name, b.name)


def test_only_the_candidates_are_priced(monkeypatch):
    """445 places are not priced on each request: only CANDIDATES of them."""
    calls = []
    monkeypatch.setattr(inspire, "find_offers", lambda spec, name, **kw: calls.append(name) or [])
    inspire.suggest_destinations(TripSpec(origin="Москва", month=7, wishes=["горы"]), 7)
    assert 0 < len(calls) <= inspire.CANDIDATES


def test_reason_and_minuses_come_from_the_catalogue():
    spec = TripSpec(origin="Москва", month=2, wishes=["лыжи"])
    for d in inspire.suggest_destinations(spec):
        assert d.why.startswith("горнолыжный курорт") and d.tradeoffs, d.name


def test_no_trip_goes_below_a_cold_sea_and_a_cold_sea_below_no_sea():
    """S5: «с собакой на машине к морю» in November: a car trip to a cold sea comes first,
    not a warm sea that a car does not reach and not a town without a sea."""
    spec = TripSpec(origin="Москва", month=11, wishes=["море"], modes=["car"], pets=True)
    spec = spec.model_copy(update={"date_from": future_date(11, 7)})
    first = inspire.suggest_destinations(spec)[0]
    assert inspire.NO_PETS not in first.violates and inspire.NO_SEA not in first.violates
    assert not any(v.endswith(inspire.NO_WAY) for v in first.violates), first.violates


C17 = "Устал, хочу тишины и леса, без самолётов, на неделю в ноябре вдвоём"


def test_c17_quiet_forest_no_planes_gives_nature_without_flights(store):
    """C17: «тишины и леса, без самолётов» gave Воронеж, Нижний, Минск and Бухарест (plane)."""
    first = say(store, C17)
    assert "flight" not in first.plan.spec.modes
    assert first.plan.spec.modes, "«без самолётов» must be written to the spec"
    r = chat(ChatRequest(session_id=first.session_id, message="тысяч 60"), store)
    assert r.stage == "inspire", r.reply
    assert "flight" not in r.plan.spec.modes and r.plan.spec.modes
    dests = r.plan.destinations
    assert 2 <= len(dests) <= 4
    assert sum("nature" in tags(d.name) for d in dests) >= 2
    for d in dests:
        place = catalog.resolve(d.name)
        assert "nature" in place["tags"] or place["pop"] < inspire.CITY_POP, d.name
        assert d.name != "Бухарест"
        assert not any(v.endswith(inspire.NO_WAY) for v in d.violates), d.violates
        assert d.travel_hours <= 24, (d.name, d.travel_hours)


def test_nature_wish_with_banned_flight_gives_nature_places_without_big_cities():
    """The pool of «природа» with a banned flight: nature places, no city from a million
    (on main the wish was unknown and the pool was the big cities)."""
    spec = TripSpec(modes=["train", "car"], wishes=["природа"], month=11, origin="Москва")
    dests = inspire.suggest_destinations(spec, 4)
    assert sum("nature" in tags(d.name) for d in dests) >= 2
    for d in dests:
        place = catalog.resolve(d.name)
        assert "nature" in place["tags"] or place["pop"] < inspire.CITY_POP, d.name
        assert not any(v.endswith(inspire.NO_WAY) for v in d.violates), d.violates


@pytest.mark.parametrize(
    "text",
    [
        "К тёплому морю в ноябре вдвоём, не хочу лететь долго, из Москвы, тысяч 150",
        "не хочу лететь больше 4 часов",
        "не хочу лететь с пересадками",
        "без авиабилетов, у нас уже есть",
        "не авиакомпанией Победа",
        "не Аэрофлотом, а Победой",
        "не лоукостером",
    ],
)
def test_c17_long_flight_or_owned_tickets_is_no_flight_ban(text):
    """Review of #277: «не хочу лететь долго» kept the flights; only «без самолётов» bans them."""
    assert not extract(text)[0].get("modes")


@pytest.mark.parametrize(
    "text", ["тихий отдых на море", "тишина и море", "не нужна тишина", "без лесов", "Озерки"]
)
def test_c17_quiet_or_negated_words_are_no_nature_theme(text):
    assert "природа" not in extract(text)[0].get("wishes", [])


def test_c17_warm_sea_with_quiet_rest_keeps_the_sea_pool(store):
    dests = inspired(
        store, "Хочу тихий отдых на тёплом море в ноябре с ребёнком 5 лет, из Москвы, тысяч 150"
    )
    assert all(d.water_c is not None and d.water_c >= WARM_WATER_C for d in dests)


def test_c17_no_flight_phrases_still_ban_the_plane():
    assert extract("не на самолёте, дорого")[0]["modes"] == ["train", "car"]
    assert extract("без самолётов")[0]["modes"] == ["train", "car"]


def test_c17_train_only_from_spb_to_the_sea_keeps_domestic_trains():
    """Review of #277: «поездом из Санкт-Петербурга на море» lost every fitting card to a 24 h cap."""
    spec = TripSpec(modes=["train"], origin="Санкт-Петербург", wishes=["море"], month=7)
    assert any(not d.violates for d in inspire.suggest_destinations(spec, 4))


def test_c17_chosen_car_keeps_the_soft_car_pool():
    """A chosen car is not a flight ban: the pool is that of main (Кобулети, Варна by car)."""
    spec = TripSpec(modes=["car"], origin="Москва", wishes=["море"], month=7)
    names = {d.name for d in inspire.suggest_destinations(spec, 4)}
    assert {"Кобулети", "Варна"} & names


@pytest.mark.parametrize(
    "text",
    [
        "Поездом из Хабаровска куда-нибудь в июле вдвоём, тысяч 80",
        "Поездом из Калининграда в горы на лыжах в феврале вдвоём, тысяч 100",
    ],
)
def test_c17_nothing_reachable_by_train_still_gives_flagged_cards(store, text):
    """Arbiter of #277: an empty filtered pool gave no cards under «Подходящие направления»."""
    dests = inspired(store, text)
    assert any(v.endswith(inspire.NO_WAY) for d in dests for v in d.violates)


def test_warm_in_november_flags_cold_places_and_ranks_them_last(store):
    """#342: «куда-нибудь тепло в ноябре недорого» gave Сочи 15.7 °C and Мостар 15.5 °C."""
    dests = inspired(store, "куда-нибудь тепло в ноябре недорого")
    for d in dests:
        assert d.air_c >= inspire.WARM_ASKED_C or any(
            v.startswith(inspire.NOT_WARM) for v in d.violates
        ), d
    assert any(d.air_c >= inspire.WARM_ASKED_C and not d.violates for d in dests)
    cold = [i for i, d in enumerate(dests) if d.air_c < inspire.WARM_ASKED_C]
    assert all(
        i > max(j for j, d in enumerate(dests) if d.air_c >= inspire.WARM_ASKED_C) for i in cold
    )


def test_where_warm_in_january_has_no_cold_place_unflagged(store):
    dests = inspired(store, "где тепло в январе")
    assert all(d.air_c >= inspire.WARM_ASKED_C or d.violates for d in dests)


def test_warm_over_budget_shows_warm_places_before_cold_ones():
    """No warm place fits the budget: the warm ones stay, flagged «дороже бюджета», above the cold."""
    spec = TripSpec(
        origin="Москва",
        date_from=future_date(11, 7),
        date_to=future_date(11, 14),
        wishes=["тепло"],
        budget_rub=5000,
    )
    dests = inspire.suggest_destinations(spec, 4)
    assert all(d.air_c >= inspire.WARM_ASKED_C for d in dests)
    assert all(any("дороже бюджета" in v for v in d.violates) for d in dests)


def test_warm_sea_wish_has_no_air_threshold():
    """«тёплое море» is the sea wish; its water is checked, not the air (as on main)."""
    assert "тепло" not in extract("тёплое море в ноябре")[0]["wishes"]
    assert "тепло" in extract("хочу погреться в ноябре")[0]["wishes"]


def test_cold_warm_cards_go_from_the_warmest(store):
    """#342 review: «поездом, где тепло, в ноябре» gave Дмитров 1.9 °C instead of Сочи 15.7 °C."""
    spec = TripSpec(modes=["train"], origin="Москва", month=11, wishes=["тепло"])
    dests = inspire.suggest_destinations(spec, 4)
    assert dests[0].air_c == max(d.air_c for d in dests)
    assert [d.air_c for d in dests if d.violates] == sorted(
        (d.air_c for d in dests if d.violates), reverse=True
    )


def test_warm_with_a_pet_pulls_further_candidates_when_all_first_are_dead_ends(store):
    """#342 review: «тепло в ноябре с собакой» gave four cards «с питомцем не собрать»."""
    dests = inspired(store, "тепло в ноябре с собакой из Москвы")
    assert any(inspire.NO_PETS not in d.violates for d in dests)


@pytest.mark.parametrize("text", ["хочу погреться в бане в ноябре", "погреться у камина"])
def test_warm_up_in_a_sauna_or_by_a_fire_is_no_warm_wish(text):
    assert "тепло" not in extract(text)[0].get("wishes", [])


def test_warm_up_by_the_fire_in_any_form_is_no_warm_wish():
    """#356: `ем\\w*` gave the characters back: «погреемся у костра» was a warm wish."""
    for text in ("погреемся у костра в октябре", "погреемся в бане"):
        assert "тепло" not in extract(text)[0].get("wishes", [])
    assert "тепло" in extract("погреемся в ноябре")[0]["wishes"]


@pytest.mark.usefixtures("flights_refuse_pets")  # #575: flights without pets
def test_sea_with_a_dog_refills_from_the_warmest_sea(store):
    """#356: «С собакой на море в ноябре» gave Петергоф 3.5 °C and Витебск «моря нет», not Сочи."""
    dests = inspired(store, "С собакой на море в ноябре из Москвы")
    assert "Сочи" in {d.name for d in dests}
    sea = [i for i, d in enumerate(dests) if "моря нет" not in d.violates]
    assert all(
        i < min(j for j, d in enumerate(dests) if "моря нет" in d.violates) for i in sea
    ) or not any("моря нет" in d.violates for d in dests)


def first_reply(store, text):
    return say(store, text)


def test_c361_northern_lights_are_checked_by_latitude_and_season(store):
    """#361: «северное сияние в декабре» gave Воронеж, Нижний, Минск, Варшава with no wish kept."""
    r = first_reply(store, "северное сияние в декабре")
    assert "северное сияние" in r.plan.spec.wishes
    first = r.plan.destinations[0]
    assert catalog.resolve(first.name)["lat"] >= inspire.AURORA_LAT and not first.violates
    for d in r.plan.destinations:
        place = catalog.resolve(d.name)
        assert place["lat"] >= inspire.AURORA_LAT or any(
            v.startswith(inspire.NO_AURORA) for v in d.violates
        )


def test_c361_mountains_and_sea_gives_a_sea_place_with_mountains_near(store):
    """#361: «горы и море в сентябре из Краснодара» gave Дубай, Манама, Маскат at 40 °C."""
    r = first_reply(store, "горы и море в сентябре из Краснодара")
    names = {d.name for d in r.plan.destinations}
    assert "Сочи" in names
    assert not names & {"Дубай", "Манама", "Маскат", "Джидда"}
    assert not any(d.violates for d in r.plan.destinations)


def test_c361_mountains_and_sea_over_budget_says_so(store):
    r = first_reply(store, "горы и море в июле, тысяч 30")
    assert all(any("дороже бюджета" in v for v in d.violates) for d in r.plan.destinations)
    assert "Нарушает требования: дороже бюджета" in r.reply


def test_c361_romantic_weekend_by_the_sea_is_two_nights(store):
    """#361: «романтический уикенд у моря» was 7 nights; «романтика» is named as not checked."""
    r = first_reply(store, "романтический уикенд у моря")
    assert r.plan.spec.nights == 2
    assert "Не проверяю по данным: романтика" in r.reply
    assert extract("уик-энд в Казани")[0]["nights"] == 2


UNFAMILIAR = [
    ("посмотреть на китов в ноябре", "киты"),
    ("хочу отель с термальными источниками в феврале", "термальные источники"),
    ("съездить на фестиваль в июле", "фестиваль"),
    ("дайвинг в октябре", "дайвинг"),
    ("винные туры осенью, вдвоём", "вино и еда"),
    ("хочу научиться серфингу в августе", "серфинг"),
    ("рыбалка в сентябре на неделю", "рыбалка"),
    ("сафари в ноябре", "сафари"),
    ("шопинг в декабре на выходные", "шопинг"),
    ("йога-ретрит весной", "йога"),
    ("музеи и театры в ноябре", "музеи"),
    ("горнолыжка в феврале на неделю", None),
    ("хочу на море в июле", None),
    ("санаторий в марте", None),
]


@pytest.mark.parametrize(("text", "unchecked"), UNFAMILIAR)
def test_c361_a_wish_is_mapped_or_named_as_not_checked(store, text, unchecked):
    """A wish the catalogue has no field for never vanishes: the reply says «Не проверяю»."""
    r = first_reply(store, text)
    wishes = r.plan.spec.wishes
    assert wishes, r.reply
    if unchecked:
        assert unchecked in wishes
        assert f"Не проверяю по данным: {unchecked}" in r.reply, r.reply
    else:
        assert "Не проверяю" not in r.reply


def test_c361_aurora_includes_reykjavik_and_names_the_season_first():
    feb = TripSpec(origin="Москва", month=2, wishes=["северное сияние", "Европа"])
    reykjavik = next(d for d in inspire.suggest_destinations(feb, 4) if d.name == "Рейкьявик")
    assert not reykjavik.violates
    july = TripSpec(origin="Москва", month=7, wishes=["северное сияние"])
    flags = [v for d in inspire.suggest_destinations(july, 4) for v in d.violates]
    assert flags and all("светлый сезон" in v for v in flags)


@pytest.mark.parametrize(
    "text",
    [
        "без музеев, просто отдохнуть у моря в июле",
        "шопинг не нужен, хочу море в июле",
        "не люблю театры, хочу море в июле",
        "море в июле, на завтрак йогурт",
    ],
)
def test_c361_refused_or_lookalike_words_are_no_wish(text):
    wishes = extract(text)[0].get("wishes", [])
    assert not set(wishes) & set(inspire.UNCHECKED)


@pytest.mark.parametrize(
    "text",
    [
        "дайвинг в Шарм-эль-Шейхе в ноябре на неделю вдвоём",
        "рыбалка в Карелии в сентябре на неделю вдвоём",
    ],
)
def test_c361_named_destination_reply_names_the_unchecked_wish(store, text):
    r = first_reply(store, text)
    assert r.stage in ("compare", "select"), r.stage
    assert "Не проверяю по данным" in r.reply


def test_c361_weekend_with_a_space_and_distance_in_the_reason(store):
    assert extract("уик энд в Казани")[0]["nights"] == 2
    spec = TripSpec(origin="Краснодар", month=9, wishes=["море", "горы"])
    why = " ".join(d.why for d in inspire.suggest_destinations(spec, 4))
    assert " км" in why and "рядом" not in why


def test_c17_train_to_the_sea_from_novosibirsk_says_the_sea_is_out_of_reach(store):
    """#315: Томск, Барнаул, Кемерово с «моря нет» и без слов, что поездом до моря не доехать."""
    r = say(store, "Поездом из Новосибирска на море в августе")
    assert r.stage == "inspire"
    assert all(any("до моря не доехать" in v for v in d.violates) for d in r.plan.destinations)
    assert "поездом до моря не доехать" in r.reply
    assert all(d.travel_hours > 0 for d in r.plan.destinations)
    assert "в пути 0.0 ч" not in r.reply and "в пути 0 ч" not in r.reply
