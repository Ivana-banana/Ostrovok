"""#123: a question about a named city at a built plan gets its climate and its airport.

The climate of the month and the nearest airport come from catalog.json (or the geocoder place),
never from the model (TRAP-4). A city without data gets «нет данных». A question about another
city is a question, not an edit: the plan, the cart and the stage stay and it is no consent.
"""

import pytest
from talk import say

from api import answers, catalog, llm
from api.contracts import Plan, TripSpec
from api.offers import resolve_place
from api.store import SessionStore

SOCHI = "Сочи из Москвы с 3 по 8 декабря, двое взрослых"
VYBORG = "Выборг из Москвы с 3 по 8 декабря, двое взрослых"
STAGES = ["compare", "assemble", "checkout"]
OTHER_CITY_WEATHER = [
    "какая погода в Анталии?",
    "а в Анталии тепло?",
    "какой климат в Анталии?",
    "а как там в Анталии с погодой?",
]


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def at(store, stage, start=SOCHI):
    r = say(store, start)
    for text in ("беру", "да")[: {"compare": 0, "assemble": 1, "checkout": 2}[stage]]:
        r = say(store, text, r.session_id)
    assert r.stage == stage
    return r


def climate_line(name, month, word, rain=False):
    """The line the catalogue gives for a place and a month: the numbers are not typed here."""
    place = catalog.resolve(name)
    air, water = catalog.month_climate(place, month)
    fact = f"днём {air:g} °C" + (f", вода {water:g} °C" if water is not None else "")
    if rain:
        fact += f", осадки {place['climate']['rain'][month - 1]:g} мм за месяц"
    return f"{name} {word}: {fact} — оценка по каталогу, не прогноз."


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("question", OTHER_CITY_WEATHER)
def test_trap_4_weather_of_another_city_is_a_question(store, stage, question):
    """«Какая погода в Анталии?» at a Сочи plan: the climate of Анталия, the plan stays."""
    r = at(store, stage)
    r2 = say(store, question, r.session_id)
    assert climate_line("Анталия", 12, "в декабре") in r2.reply, r2.reply
    assert "Сочи в декабре" not in r2.reply  # the question is about the other city
    assert r2.stage == stage and r2.plan == r.plan and r2.cart == r.cart
    assert r2.plan.spec.destinations == ["Сочи"]
    assert r2.llm is False and r2.voucher is None
    assert store.get(r.session_id) == r2


@pytest.mark.parametrize("stage", ["compare", "assemble"])
def test_question_about_another_city_is_not_consent(store, stage):
    r = at(store, stage)
    r2 = say(store, "какая погода в Анталии?", r.session_id)
    r3 = say(store, {"compare": "беру", "assemble": "да"}[stage], r2.session_id)
    assert r3.stage == {"compare": "assemble", "assemble": "checkout"}[stage]
    assert r3.plan.spec.destinations == ["Сочи"]


def test_weather_of_another_city_asks_the_rain_when_asked(store):
    r = at(store, "assemble")
    reply = say(store, "сколько осадков в Анталии?", r.session_id).reply
    assert climate_line("Анталия", 12, "в декабре", rain=True) in reply, reply


def test_weather_of_another_city_and_month_keeps_the_dates(store):
    """«Погода в Анталии в январе?»: the January climate, the December dates stay."""
    r = at(store, "compare")
    r2 = say(store, "какая погода в Анталии в январе?", r.session_id)
    assert climate_line("Анталия", 1, "в январе") in r2.reply, r2.reply
    assert r2.plan == r.plan and r2.stage == "compare"


def test_blocker_departure_airport_is_the_airport_of_the_origin(store):
    """«Из какого аэропорта вылет?» at a Сочи plan: the airport of Москва, never AER."""
    r = at(store, "assemble")
    port = catalog.resolve("Москва")["airport"]
    for q in (
        "из какого аэропорта вылет?",
        "какой аэропорт вылета?",
        "с какого аэропорта вылетаем?",
    ):
        r2 = say(store, q, r.session_id)
        assert f"Москва: ближайший аэропорт {port['iata']}, {port['km']} км" in r2.reply, r2.reply
        assert "AER" not in r2.reply and "Сочи:" not in r2.reply, r2.reply
        assert r2.plan == r.plan and r2.cart == r.cart and r2.stage == "assemble"


def test_departure_city_in_the_question_is_still_an_edit(store):
    r = at(store, "compare")
    r2 = say(store, "вылет из Казани, какой аэропорт?", r.session_id)
    assert r2.plan.spec.origin == "Казань", r2.reply


