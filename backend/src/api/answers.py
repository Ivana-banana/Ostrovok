"""Facts for a question at a built plan (#186): weather, visas, «почему не <отель>».

Every fact comes from the plan offers or the catalogue (catalog.json); the model writes
none of it (TRAP-4). No data means «нет данных», never a guess. Each price is marked as
synthetic (TRAP-7). A question changes neither the plan nor the cart: the caller only reads.
"""

import re
from datetime import timedelta

from api import catalog
from api.contracts import HotelOffer, Plan, TransportOffer
from api.extract import MONTH
from api.fixtures import CATALOG, IN_MONTH
from api.offers import places, resolve_place
from api.optimizer import hotel_rows

WEATHER_RE = re.compile(
    r"погод|климат|температур|градус|дожд|осадк|снег|ливн|холодн|тепл|жарк|купа|вода\s+в\s+мор"
)
RAIN_RE = re.compile(r"дожд|осадк|снег|ливн")
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


def _rub(n: int) -> str:
    return f"{n:,} ₽ (синтетика)".replace(",", " ")


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
        return (
            f"{chosen.title} уже в плане: {_rub(chosen.price_rub)}, {_cancel(chosen)}.\n"
            + why_chosen(plan, chosen, hotel_rows(plan.offers, plan.selected))
        )
    facts = [f"цена {_rub(named.price_rub)} против {_rub(chosen.price_rub)}{where}"]
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
    return f"{h.title}: {', '.join(facts)}, {_rub(h.price_rub)}"


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
        return f"{o.title} — {_rub(o.price_rub)}, в пути {span(o)}"

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
        return ""  # another place: the older rules offer the change of the destination
    city = spots[0] if spots else "этого направления"
    return (
        f"«{named}» нет в данных (отели: {city}); сравнить не с чем, выдумывать не буду. "
        f"Отели в данных: {have}."
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


def without_plan_fields(found: dict, plan: Plan) -> dict:
    """The fields of a question minus the month and the places the plan already has:
    «какая погода в декабре?» at a December plan in Сочи names no new field."""
    month, places = _month(plan), set(plan.spec.destinations)
    if plan.spec.origin:
        places.add(plan.spec.origin)  # «почему не Москва?»: the city of departure is no new place
    return {
        k: v
        for k, v in found.items()
        if not (k == "month" and v == month)
        and not (k == "destinations" and v and set(v) <= places)
    }


def weather_fact(plan: Plan, low: str = "") -> str:
    """The climate of the trip month from the catalogue (catalog.json, or the geocoder place)."""
    month = _month(plan)
    when = IN_MONTH[month - 1] if month else ""
    lines = []
    for city in _places(plan):
        place = resolve_place(city)
        if place is None:
            lines.append(f"Климата для {city} в данных нет — {NO_DATA}.")
            continue
        air, water = catalog.month_climate(place, month) if month else (None, None)
        if air is None:
            lines.append(f"Климата {city} {when or 'вашего месяца'} в данных нет — {NO_DATA}.")
            continue
        fact = f"днём {air:g} °C" + (f", вода {water:g} °C" if water is not None else "")
        rain = place["climate"]["rain"][month - 1]
        if RAIN_RE.search(low):
            fact += (
                f", осадки {rain:g} мм за месяц" if rain is not None else ", осадков в данных нет"
            )
        lines.append(f"{city} {when}: {fact} — оценка по каталогу, не прогноз.")
    if not lines:
        return f"Направление не выбрано — климата {NO_DATA}."
    return "\n".join(lines) + "\nПрогноза погоды в данных нет."


def visa_fact() -> str:
    return (
        f"Данных о визах и правилах въезда в каталоге нет — {NO_DATA}. "
        "Проверьте требования для вашего гражданства на сайте консульства страны поездки."
    )


def plan_fact(plan: Plan, low: str, message: str = "") -> str:
    """The fact a question about the plan asks for, or "" when it asks for none of these.

    `low` is the message in lower case without «ё»; `message` is the same with its case, for
    the name of a hotel that is not in the data. A named hotel goes first: «почему не
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
    if WEATHER_RE.search(low):
        return weather_fact(plan, low)
    return ""
