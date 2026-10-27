"""Facts for a question at a built plan (#186): weather, visas, «почему не <отель>».

Every fact comes from the plan offers or the catalogue (catalog.json); the model writes
none of it (TRAP-4). No data means «нет данных», never a guess. Each price is marked as
synthetic (TRAP-7). A question changes neither the plan nor the cart: the caller only reads.
"""

import re

from api import catalog
from api.contracts import HotelOffer, Plan
from api.fixtures import IN_MONTH
from api.offers import resolve_place
from api.optimizer import hotel_rows

WEATHER_RE = re.compile(r"погод|климат|температур|градус|дожд|холодн|тепл|жарк|купа|вода\s+в\s+мор")
VISA_RE = re.compile(r"\bвиз[аыуеой]{0,2}\b|\bвизов\w*|загранпаспорт\w*|правил\w*\s+въезд")
# «почему не Бульвар?», «чем Гранд хуже?», «а не Парк?»: a hotel of the plan is named.
VERSUS_RE = re.compile(
    r"\bпочему\b|\bчем\b|\bа\s+не\b|\bвместо\b|\bлучше\b|\bхуже\b|\bдешевле\b|\bдороже\b"
)
NAME_RE = re.compile(r"«([^»]+)»")
NO_DATA = "нет данных"


def _rub(n: int) -> str:
    return f"{n:,} ₽ (синтетика)".replace(",", " ")


def _stems(name: str) -> list[str]:
    words = re.findall(r"\w+", name.lower().replace("ё", "е"))
    return [w[: max(4, len(w) - 2)] for w in words if len(w) > 2]


def _named(low: str, hotel: HotelOffer) -> bool:
    """The question names this hotel: every word of its name, any ending («Бульвара»)."""
    m = NAME_RE.search(hotel.title)
    stems = _stems(m.group(1) if m else hotel.title)
    tokens = re.findall(r"\w+", low)
    return bool(stems) and all(any(t.startswith(s) for t in tokens) for s in stems)


def _cancel(h: HotelOffer) -> str:
    return "бесплатная отмена" if h.cancellation.refundable else "невозвратный тариф"


def versus_fact(plan: Plan, low: str) -> str:
    """Why the hotel of the plan and not the named one: price, cancellation, distance, rating."""
    if not VERSUS_RE.search(low):
        return ""
    rows = hotel_rows(plan.offers, plan.selected)
    chosen = next((h for h in rows if h.offer_id in plan.selected), None)
    named = next((h for h in rows if _named(low, h)), None)
    if named is None or chosen is None:
        return ""
    if named.offer_id == chosen.offer_id:
        return f"{chosen.title} уже в плане: {_rub(chosen.price_rub)}, {_cancel(chosen)}."
    facts = [f"цена {_rub(named.price_rub)} против {_rub(chosen.price_rub)} в плане"]
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
        facts.append(f"отмена: {_cancel(named)} против {_cancel(chosen)} в плане")
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
    short = NAME_RE.search(named.title)
    other = f"«{short.group(1)}»" if short else named.title
    lines = [f"{named.title} против {chosen.title} в плане: " + "; ".join(facts) + "."]
    if ours:
        lines.append(f"Отель в плане выигрывает: {', '.join(ours)}.")
    if theirs:
        lines.append(f"{other} выигрывает: {', '.join(theirs)}.")
    lines.append(
        "План выбирает поездку целиком: цена отеля, дороги и трансферов, условия и рейтинг."
    )
    return "\n".join(lines)


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
    return {
        k: v
        for k, v in found.items()
        if not (k == "month" and v == month)
        and not (k == "destinations" and v and set(v) <= places)
    }


def weather_fact(plan: Plan) -> str:
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
        fact = f"воздух {air:g} °C" + (f", вода {water:g} °C" if water is not None else "")
        lines.append(f"{city} {when}: {fact} — оценка по каталогу, не прогноз.")
    if not lines:
        return f"Направление не выбрано — климата {NO_DATA}."
    return "\n".join(lines) + "\nПрогноза погоды в данных нет."


def visa_fact() -> str:
    return (
        f"Данных о визах и правилах въезда в каталоге нет — {NO_DATA}. "
        "Проверьте требования для вашего гражданства на сайте консульства страны поездки."
    )


def plan_fact(plan: Plan, low: str) -> str:
    """The fact a question about the plan asks for, or "" when it asks for none of these.

    `low` is the message in lower case without «ё». A named hotel goes first: «почему не
    Бульвар, там же море ближе?» asks about the hotel, not the weather."""
    if fact := versus_fact(plan, low):
        return fact
    if VISA_RE.search(low):
        return visa_fact()
    if WEATHER_RE.search(low):
        return weather_fact(plan)
    return ""