@pytest.mark.parametrize(
    ("question", "name"),
    [
        ("какой аэропорт у Новороссийска?", "Новороссийск"),
        ("какая погода в Новороссийске?", "Новороссийск"),
        ("погода в Новом Афоне?", "Новом Афоне"),
    ],
)
def test_blocker_city_named_new_is_a_city(store, question, name):
    """A name that starts with «Нов» is a city, not «Новый год»: never the answer for Сочи."""
    r = at(store, "compare")
    r2 = say(store, question, r.session_id)
    assert name in r2.reply, r2.reply
    assert "AER" not in r2.reply and "Сочи в декабре" not in r2.reply, r2.reply
    assert r2.plan == r.plan


@pytest.mark.parametrize("text", ["какая погода на Новый год?", "какая погода в Новом году?"])
def test_new_year_is_no_city(text):
    """Only «Новый год» is a time: no place is named (the re-plan of the holiday is as before)."""
    plan = Plan(spec=TripSpec(destinations=["Сочи"], month=12))
    assert answers._named_places(plan, text, {}) == []


@pytest.mark.parametrize("question", ["а в Мае тепло?", "а в Июле там жарко?", "а в Марте как?"])
def test_a_capitalized_month_is_no_city(store, question):
    r = at(store, "compare")
    r2 = say(store, question, r.session_id)
    assert ": климата" not in r2.reply, r2.reply  # #212: the no-data line


@pytest.mark.parametrize(
    "text", ["махнём в Анталию, там тепло?", "а если в Анталию, какая погода?"]
)
def test_a_wish_to_go_still_changes_the_destination(store, text):
    r = at(store, "compare")
    assert say(store, text, r.session_id).plan.spec.destinations == ["Анталия"]


def test_weather_of_the_plan_city_is_as_before(store):
    r = at(store, "assemble")
    reply = say(store, "какая погода в Сочи?", r.session_id).reply
    assert climate_line("Сочи", 12, "в декабре") in reply, reply
    assert "Анталия" not in reply


def test_trap_4_unknown_city_has_no_data(store):
    """A name that is in no data: «нет данных», not the climate of the plan city."""
    r = at(store, "compare")
    r2 = say(store, "какая погода в Зурбагане?", r.session_id)
    assert "нет данных" in r2.reply and "Зурбаган" in r2.reply, r2.reply
    assert "Сочи в декабре" not in r2.reply
    assert r2.plan == r.plan and r2.stage == "compare"


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize(
    "question",
    [
        "какой ближайший аэропорт у Анталии?",
        "а аэропорт в Анталии далеко?",
        "есть ли аэропорт в Анталии?",
    ],
)
def test_airport_of_another_city_is_a_question(store, stage, question):
    r = at(store, stage)
    r2 = say(store, question, r.session_id)
    port = catalog.resolve("Анталия")["airport"]
    assert f"Анталия: ближайший аэропорт {port['iata']}, {port['km']} км" in r2.reply, r2.reply
    assert r2.stage == stage and r2.plan == r.plan and r2.cart == r.cart
    assert r2.plan.spec.destinations == ["Сочи"] and r2.plan.spec.origin == "Москва"
    assert r2.llm is False


def test_airport_without_a_name_is_the_plan_city(store):
    r = at(store, "assemble")
    reply = say(store, "какой там ближайший аэропорт?", r.session_id).reply
    port = catalog.resolve("Сочи")["airport"]
    assert f"Сочи: ближайший аэропорт {port['iata']}, {port['km']} км" in reply, reply


def test_trap_4_far_airport_is_through_the_hub(store):
    """Выборг has no airport of its own: LED, 131 km. The flight of the plan goes through LED."""
    r = at(store, "compare", VYBORG)
    assert any(o.mode == "flight" for o in r.plan.offers if o.kind == "transport")
    reply = say(store, "какой ближайший аэропорт?", r.session_id).reply
    port = catalog.resolve("Выборг")["airport"]
    assert port["iata"] == "LED" and port["km"] == 131
    assert "Выборг: своего аэропорта нет, ближайший LED, 131 км по прямой." in reply, reply
    assert "через LED" in reply and "Москва → Выборг" in reply
    assert "Прилёт" not in reply and "пересадк" not in reply  # no invented connection (TRAP-5)


def test_trap_4_far_airport_of_another_city_names_no_flight(store):
    """The plan has no flight to Выборг: the answer states the airport and invents no route."""
    r = at(store, "compare")
    reply = say(store, "какой аэропорт у Выборга?", r.session_id).reply
    assert "Выборг: своего аэропорта нет, ближайший LED, 131 км по прямой." in reply, reply
    assert "через LED" not in reply
    assert r.plan == say(store, "погода?", r.session_id).plan


