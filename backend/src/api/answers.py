"""Facts for a question at a built plan (#186): weather, visas, «почему <отель> а не <отель>».

Every fact comes from the plan offers or the catalogue (catalog.json); the model writes
none of it (TRAP-4). No data means «нет данных», never a guess. Each price is marked as
synthetic (TRAP-7). A question changes neither the plan nor the cart: the caller only reads.
«Почему не Бульвар, там же море ближе?» is a question about the hotel. «Давай Бульвар, там море
ближе» is an edit: the model reads it, and nothing here answers it.
"""

import re
from datetime import timedelta

from api import catalog
from api.contracts import HotelOffer, Plan, TransportOffer, TripSpec
from api.extract import MONTH, NOT_PLACES
from api.fixtures import CATALOG, IN_MONTH
from api.offers import (
    CLOSED_NOTE,
    FAR_AIRPORT_KM,
    ROOM_GUESTS,
    closed_airport,
    places,
    resolve_place,
    rooms_label,
)
from api.optimizer import hotel_rows

WEATHER_RE = re.compile(
    r"погод|климат|температур|градус|дожд|осадк|снег|ливн|холодн|тепл|жарк|купа|вода\s+в\s+мор"
    r"|какая\s+вода\b"
)
RAIN_RE = re.compile(r"дожд|осадк|снег|ливн")
# «какой ближайший аэропорт?», «аэропорт далеко?»: the airport is asked about. «Сколько стоит
# трансфер из аэропорта?» and «рейс в аэропорт задержат?» ask about the plan, not the place (#123).
AIRPORT_RE = re.compile(r"аэропорт|аэродром")
AIRPORT_ASK_RE = re.compile(
    r"\b(?:какой|какие|какого|где|далеко|далек\w*|ближайш\w*|расстояни\w*|есть\s+ли|свой|"
    r"свое[гм]\w*|км|километр\w*)\b"
)
AIRPORT_PLAN_RE = re.compile(
    r"трансфер|такси|задерж|опозда|багаж|парковк|стоянк|встреч|добра|доехать"
)
# A wish or an order to go there: the city is then an edit of the plan, not the subject of a question.
GO_THERE_RE = re.compile(
    r"\b(?:поех\w*|поед\w*|лет[иеу]м\w*|едем|отправ\w*|переезж\w*|измен\w*|вместо|"
    r"перенес\w*|поменя\w*|махн\w*)\b|\bа\s+если\s+(?:во?|на)\b"
)
# «Из какого аэропорта вылет?» asks about the airport of the origin, not of the place of the plan.
DEPARTURE_RE = re.compile(r"\bвылет\w*|\bоткуда\b|\bотправлени\w*")
# «в Анталии», «у Тихвина», «к Выборгу»: a capitalized name after a preposition. Extract reads
# only some of these forms; a name it misses must not turn into the climate of the plan city.
PLACE_AFTER_RE = re.compile(
    r"(?:^|[\s,(])(?:во?|на|у|ко?|от|около|возле|близ|для)\s+"
    r"([А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*(?:\s+[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*)?)"
)
NOT_A_PLACE_RE = re.compile(r"(?:рожд|пасх|масленич|крещ|троиц)")
VISA_RE = re.compile(r"\bвиз[аыуеой]{0,2}\b|\bвизов\w*|загранпаспорт\w*|правил\w*\s+въезд")
# «почему не Бульвар?», «чем Гранд хуже?», «а не Парк?»: a hotel of the plan is named.
VERSUS_RE = re.compile(
    r"\bпочему\b|\bчем\b|\bа\s+не\b|\bвместо\b|\bлучше\b|\bхуже\b|\bдешевле\b|\bдороже\b"
)
NAME_RE = re.compile(r"«([^»]+)»")
# «чем Бульвар лучше?»: «лучше» here is a comparison, not a wish to change the hotel.
CHEM_RE = re.compile(r"\bчем\b[^?.!]*\b(?:лучше|хуже)\b")
CHOICE_WORDS_RE = re.compile(r"\b(?:давай\w*|хоч\w+|хотим|возьм\w+|бер[уиё]\w*|замен\w+|смен\w+)\b")
WHY_NOT_RE = re.compile(r"\b(?:почему|зачем)\s+(?:же\s+|бы\s+)?не\s+(.+?)\s*\??\s*$")
# Only a question that says «отель» is about the chosen hotel: «почему эта цена?» is not.
THIS_HOTEL_RE = re.compile(r"\b(?:почему|зачем)\b.*(?:\bотел|гостиниц)")
RACE_RE = re.compile(r"\bрейс\w*|\bпереезд\w*")
MODE_NOUN = {"train": "Поездов", "flight": "Перелётов", "car": "Поездки на машине"}
# A word of a hotel name that is no name: «Шаляпин Палас Отель» is named by «Шаляпин».
GENERIC_WORDS = {"отель", "hotel", "палас", "город", "апарт", "гостиница", "хостел", "курорт"}
# What a «почему не …» tail may not be to count as a hotel name: a month, a number, a star,
# a meal, a comparative, a preposition («в январе», «4 звезды», «с завтраком», «дешевле»).
NOT_A_NAME_RE = re.compile(
    r"\d|★|\b(?:одн\w+|дв[ауе]\w*|тр[её]х\w*|четыр\w*|пят\w*)\s+звезд|завтрак|питани|ужин|обед"
    r"|включен|пансион|парковк|бассейн|\ball\s+inclusive\b|\b(?:u?ai|bb|hb|fb|ro)\b"
    r"|(?:по)?(?:дешевле|дороже|ближе|лучше|больше|меньше)"
    rf"|{MONTH}"
)
PREPOSITION_RE = re.compile(r"^(?:в|во|на|с|со|без|для|по|к|у|за|из|при)\s")
MODES = {
    "train": re.compile(r"\bпоезд\w*|\bж/?д\b|сапсан|ласточк"),
    "flight": re.compile(r"\bсамол[её]т\w*|\bперел[её]т\w*|\bавиа\w*"),
    "car": re.compile(r"\bмашин\w*|\bавтомобил\w*"),
}
NO_DATA = "нет данных"