@pytest.mark.parametrize(
    ("city", "gen", "iata", "km", "how"),
    [
        ("Владикавказ", "Владикавказа", "OGZ", 26, "по дороге"),
        ("Мехико", "Мехико", "MEX", 8, "по дороге"),
        ("Сочи", "Сочи", "AER", 25, "по прямой"),
    ],
)
def test_trap_4_airport_km_say_how_they_were_measured(store, city, gen, iata, km, how):
    """#266: «по дороге» only when the catalogue km are by road (`road`), else «по прямой»."""
    port = catalog.resolve(city)["airport"]
    assert (port["iata"], port["km"], bool(port.get("road"))) == (iata, km, how == "по дороге")
    r = at(store, "compare")
    reply = say(store, f"какой аэропорт у {gen}?", r.session_id).reply
    other = "по прямой" if how == "по дороге" else "по дороге"
    assert f"{city}: ближайший аэропорт {iata}, {km} км от города ({how})." in reply, reply
    assert other not in reply
    line = answers.destination_line(Plan(spec=TripSpec(destinations=[city], month=12)))
    assert f"ближайший аэропорт {iata}, {km} км {how}" in line, line


def test_trap_4_far_airport_by_road_says_so(monkeypatch):
    place = dict(catalog.resolve("Выборг"))
    monkeypatch.setattr(
        answers,
        "resolve_place",
        lambda name: {**place, "airport": {**place["airport"], "road": True}},
    )
    reply = answers.airport_fact(Plan(spec=TripSpec(destinations=["Выборг"])))
    assert "своего аэропорта нет, ближайший LED, 131 км по дороге." in reply, reply


def test_trap_4_airport_of_unknown_city_has_no_data(store):
    r = at(store, "compare")
    r2 = say(store, "какой ближайший аэропорт у Зурбагана?", r.session_id)
    assert "нет данных" in r2.reply and "Зурбаган" in r2.reply, r2.reply
    assert "Сочи: ближайший" not in r2.reply
    assert r2.plan == r.plan and r2.stage == "compare"


def test_weather_and_airport_in_one_question(store):
    r = at(store, "assemble")
    reply = say(store, "какая погода в Анталии и какой там аэропорт?", r.session_id).reply
    port = catalog.resolve("Анталия")["airport"]
    assert climate_line("Анталия", 12, "в декабре") in reply
    assert f"Анталия: ближайший аэропорт {port['iata']}, {port['km']} км" in reply
    assert r.plan == say(store, "погода?", r.session_id).plan


def tikhvin_fetch(url, params):
    """Open-Meteo with one place: Тихвин, 184 km from LED; the air is the number of the month."""
    days = [f"2025-{m:02d}-15" for m in range(1, 13)]
    if url == catalog.GEOCODE:
        if params["name"] != "Тихвин":
            return {}
        return {"results": [{"id": 7, "name": "Тихвин", "latitude": 59.6451,
                             "longitude": 33.5163, "timezone": "Europe/Moscow",
                             "country": "Россия"}]}  # fmt: skip
    if url == catalog.ARCHIVE:
        return {"daily": {"time": days, "temperature_2m_max": [float(m) for m in range(12)],
                          "precipitation_sum": [31.0] * 12}}  # fmt: skip
    return {"latitude": 0.0, "longitude": 0.0, "daily": {}}


def test_trap_6_geocoder_city_airport_and_climate(store, monkeypatch):
    """Тихвин is outside the snapshot: the place of the geocoder (cached after one call)."""
    r = at(store, "compare")
    monkeypatch.setattr(catalog, "_get", tikhvin_fetch)
    r2 = say(store, "какой ближайший аэропорт у Тихвина и какая там погода?", r.session_id)
    port = resolve_place("Тихвин")["airport"]
    assert port["iata"] == "LED" and 150 < port["km"] < 200
    assert f"Тихвин: своего аэропорта нет, ближайший LED, {port['km']} км по прямой." in r2.reply, (
        r2.reply
    )
    assert "Тихвин в декабре: днём 11 °C" in r2.reply and "не прогноз" in r2.reply
    assert r2.plan == r.plan and r2.stage == "compare"


def first_answer(store, text):
    r = say(store, text)
    assert r.stage == "compare", r.reply
    return r


def flights(r):
    return [o.title for o in r.plan.offers if o.kind == "transport" and o.mode == "flight"]


def test_trap_6_named_city_first_answer_has_climate_and_airport(store, monkeypatch):
    """«Тихвин на 8 ночей»: the first answer has the month climate and the airport (#123)."""
    monkeypatch.setattr(catalog, "_get", tikhvin_fetch)
    r = first_answer(store, "Тихвин на 8 ночей")
    port = resolve_place("Тихвин")["airport"]
    month = r.plan.spec.date_from.month
    air, _ = catalog.month_climate(resolve_place("Тихвин"), month)
    line = f"≈ {air:g} °C, 31 мм; ближайший аэропорт LED, {port['km']} км"
    assert line in r.reply, r.reply
    assert "оценка по каталогу, не прогноз" in r.reply
    assert flights(r) and all(" через LED," in t for t in flights(r)), flights(r)
    assert "Перелёт Москва → Тихвин через LED, " in r.reply
    assert "Перелёт Тихвин → Москва (VKO) через LED, " in r.reply