# TRAP-7 (#567): the source of a real price by kind and mode (web/src/Board.tsx PRICE_SOURCES).
PRICE_SOURCES = {"hotel": "101hotels", "flight": "Авиасейлс", "train": "tutu"}


def price_mark(offers) -> str:
    """The label of a sum of these offers: «синтетика» when all are synthetic or there are none,
    the real sources when none is, «часть цен — синтетика» for a mix (TRAP-7)."""
    if all(o.synthetic for o in offers):
        return "синтетика"
    if any(o.synthetic for o in offers):
        return "часть цен — синтетика"
    names = (PRICE_SOURCES.get(getattr(o, "mode", o.kind), "реальные данные") for o in offers)
    return " + ".join(dict.fromkeys(names))


def _rub(n: int, *offers) -> str:
    """A price with the label of the offers it comes from; no offers: «синтетика»."""
    return f"{n:,} ₽ ({price_mark(offers)})".replace(",", " ")


def _span(delta: timedelta) -> str:
    """«2 ч 30 мин» from the times of an offer (a tool fact, never a model guess)."""
    h, m = divmod(int(delta.total_seconds() // 60), 60)
    return " ".join(x for x in (f"{h} ч" if h else "", f"{m} мин" if m or not h else "") if x)


def span(offer) -> str:
    return _span(offer.arrive_at - offer.depart_at)


def _words(name: str, shortest: int = 4) -> list[str]:
    ws = re.findall(r"\w+", name.lower().replace("ё", "е"))
    return [w for w in ws if len(w) >= shortest and w not in GENERIC_WORDS]


def _hit(tokens: list[str], word: str, loose: bool = False) -> int | None:
    """Index of the token that is this word with a case ending («Бульвара», «Панораму»).
    A short word (up to 6 letters) is taken whole: «границу» is no «Гранд», «парковки» no «Парк».
    `loose`: the names of places, which change more («Москву»)."""
    cut = max(3, len(word) - 2) if loose else (len(word) - 1 if len(word) >= 7 else len(word))
    for i, t in enumerate(tokens):
        if t.startswith(word[:cut]) and len(t) <= len(word) + 2:
            return i
    return None


def _name_of(hotel: HotelOffer) -> str:
    """The name without the city: «Корстон, Казань» is «Корстон»."""
    m = NAME_RE.search(hotel.title)
    return m.group(1) if m else hotel.title.split(",")[0]


def _named_at(low: str, hotel: HotelOffer) -> int | None:
    """Position of the hotel in the question: every significant word of its name is there
    («Лазурный берег» is not «на берегу», «Северная звезда» is not «4 звезды»)."""
    tokens = re.findall(r"\w+", low)
    words = _words(_name_of(hotel))
    hits = [_hit(tokens, w) for w in words]
    if not words or None in hits:
        return None
    return min(i for i in hits if i is not None)


def _named(low: str, hotel: HotelOffer) -> bool:
    return _named_at(low, hotel) is not None


def is_hotel_compare(plan: Plan, low: str) -> bool:
    """«Чем Бульвар лучше?»: a hotel stands between «чем» and «лучше/хуже», and nobody orders
    anything. «давай самолётом, чем быстрее тем лучше» is an order, not a comparison."""
    m = CHEM_RE.search(low)
    if not m or CHOICE_WORDS_RE.search(low):
        return False
    segment = m.group(0)
    return any(_named_at(segment, h) is not None for h in hotel_rows(plan.offers, plan.selected))


def _cancel(h: HotelOffer) -> str:
    return "бесплатная отмена" if h.cancellation.refundable else "невозвратный тариф"


def _free_tariff(plan: Plan, hotel: HotelOffer) -> HotelOffer | None:
    """The cheapest tariff of this hotel with free cancellation (the same meal first), or None.
    The row of a hotel shows one tariff; the others are offers of the plan too (TRAP-4)."""
    if hotel.cancellation.refundable:
        return None
    tariffs = [
        o
        for o in plan.offers
        if isinstance(o, HotelOffer)
        and (o.title, o.city) == (hotel.title, hotel.city)
        and o.cancellation.refundable
    ]
    return min(tariffs, key=lambda o: (o.meal != hotel.meal, o.price_rub, o.offer_id), default=None)


def _free_line(plan: Plan, *hotels: HotelOffer) -> str:
    """«Тариф с бесплатной отменой: «Бульвар» — 30 900 ₽ (синтетика)». A hotel without such a
    tariff in the data is named only next to one that has it; no tariff at all gives ""."""
    found = [(h, _free_tariff(plan, h)) for h in hotels if not h.cancellation.refundable]
    if not any(alt for _, alt in found):
        return ""
    parts = []
    for h, alt in found:
        if alt is None:
            parts.append(f"«{_name_of(h)}» — в данных нет")
            continue
        meal = f", питание {alt.meal}" if alt.meal != h.meal else ""
        parts.append(f"«{_name_of(h)}» — {_rub(alt.price_rub, alt)}{meal}")
    return "Тариф с бесплатной отменой: " + "; ".join(parts) + "."


def versus_fact(plan: Plan, low: str) -> str:
    """Why the hotel of the plan and not the named one: price, cancellation, distance, rating.
    Two named hotels («чем Парк лучше Панорамы?») are compared with each other."""
    if not VERSUS_RE.search(low):
        return ""
    rows = hotel_rows(plan.offers, plan.selected)
    chosen = next((h for h in rows if h.offer_id in plan.selected), None)
    hits = sorted((i, h) for h in rows if (i := _named_at(low, h)) is not None)
    if chosen is None or not hits:
        return ""
    found = [h for _, h in hits]
    base = chosen
    if len(found) >= 2:
        base = chosen if chosen in found else found[1]
        named = next(h for h in found if h is not base)
    else:
        named = found[0]
    where = " в плане" if base is chosen else ""
    return _versus(plan, base, named, where)


def _versus(plan: Plan, chosen: HotelOffer, named: HotelOffer, where: str) -> str:
    if named.offer_id == chosen.offer_id:
        free = _free_line(plan, chosen)
        return (
            f"{chosen.title} уже в плане: {_rub(chosen.price_rub, chosen)}, {_cancel(chosen)}.\n"
            + (free + "\n" if free else "")
            + why_chosen(plan, chosen, hotel_rows(plan.offers, plan.selected))
        )
    facts = [f"цена {_rub(named.price_rub, named)} против {_rub(chosen.price_rub, chosen)}{where}"]
    ours, theirs = [], []
    if named.price_rub > chosen.price_rub:
        ours.append("дешевле")
    elif named.price_rub < chosen.price_rub:
        theirs.append("дешевле")
    if named.cancellation.refundable != chosen.cancellation.refundable:
        (theirs if named.cancellation.refundable else ours).append("бесплатная отмена")
    if named.cancellation.refundable == chosen.cancellation.refundable:
        facts.append(f"отмена: у обоих {_cancel(chosen)}")
    else:
        facts.append(f"отмена: {_cancel(named)} против {_cancel(chosen)}{where}")
    a, b = named.distance_center_km, chosen.distance_center_km
    if a is not None and b is not None:
        facts.append(f"до центра {a:g} км против {b:g} км")
        if a != b:
            (theirs if a < b else ours).append("ближе к центру")
    a, b = named.rating, chosen.rating
    if a is not None and b is not None:
        facts.append(f"рейтинг {a:g} против {b:g}")
        if a != b:
            (theirs if a > b else ours).append("рейтинг выше")
    other = f"«{_name_of(named)}»"
    lines = [f"{named.title} против {chosen.title}{where}: " + "; ".join(facts) + "."]
    if free := _free_line(plan, named, chosen):
        lines.append(free)
    mine = "Отель в плане" if where else f"«{_name_of(chosen)}»"
    if ours:
        lines.append(f"{mine} выигрывает: {', '.join(ours)}.")
    if theirs:
        lines.append(f"{other} выигрывает: {', '.join(theirs)}.")
    if where:
        road = any(o.kind == "transport" and o.offer_id in plan.selected for o in plan.offers)
        lines.append(
            "План выбирает поездку целиком: цена отеля, дороги и трансферов, условия и рейтинг."
            if road
            else "План выбирает проживание: цена отеля, условия и рейтинг. Как добраться — нет данных."
        )
    return "\n".join(lines)


def why_chosen(plan: Plan, mine: HotelOffer, rows: list[HotelOffer]) -> str:
    """Why this hotel, from the rows of the comparison: place by price, budget, wishes met."""
    spec = plan.spec
    by_price = sorted(rows, key=lambda h: (h.price_rub, h.offer_id))
    place = by_price.index(mine) + 1
    facts = [
        "самый дешёвый из отелей в данных"
        if place == 1
        else f"{place}-й по цене из {len(rows)} отелей в данных"
    ]
    if spec.hotel_budget_rub is not None:
        within = "в бюджете" if mine.price_rub <= spec.hotel_budget_rub else "выше бюджета"
        facts.append(f"{within} отеля {_rub(spec.hotel_budget_rub)}")
    if spec.meal and mine.meal == spec.meal:
        facts.append(f"питание {mine.meal}, как вы просили")
    if spec.refundable and mine.cancellation.refundable:
        facts.append("с бесплатной отменой, как вы просили")
    return "Почему он: " + "; ".join(facts) + ". Выбор считает оптимизатор по данным каталога."


def this_hotel_fact(plan: Plan, low: str) -> str:
    """«Почему этот отель?», «почему выбрали …?»: the facts of the chosen hotel and the reasons."""
    if not THIS_HOTEL_RE.search(low) or WHY_NOT_RE.search(low):
        return ""  # «почему не отель получше?» is not about the chosen hotel
    rows = hotel_rows(plan.offers, plan.selected)
    chosen = next((h for h in rows if h.offer_id in plan.selected), None)
    if chosen is None:
        return ""
    return f"{_line(chosen)}.\n" + why_chosen(plan, chosen, rows)


def _line(h: HotelOffer) -> str:
    facts = [f"{h.stars}★"] if h.stars else []
    if h.rating is not None:
        facts.append(f"рейтинг {h.rating:g}")
    facts += [h.meal, _cancel(h)]
    if h.distance_center_km is not None:
        facts.append(f"{h.distance_center_km:g} км от центра")
    return f"{h.title}: {', '.join(facts)}, {_rub(h.price_rub, h)}"


def mode_fact(plan: Plan, low: str) -> str:
    """«Почему не поезд?»: for each chosen leg, the cheapest leg of that mode on its route.
    «Почему выбрали поезд?», «почему этот рейс?» show the legs of the plan."""
    if not re.search(r"\b(?:почему|зачем|чем)\b", low):
        return ""
    mode = next((m for m, p in MODES.items() if p.search(low)), "")
    if not mode and not RACE_RE.search(low):
        return ""
    legs = [o for o in plan.offers if o.offer_id in plan.selected and isinstance(o, TransportOffer)]

    def text(o: TransportOffer) -> str:
        return f"{o.title} — {_rub(o.price_rub, o)}, в пути {span(o)}"

    rows = []
    for leg in legs:
        same = [
            o
            for o in plan.offers
            if isinstance(o, TransportOffer)
            and o.mode == mode
            and (o.origin, o.destination) == (leg.origin, leg.destination)
        ]
        route = f"{leg.origin} → {leg.destination}"
        if not mode:  # «почему этот рейс?»: the leg of the plan
            rows.append(f"{route}: в плане {text(leg)}.")
        elif leg.mode == mode:
            rows.append(f"{route}: в плане уже так — {text(leg)}.")
        elif same:
            alt = min(same, key=lambda o: (o.price_rub, o.offer_id))
            rows.append(f"{route}: в плане {text(leg)}; для сравнения {text(alt)}.")
        else:
            rows.append(f"{MODE_NOUN[mode]} {route} в предложениях плана нет.")
    return "\n".join(rows)


def why_not_tail(text: str) -> str:
    """What comes after «почему не», up to a comma: «Бульвар, там же дешевле» is «Бульвар»."""
    m = WHY_NOT_RE.search(text)
    return m.group(1).split(",")[0].strip(" ?!.") if m else ""


def _is_place(tail: str) -> bool:
    """A city or a country of the catalogue or of the places snapshot (TRAP-3 spellings)."""
    if resolve_place(tail):
        return True
    tokens = re.findall(r"\w+", tail.lower().replace("ё", "е"))
    stems = [st for c in CATALOG.values() for st in c["stems"]]
    countries = {p.get("country", "") for p in places()}
    words = [w for c in countries for w in _words(c, 3)]
    return any(t.startswith(st) for t in tokens for st in stems) or any(
        _hit(tokens, w, loose=True) is not None for w in words
    )


def unknown_name_fact(plan: Plan, message: str) -> str:
    """«Почему не Лазурь?»: the tail is no hotel of the data. The city of departure and the
    destination are no hotels either; a tail that is no name («в январе», «дешевле»,
    «All inclusive») or another place of the catalogue gets nothing here: the older rules read
    it. `message` keeps the case: a name starts with a capital."""
    named = why_not_tail(message.lower().replace("ё", "е"))
    if not named:
        return ""
    m = WHY_NOT_RE.search(message.lower().replace("ё", "е"))
    named = message[m.start(1) : m.start(1) + len(named)]
    low = named.lower().replace("ё", "е")
    if not named[:1].isupper() or len(named.split()) > 4:
        return ""
    if NOT_A_NAME_RE.search(low) or PREPOSITION_RE.search(low):
        return ""
    tokens = re.findall(r"\w+", low)
    rows = hotel_rows(plan.offers, plan.selected)
    have = ", ".join(_name_of(h) for h in rows)
    if plan.spec.origin and any(
        _hit(tokens, w, loose=True) is not None for w in _words(plan.spec.origin, 3)
    ):
        return f"«{named}» — город вылета, а не отель. Отели в данных: {have}."
    spots = plan.spec.destinations
    if any(_hit(tokens, w, loose=True) is not None for d in spots for w in _words(d, 3)):
        return f"«{named}» — это и есть направление плана, а не отель. Отели в данных: {have}."
    if _is_place(named):
        # «Почему не Египет, там теплее?» has a reason: the older rules read it as a weather question
        full = WHY_NOT_RE.search(message.lower().replace("ё", "е")).group(1).strip(" ?!.")
        return place_fact(plan, named) if len(full) == len(named) else ""
    city = spots[0] if spots else "этого направления"
    return (
        f"«{named}» нет в данных (отели: {city}); сравнить не с чем, выдумывать не буду. "
        f"Отели в данных: {have}."
    )


def _to(name: str) -> str:
    """«Турция» → «Турцию», «Хургада» → «Хургаду» for «поехали в …»; other names stay."""
    if name.endswith("ия"):
        return name[:-1] + "ю"
    return name[:-1] + "у" if name.endswith("а") else name


def place_fact(plan: Plan, named: str) -> str:
    """C13: «почему не Турция?» at a plan elsewhere. The plan is not rebuilt. The place gets the
    climate of the catalogue (a number from a tool); its prices and flights were not compared,
    and the answer says so (TRAP-4). The user is told how to switch."""
    spots = plan.spec.destinations
    place = _place(named)
    if place and any((p := _place(d)) and p["name"] == place["name"] for d in spots):
        return f"«{named}» — это и есть направление плана."
    where = ", ".join(spots) if spots else "выбранном направлении"
    return (
        f"План собран на «{where}». «{named}» я не сравнивал: цен и рейсов туда в плане нет, "
        "выдумывать не буду.\n"
        f"{weather_fact(plan, '', [named])}\n"
        f"Могу сравнить: напишите «поехали в {_to(named)}», и я пересоберу план."
    )


def _month(plan: Plan) -> int | None:
    spec = plan.spec
    return spec.date_from.month if spec.date_from else spec.month


def _places(plan: Plan) -> list[str]:
    cities = list(plan.spec.destinations)
    for o in plan.offers:
        if isinstance(o, HotelOffer) and o.offer_id in plan.selected and o.city not in cities:
            cities.append(o.city)
    return cities


def is_airport_question(low: str) -> bool:
    return bool(
        AIRPORT_RE.search(low) and AIRPORT_ASK_RE.search(low) and not AIRPORT_PLAN_RE.search(low)
    )


def _place(name: str) -> dict | None:
    """The place of a name as typed. A genitive or dative the geocoder does not know («у Тихвина»,
    «к Тихвину») is asked once more without its last vowel."""
    place = resolve_place(name)
    if place is None and len(name) > 4 and name[-1] in "ауюы":
        place = resolve_place(name[:-1])
    return place


def _name_key(name: str) -> str:
    place = _place(name)
    return place["name"] if place else catalog.norm(name)[:5]


def _named_places(plan: Plan, message: str, found: dict) -> list[str]:
    """The places a message names: those extract read (its destinations; an «от Тихвина» that is
    no new origin reads as a departure from Тихвин) and the capitalized names after a
    preposition that it missed. A name keeps its form: the catalogue and the geocoder decide."""
    typed, heads = [], set()  # the names after a preposition; the first words of all of them
    for m in PLACE_AFTER_RE.finditer(message):
        words = m.group(1).split()
        first = words[0].lower().replace("ё", "е")
        heads.add(words[0])
        if (
            first in NOT_PLACES  # «Новый год», «Майские»
            or re.fullmatch(MONTH, first)  # «а в Мае тепло?»
            or NOT_A_PLACE_RE.match(first)
            or (first.startswith("нов") and re.match(r"\s+год", message[m.end(1) :]))
        ):
            continue  # «в Новом году» is a time; «Новороссийск», «Новом Афоне» are places
        # «Нижнем Новгороде» is one name; «Анталии Бульвар» is a name and a word: the first fits.
        # Two words that the data knows neither as a whole nor the first are one name («Новом Афоне»).
        joined = " ".join(words)
        whole = len(words) == 1 or _place(joined) or not _place(words[0])
        typed.append(joined if whole else words[0])
    # Extract keeps the first word of «Новом Афоне» and of «в Новом году»: the text decides.
    names = [n for n in found.get("destinations") or [] if n not in heads]
    origin = found.get("origin")
    if origin and origin != plan.spec.origin and origin not in heads:
        names.append(origin)
    names += typed
    seen, out = set(), []
    for n in names:
        if (key := _name_key(n)) not in seen:
            seen.add(key)
            out.append(n)
    return out


def is_city_question(plan: Plan, low: str, message: str, found: dict) -> bool:
    """«Какая погода в Анталии?» at a plan in Сочи: a question about a named city (#123).
    «Хочу в Анталию, там тепло?» and «поехали в Анталию» order, so they stay an edit."""
    return bool(
        (WEATHER_RE.search(low) or is_airport_question(low))
        and not (CHOICE_WORDS_RE.search(low) or GO_THERE_RE.search(low))
        and not (DEPARTURE_RE.search(low) and is_airport_question(low))
        and _named_places(plan, message, found)
    )


def without_plan_fields(found: dict, plan: Plan, low: str = "", message: str = "") -> dict:
    """The fields of a question minus the month and the places the plan already has:
    «какая погода в декабре?» at a December plan names no new field. The places and the month
    of a question about a named city are the subject of the question, not a new field (#123)."""
    month, places = _month(plan), set(plan.spec.destinations)
    if plan.spec.origin:
        places.add(plan.spec.origin)  # «почему не Москва?»: the city of departure is no new place
    about_city = bool(low) and is_city_question(plan, low, message, found)
    return {
        k: v
        for k, v in found.items()
        if not (k == "month" and v == month)
        and not (k == "destinations" and v and set(v) <= places)
        and not (about_city and k in ("destinations", "origin", "month"))
    }


def _rain(place: dict, month: int) -> str:
    rain = place["climate"]["rain"][month - 1]
    return f", осадки {rain:g} мм за месяц" if rain is not None else ", осадков в данных нет"


def weather_fact(plan: Plan, low: str = "", cities: list[str] | None = None, month=None) -> str:
    """The climate of the trip month from the catalogue (catalog.json, or the geocoder place).
    `cities`: the places the question names; the places of the plan when it names none."""
    month = month or _month(plan)
    when = IN_MONTH[month - 1] if month else ""
    lines = []
    for city in cities or _places(plan):
        place = _place(city)
        air, water = catalog.month_climate(place, month) if place and month else (None, None)
        if air is None and water is None:
            # «Калуга: климата …» keeps the name as typed: no preposition, no case to get wrong
            name = place["name"] if place else city
            lines.append(f"{name}: климата {when or 'вашего месяца'} в данных нет — {NO_DATA}.")
            continue
        city = place["name"]
        parts = []
        if air is not None:
            parts.append(f"днём {air:g} °C")
        if water is not None:
            parts.append(f"вода {water:g} °C")
        fact = ", ".join(parts)
        if RAIN_RE.search(low):
            fact += _rain(place, month)
        line = f"{city} {when}: {fact} — оценка по каталогу, не прогноз."
        notes = " ".join(place.get("climate_notes", []))
        lines.append(line + (" " + notes if notes else ""))
    if not lines:
        return f"Направление не выбрано — климата {NO_DATA}."
    return "\n".join(lines) + "\nПрогноза погоды в данных нет."


def _how(port: dict) -> str:
    """How the airport km were measured: «по дороге» when the snapshot has the road length
    (`road`), else «по прямой». The data decides, the word is never guessed (TRAP-4)."""
    return "по дороге" if port.get("road") else "по прямой"


def destination_line(plan: Plan) -> str:
    """One line of the first answer for the main place: the climate of the month and the nearest
    airport, from the catalogue or the geocoder place. A part without data is left out, never
    guessed (TRAP-4); no data at all gives an empty line."""
    month = _month(plan)
    place = _place(plan.spec.destinations[0]) if plan.spec.destinations else None
    if place is None:
        return ""
    parts = []
    air, _ = catalog.month_climate(place, month) if month else (None, None)
    if air is not None:
        rain = place["climate"]["rain"][month - 1]
        parts.append(
            f"{IN_MONTH[month - 1]} днём ≈ {air:g} °C"
            + (f", {rain:g} мм" if rain is not None else "")
        )
    if port := place.get("airport"):
        parts.append(f"ближайший аэропорт {port['iata']}, {port['km']} км {_how(port)}")
    notes = " ".join(place.get("climate_notes", []))
    if not parts:
        return notes
    line = "Климат: " + "; ".join(parts) + " (оценка по каталогу, не прогноз)."
    return line + (" " + notes if notes else "")


def closed_line(plan: Plan) -> str:
    """C8 (#363): the airport of the place is closed — the reason the plan flies elsewhere or
    goes by land. Empty for an open airport or no place."""
    place = _place(plan.spec.destinations[0]) if plan.spec.destinations else None
    iata = closed_airport(place) if place else ""
    return f"Аэропорт {iata} ({place['name']}) {CLOSED_NOTE}." if iata else ""


def rooms_line(spec: TripSpec) -> str:
    """C7 (#363): a party over one standard room gets more rooms; the hotel price is for them."""
    if not (label := rooms_label(spec)):
        return ""
    guests = spec.adults + len(spec.children_ages)
    return (
        f"Размещение: {guests} гостей — {label} (в стандартном номере до {ROOM_GUESTS} гостей, "
        f"оценка); цена отеля — за все номера. Точное размещение уточните у отеля."
    )


def airport_fact(plan: Plan, cities: list[str] | None = None) -> str:
    """The nearest airport of each place: IATA code and km from the catalogue. A place that is
    far from it has no airport of its own; a flight of the plan to it goes through that airport."""
    lines = []
    for city in cities or _places(plan):
        place = _place(city)
        port = place.get("airport") if place else None
        if not port:
            lines.append(f"Аэропорта для {city} в данных нет — {NO_DATA}.")
            continue
        name, iata, km = place["name"], port["iata"], port["km"]
        if closed_airport(place):
            lines.append(f"{name}: ближайший аэропорт {iata}, {km} км — {CLOSED_NOTE}.")
            continue
        if km <= FAR_AIRPORT_KM:
            lines.append(f"{name}: ближайший аэропорт {iata}, {km} км от города ({_how(port)}).")
            continue
        line = f"{name}: своего аэропорта нет, ближайший {iata}, {km} км {_how(port)}."
        routes = dict.fromkeys(
            f"{o.origin} → {o.destination}"
            for o in plan.offers
            if isinstance(o, TransportOffer) and o.mode == "flight" and o.destination == name
        )
        if routes:
            line += f" Перелёт в плане ({', '.join(routes)}) — через {iata}: дальше по земле."
        lines.append(line)
    if not lines:
        return f"Направление не выбрано — аэропорта {NO_DATA}."
    return "\n".join(lines)


def city_fact(plan: Plan, low: str, message: str = "", found: dict | None = None) -> str:
    """Climate and airport of the named places (of the plan places when none is named)."""
    cities = _named_places(plan, message, found or {})
    month = (found or {}).get("month") if cities else None
    facts = []
    if WEATHER_RE.search(low):
        facts.append(weather_fact(plan, low, cities, month))
    if is_airport_question(low):
        if DEPARTURE_RE.search(
            low
        ):  # «из какого аэропорта вылет?»: the origin, never the plan city
            origin = plan.spec.origin
            facts.append(
                airport_fact(plan, [origin])
                if origin
                else f"Город вылета не назван — аэропорта вылета {NO_DATA}."
            )
        else:
            facts.append(airport_fact(plan, cities))
    return "\n".join(facts)


def visa_fact() -> str:
    return (
        f"Данных о визах и правилах въезда в каталоге нет — {NO_DATA}. "
        "Проверьте требования для вашего гражданства на сайте консульства страны поездки."
    )


def consent_fact(plan: Plan, low: str, message: str = "", found: dict | None = None) -> str:
    """The tail question of a consent: «беру, а что с визой?», «погода хорошая? тогда беру».
    The consent goes through; a visa, weather or airport question in the same message gets its
    fact line. A message without «?» and any other topic get none: «беру Бульвар, там дешевле»
    is a pick of a hotel, not a question."""
    if "?" not in low:
        return ""
    if VISA_RE.search(low):
        return visa_fact()
    return city_fact(plan, low, message, found)


def plan_fact(plan: Plan, low: str, message: str = "", found: dict | None = None) -> str:
    """The fact a question about the plan asks for, or "" when it asks for none of these.

    `low` is the message in lower case without «ё»; `message` is the same with its case, for
    the name of a hotel that is not in the data. `found` is what extract read from the message:
    the places a question about a city names (#123). A named hotel goes first: «почему не
    Бульвар, там же море ближе?» asks about the hotel, not the weather."""
    tail = why_not_tail(low)
    hotel_tail = not (tail and NOT_A_NAME_RE.search(tail))  # «почему не 4 звезды?» names none
    if hotel_tail and (fact := versus_fact(plan, low)):
        return fact
    if fact := mode_fact(plan, low):  # «почему выбрали поезд?» is no question about a hotel
        return fact
    if hotel_tail and (fact := this_hotel_fact(plan, low)):
        return fact
    if message and (fact := unknown_name_fact(plan, message)):
        return fact
    if VISA_RE.search(low):
        return visa_fact()
    return city_fact(plan, low, message, found)