def test_trap_4_catalogue_city_first_answer_has_climate_and_airport(store):
    r = first_answer(store, VYBORG)
    place = catalog.resolve("Выборг")
    air, _ = catalog.month_climate(place, 12)
    rain = place["climate"]["rain"][11]
    assert f"в декабре днём ≈ {air:g} °C, {rain:g} мм; ближайший аэропорт LED, 131 км" in r.reply
    assert "Перелёт Москва → Выборг через LED, " in r.reply


def test_city_with_its_own_airport_has_no_via_label(store):
    """Сочи: AER is 25 km away, the flight is «Москва → Сочи», nothing is added."""
    r = first_answer(store, SOCHI)
    rain = catalog.resolve("Сочи")["climate"]["rain"][11]
    assert f"в декабре днём ≈ 11.6 °C, {rain:g} мм; ближайший аэропорт AER, 25 км" in r.reply, (
        r.reply
    )
    assert flights(r) and not any("через" in t for t in flights(r)), flights(r)
    assert "Перелёт Москва → Сочи (AER), " in r.reply


def test_trap_4_no_airport_data_no_line(monkeypatch):
    """A place without an airport or a climate in the data: no number is made up, no line."""
    plan = Plan(spec=TripSpec(destinations=["Сочи"], month=12))
    place = dict(catalog.resolve("Сочи"))
    monkeypatch.setattr(answers, "resolve_place", lambda name: {**place, "airport": None})
    assert answers.destination_line(plan) == (
        f"Климат: в декабре днём ≈ 11.6 °C, {place['climate']['rain'][11]:g} мм "
        "(оценка по каталогу, не прогноз)."
    )
    monkeypatch.setattr(
        answers, "resolve_place", lambda name: {**place, "airport": None, "climate": None}
    )
    assert answers.destination_line(plan) == ""
    monkeypatch.setattr(answers, "resolve_place", lambda name: None)
    assert answers.destination_line(plan) == ""


@pytest.mark.parametrize("stage", STAGES)
def test_trap_7_the_answer_has_no_price(store, stage):
    """Climate and airport are no prices: nothing to mark, and no invented number of money."""
    r = at(store, stage)
    for q in ("какая погода в Анталии?", "какой ближайший аэропорт у Анталии?"):
        assert "₽" not in say(store, q, r.session_id).reply


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize(
    "question", ["какая погода в Анталии?", "какой ближайший аэропорт у Анталии?"]
)
def test_question_about_a_city_never_reaches_the_model(monkeypatch, store, stage, question):
    r = at(store, stage)
    asked = []

    def stub(m, s, until=None, today=None):
        asked.append(m)
        return {"wishes": ["бассейн"], "destinations": ["Казань"]}

    monkeypatch.setattr(llm, "extract_spec", stub)
    r2 = say(store, question, r.session_id)
    assert asked == []
    assert r2.plan == r.plan and r2.stage == stage and r2.cart == r.cart


@pytest.mark.parametrize(
    "request_text",
    ["хочу в Анталию, там тепло?", "давай в Анталию, какая там погода?", "поехали в Анталию"],
)
def test_an_order_with_a_city_still_changes_the_destination(store, request_text):
    """The fix is for questions only: a wish to go to another city re-plans, as before."""
    r = at(store, "compare")
    r2 = say(store, request_text, r.session_id)
    assert r2.plan.spec.destinations == ["Анталия"], r2.reply


def test_train_and_car_labels_have_no_via_label(store):
    """Only a flight goes through an airport: «Поезд Москва → Казань» is not «через »."""
    default = first_answer(store, "Казань из Москвы с 3 по 8 декабря, двое взрослых")
    car = first_answer(store, "Казань из Москвы с 3 по 8 декабря, двое взрослых, на машине")
    titles = [o.title for r in (default, car) for o in r.plan.offers if o.kind == "transport"]
    ground = [t for t in titles if t.startswith(("Поезд", "На своей машине"))]
    assert any(t.startswith("Поезд ") for t in ground), titles
    assert any(t.startswith("На своей машине") for t in ground), titles
    assert not any("через" in t for t in ground), ground
    kazan = [t for t in titles if t.startswith("Перелёт Москва → Казань")]
    assert kazan and all("→ Казань (KZN)," in t and "через" not in t for t in kazan), kazan


def test_flight_to_vyborg_is_still_through_led(store):
    r = first_answer(store, VYBORG)
    assert any("Перелёт Москва → Выборг через LED," in t for t in flights(r)), flights(r)
