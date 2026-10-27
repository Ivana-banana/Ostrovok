"""Agent: the stage state machine of ADR-0001 (T2, #6).

One user message moves the session as far as the known TripSpec allows:
- clarify: only the first reply of a session, at most 2 blocking questions;
- inspire: no destination yet, 2–4 destinations to choose from;
- select: the destination is known, but no offers were found;
- compare: offers are found and ranked, the best set is pre-selected;
- assemble: the user accepted the set, the timeline is built;
- checkout: the cart holds the set and waits for an explicit "подтверждаю";
- booked: the voucher is issued.
A change of trip fields after offers exist re-plans the trip and returns to compare.

The LLM (T11, #15) only adds TripSpec fields the rules missed and rewrites the `why` of the
optimizer explanations; reply.llm is True only when its output was used. Without it the
same path runs on rules (TRAP-1).
"""

import re
from datetime import date, timedelta
from uuid import uuid4

from api import llm
from api.cart import CartRefusal, checkout_reply, fill_cart
from api.contracts import (
    Cart,
    ChatReply,
    ChatRequest,
    Explanation,
    HotelOffer,
    Plan,
    Question,
    Stage,
    TransportOffer,
    TripSpec,
)
from api.dag import (
    DATE_EDIT_RE,
    SHIFT_RE,
    describe,
    dirty_nodes,
    is_shift_note,
    period,
    replan,
    shift_dates,
)
from api.extract import (
    DATE_KEYS,
    DEFAULT_NIGHTS,
    MAX_NIGHTS,
    MONTH_NAMES,
    excluded_places,
    extract,
    is_chip_message,
    is_excluded,
    is_past_note,
    is_too_long_note,
    too_long_note,
    with_llm,
)
from api.fixtures import CATALOG, build_timeline, future_date
from api.fixtures import suggest_destinations as catalog_destinations
from api.offers import find_offers, resolve_place
from api.optimizer import (
    _hotel_why,
    _leg_why,
    _pool,
    _risks,
    _sacrificed,
    dead_end_text,
    enumerate_trips,
    hotel_rows,
    pets_possible,
    pick_hotel,
)
from api.store import SessionStore

NOT_IN_CATALOG = "нет в каталоге"  # shared with the simulator in checks.py (#149)
CONFIRM_RE = re.compile(
    r"\b(да|ок|окей|хорошо|подходит|давай\w*|бер[уиё]\w*|оформ\w*|собер\w*|корзин\w*)\b"
)
NEGATION_RE = re.compile(r"\b(нет|не)\b")
BOOK_RE = re.compile(r"\bподтверждаю\b")
# S4: orders to set a price, to book without the confirmation or to show the prompt are
# refused. Only orders: «цена 0 ₽ за ребёнка?» or «отель без подтверждения по телефону» pass.
REFUSE_RE = re.compile(
    r"\bпромпт|system\s*prompt|(?:системн|служебн)\w*\s+инструкц"
    r"|\b(?:покаж|выведи|раскро|скаж|пришли|напиш|повтори|прочита|озвуч)\w*\s+(?:\w+\s+)?"
    r"(?:свои|твои|ваши|скрыт\w+|исходн\w+|начальн\w+)\s+(?:\w+\s+)?инструкц"
    r"|\bигнорир\w*\s+(?:\w+\s+){0,2}инструкц"
    r"|(?:постав|установ|сдела|измен|смен)\w*\s+(?:\w+\s+)?цен\w*\s+(?:на\s+|в\s+)?(?:0|н[оу]л\w*)\b"
    r"|\bцен\w*\s+должн\w*\s+быть\s+(?:0|н[оу]л\w*)\b"
    r"|\bне\s+подтверждай\b"
    r"|\bне\s+(?:спрашивай|запрашивай|проси|жди|требуй)\w*\s+(?:\w+\s+)?(?:подтвержд|согласи)"
    r"|\b(?:за)?(?:оформ|бронир|выда|выпиш)\w*\s+(?:\w+\s+){0,2}без\s+(?:\w+\s+)?"
    r"(?:подтвержд|оплат\w*\b(?!\s+(?:на\s+месте|на\s+ресепшен|при\s+(?:заселении|заезде)"
    r"|в\s+отеле|заранее|сейчас)))"
    r"|\bсразу\s+(?:\w+\s+)?(?:выда|оформ|выпиш)\w*\s+(?:\w+\s+)?ваучер"
    r"|ваучер\w*\s+без\s+(?:\w+\s+)?(?:подтвержд|оплат)[^?]*$"  # a question is no order
)
REFUSAL = (
    "Цены задаёт каталог, а бронь оформляю только после вашего подтверждения итоговой суммы. "
    "Служебные инструкции не показываю."
)
NOT_BOOK_RE = re.compile(r"\bне\s+подтверждаю\b")
# S5 (#90): a way out of any stage. «Начать заново» drops the plan and the cart; «другое
# направление» drops the city and shows others from the catalogue.
RESTART_RE = re.compile(
    r"\bнач(?:ать|ни|ните|н[её]м|инаем)\s+"
    r"(?:(?:вс[её]|поиск|план|диалог|поездку)\s+)?(?:заново|сначала|с\s+нуля)\b"
    r"|\b(?:давай(?:те)?|хочу|попробу\w+|поехали)\s+(?:вс[её]\s+)?(?:заново|с\s+нуля)\b"
    r"|\bвс[её]\s+заново\b"
    r"|\bсбрось(?:те)?(?:\s+(?:вс[её]|план|поиск|поездку|диалог))?\s*(?:[.!,;]|$)"
    r"|\bнов(?:ый|ую)\s+(?:поиск|запрос|поездк\w*)\b",
    re.IGNORECASE,
)
# A negation before the phrase («не хочу с нуля») or after it («с нуля не надо»).
NOT_RESTART_RE = re.compile(
    r"\bне\s+(?:\w+\s+)?(?:начин\w+|начат\w*|сбрас\w+|с\s+нуля|заново|сначала)"
    r"|\b(?:с\s+нуля|заново|сначала|поиск\w*|запрос\w*|поездк\w*)\s+(?:\w+\s+)?"
    r"не\s+(?:надо|нужн\w*|хочу|будем)"
)
OTHER_RE = re.compile(
    r"\b(?:друг(?:ое|ой|ую|ие|их)|ино[ейю])\s+(?:направлен\w*|город\w*|мест\w*|стран\w*|курорт\w*)"
    r"|\bне\s+(?:хочу|нравится|подходит)\s+(?:это|этот|эта)\s+(?:направлен|город|мест)"
)
OTHER_VARIANTS_RE = re.compile(r"\bдруг(?:ие|их|ой)\s+вариант\w*")
# «второй половине ноября» is a part of a month, not the second offer.
ORDINAL_RE = re.compile(
    r"\b(перв(?:ый|ая|ое|ую|ого)|втор(?:ой|ая|ое|ую|ого)|трет(?:ий|ья|ье|ью|ьего)"
    r"|четв[её]рт(?:ый|ая|ое|ую|ого)|пят(?:ый|ая|ое|ую|ого))\b(?!\s+(?:половин|недел|декад))"
    r"|^\s*([1-5])\s*$"
)
ORDINALS = {"перв": 0, "втор": 1, "трет": 2, "четв": 3, "пяты": 4, "пята": 4, "пято": 4, "пяту": 4}
# S6 (#31): the user picks transport by a mode word or by a row of the transport table.
# A pick needs a verb or an ordinal with a noun: «хочу только поезда» stays a constraint.
LEG_PICK_RE = re.compile(
    r"\b(?:возьм\w*|бер[уиё]\w*|выб\w+|поед\w*|(?:по)?лет(?:им|ите)|полечу|лечу"
    r"|смен\w*|замен\w*|поменя\w*|переключ\w*|лучше|давай\w*)"
)
LEG_NOUN = r"(?:рейс\w*|перел[её]т\w*|поезд\w*)"
LEG_STEMS = ("перв", "втор", "трет", "четв", "пят", "шест", "седьм", "восьм", "девят")
LEG_ORDINAL = r"(?:перв|втор|трет|четв[её]рт|пят|шест|седьм|восьм|девят)(?:ый|ой|ий|ая|ое|ую)"
LEG_NOT_DATE = (
    r"(?![:.]\d|\s*(?:янв|фев|мар|апр|ма[яй]|июн|июл|авг|сен|окт|ноя|дек"
    r"|ч\b|час|мин|дн|дне|ноч|раз|чел|взр|дет|₽|руб|тыс))"
)
# (ordinal, noun), (noun, digits), (noun, ordinal): the groups are the number and the noun.
LEG_ROW_RES = (
    (re.compile(rf"\b({LEG_ORDINAL})\s+({LEG_NOUN})"), 0, 1),
    (re.compile(rf"\b({LEG_NOUN})\s+(?:№\s*|номер\s+)?(\d{{1,2}})\b{LEG_NOT_DATE}"), 1, 0),
    (re.compile(rf"\b({LEG_NOUN})\s+({LEG_ORDINAL})"), 1, 0),
)
LEG_MODES: dict[str, re.Pattern] = {
    "train": re.compile(r"\bпоезд\w*|\bж/?д\b|сапсан|ласточк"),
    "flight": re.compile(r"\bсамол[её]т\w*|\bперел[её]т\w*|\bавиа\w*|(?:по)?лет(?:им|ите)|полечу"),
    "car": re.compile(r"\bмашин\w*|\bавтомобил\w*|\bна\s+авто\b"),
}
LEG_NONE = {
    "train": "Поездов в предложениях нет.",
    "flight": "Перелётов в предложениях нет.",
    "car": "Поездки на машине в предложениях нет.",
}
LEG_THERE_RE = re.compile(r"\bтуда\b|\bв\s+одну\s+сторону\b")
LEG_BACK_RE = re.compile(r"\bобратно\b|\bназад\b|\bобратн\w+")
LEG_CLAUSE_RE = re.compile(r"[,;]|\bа\b|\bи\b")
# Assumption text starts with "<label>:"; a stated field removes its assumption.
LABELS = {
    "destinations": "направление",
    "month": "когда",
    "date_from": "даты",
    "nights": "длительность",
    "origin": "откуда",
    "budget_rub": "бюджет",
    "children_ages": "возраст ребёнка",
    "adults": "взрослых",
    "max_travel_hours": "время в пути",
}
# #128: a route through several cities. The plan covers the first city; the rest is named here.
# #139: cities as candidates («Сочи или Анапа») get the same notice and chips.
ROUTE = "маршрут"
CANDIDATES = "варианты"
ROUTE_RE = re.compile(
    r"\b(?:потом|затем|далее|оттуда|следом|а\s+после|после\s+(?:этого|того|чего))\b"
    r"|\s[—–]\s(?!(?:что|как\S*|где|куда|стоит|лучше|выгодн\S*|чем|какой)\b)|→|->"  # #141: «— что лучше?»
)
DEFAULT_ORIGIN = "Москва"  # the departure city when none is named
MAX_CANDIDATES = 3  # the first is planned, the 2nd and the 3rd get a chip
STAY_RE = re.compile(
    r"\b(?:\d+|од(?:ин|на|ну)|дв(?:а|е|ое)|тр(?:и|ое)|четыре|пять|шесть|семь|восемь|девять|десять)"
    r"\s*(?:ноч|дн|сут)"
)
AHEAD_RE = re.compile(r"\bчерез\s+\S+\s*(?:ноч|дн|сут)")  # «вылет через 3 дня»: no stay length
OR_RE = re.compile(r"\bили\b")  # cities joined by «или» are candidates, never a route
# The adults are not named: 2 by default. With a child this becomes a question (#112).
PARTY_UNKNOWN = "взрослых: 2 (состав не назван)"
# #113: the refused places live in this assumption (TripSpec has no field for them).
EXCLUDED = "исключено"


# #114: «тепло» in the wishes. Estimate without a source, like the climate of the catalogue:
# in the cold half of the year a destination counts as warm if its November air is this warm.
WARM_AIR_C = 15.0
COLD_MONTHS = (10, 11, 12, 1, 2, 3, 4)


def suggest_destinations(spec: TripSpec, limit: int = 4, exclude: list[str] | None = None):
    """catalog_destinations, and with the «тепло» wish in the cold months: warm places only.

    If no warm place is left (all refused), the others are shown and marked, never hidden."""
    if "тепло" not in spec.wishes or spec.month not in COLD_MONTHS:
        return catalog_destinations(spec, limit, exclude)
    ranked = catalog_destinations(spec, len(CATALOG), exclude)
    warm = [d for d in ranked if (CATALOG[d.name]["nov"][0] or 0) >= WARM_AIR_C]
    if warm:
        return warm[:limit]
    note = "не тёплое направление: «тепло» в пожеланиях, а тёплых мест не осталось"
    return [d.model_copy(update={"violates": [*d.violates, note]}) for d in ranked[:limit]]


def _label(assumption: str) -> str:
    return assumption.split(":", 1)[0]


def _noted(spec: TripSpec, label: str) -> bool:
    return any(_label(a) == label for a in spec.assumptions)


def merge(spec: TripSpec, found: dict, notes: list[str]) -> TripSpec:
    """Stated fields override the spec; their old assumptions are dropped."""
    data = spec.model_dump()
    stated = {LABELS[k] for k in found if k in LABELS}
    if "destinations" in found:
        stated |= {ROUTE, CANDIDATES}  # a new choice of the city ends the route note
    if stated & {"когда", "даты", "длительность"}:
        data["date_to"] = None  # derived from date_from + nights again
        assumed = [
            a
            for a in data["assumptions"]
            if _label(a) == "даты" and not is_shift_note(a) and not is_past_note(a)
        ]  # a named day moved to next year (#159) is said, not guessed
        if "даты" not in stated and assumed:  # the dates were guessed, not said; a shift is said
            data["date_from"] = None
            stated.add("даты")
    if any(is_too_long_note(n) for n in notes):
        stated.add("длительность")  # a length not taken: its note replaces the old one (#160)
    kept = [a for a in data["assumptions"] if _label(a) not in stated]
    data["assumptions"] = kept + [n for n in notes if n not in kept]
    for k, v in found.items():
        data[k] = data[k] + [w for w in v if w not in data[k]] if k == "wishes" else v
    return TripSpec.model_validate(data)


def refused_before(spec: TripSpec) -> list[str]:
    """The places the user refused in the earlier messages."""
    return [
        p
        for a in spec.assumptions
        if _label(a) == EXCLUDED
        for p in a.split(": ", 1)[1].split(", ")
    ]


def with_refused(spec: TripSpec, refused: list[str]) -> TripSpec:
    kept = [a for a in spec.assumptions if _label(a) != EXCLUDED]
    note = [f"{EXCLUDED}: {', '.join(refused)}"] if refused else []
    return spec.model_copy(update={"assumptions": kept + note})


def party_question(spec: TripSpec) -> list[Question]:
    """A child and no word about the adults: 2 adults stay an assumption, the user can fix it."""
    if not (spec.children_ages and PARTY_UNKNOWN in spec.assumptions):
        return []
    options = ["Один взрослый", "Двое взрослых", "Трое взрослых"]  # no «ребёнок»: age stays
    return [Question(text="Кто едет с ребёнком — сколько взрослых?", options=options)]


def blocking_questions(spec: TripSpec) -> list[Question]:
    """At most 2 questions, only when the message names neither a place nor a wish.

    A place or a wish is enough to move on: the rest becomes assumptions named in the reply.
    One exception (#112): a child, no word about the adults and a fixed place. The guest count
    sets the price and the booking, so it is asked, not assumed. Without a place the question
    only goes next to the options (party_question), it never replaces them.
    """
    party = party_question(spec) if spec.destinations else []
    if spec.destinations or spec.wishes:
        return party
    out = []
    if not spec.month and not spec.date_from:
        m = date.today().month
        options = [MONTH_NAMES[(m + i) % 12] for i in range(3)] + ["пока не знаю"]
        out.append(Question(text="Когда хотите поехать?", options=options))
    if spec.budget_rub is None:
        options = ["до 50 000 ₽", "до 100 000 ₽", "до 200 000 ₽", "не важно"]
        out.append(Question(text="Какой бюджет на всю поездку?", options=options))
    return out


def assume(spec: TripSpec) -> TripSpec:
    """Fill the fields needed for a plan and list each assumption for the user."""
    data = spec.model_dump()
    notes = data["assumptions"]

    def note(label: str, text: str) -> None:
        if all(_label(a) != label for a in notes):
            notes.append(f"{label}: {text}")

    today = date.today()
    if not data["month"] and not data["date_from"]:
        data["month"] = today.month % 12 + 1
        note("когда", f"{MONTH_NAMES[data['month'] - 1]} (срок не назван)")
    if not data["nights"]:
        data["nights"] = DEFAULT_NIGHTS
        note("длительность", f"{DEFAULT_NIGHTS} ночей (не названа)")
    if not data["date_from"]:
        m = data["month"]
        start = today + timedelta(days=1) if m == today.month else future_date(m, 1)
        data["date_from"] = start + timedelta(days=(5 - start.weekday()) % 7)  # Saturday
        end = data["date_from"] + timedelta(days=data["nights"])
        note("даты", f"{data['date_from']:%d.%m}–{end:%d.%m} (точных дат не было)")
    data["month"] = data["month"] or data["date_from"].month
    data["date_to"] = data["date_to"] or data["date_from"] + timedelta(days=data["nights"])
    if not data["origin"]:
        data["origin"] = DEFAULT_ORIGIN
        note("откуда", "Москва (город вылета не назван)")
    if data["budget_rub"] is None:
        cheap = "недорого" in data["wishes"]
        note("бюджет", "не задан — сначала дешёвые" if cheap else "не задан — разные цены")
    return TripSpec.model_validate(data)


def is_route(message: str, destinations: list[str]) -> bool:
    """#128: several cities named as a route («потом в Казань», two cities each with nights).

    «Сочи или Анапа» are candidates, not a route: see severals().
    """
    low = message.lower().replace("ё", "е")
    if len(destinations) < 2 or OR_RE.search(low):
        return False
    stays = STAY_RE.findall(AHEAD_RE.sub(" ", low))
    return bool(ROUTE_RE.search(low)) or len(stays) > 1


def severals(message: str, destinations: list[str]) -> str | None:
    """ROUTE or CANDIDATES when the message names several cities; the plan covers the first."""
    if len(destinations) < 2:
        return None
    return ROUTE if is_route(message, destinations) else CANDIDATES


def route_note(destinations: list[str], kind: str = ROUTE) -> str:
    rest = ", ".join(destinations[1:])
    if kind == CANDIDATES:
        return f"{CANDIDATES}: в плане сначала {destinations[0]}, ещё: {rest}"
    return f"{ROUTE}: в плане только {destinations[0]}, не вошло: {rest}"


def route_reply(destinations: list[str], kind: str = ROUTE) -> tuple[str, list[Question]]:
    """The notice and the chips «План: город»; a city without offers (out of the data) gets none."""
    first = destinations[0]
    if kind == CANDIDATES:
        tail = destinations[1:]
        chips = with_offers(tail[: MAX_CANDIDATES - 1])
        text = f"Сравниваю сначала {first}."
        if chips:
            text += f" {', '.join(chips)} — {'кнопкой' if len(chips) == 1 else 'кнопками'} ниже."
        if left := [d for d in tail if d not in chips]:
            text += f" Не вошло: {', '.join(left)}."
        questions = [Question(text="Что планировать?", options=[f"План: {d}" for d in chips])]
        return text, questions if chips else []
    rest = ", ".join(destinations[1:])
    text = (
        f"В плане только: {first}. Поездку {' → '.join(destinations)} "
        f"как один план я пока не собираю. Не вошло: {rest}. Выберите, что планировать."
    )
    options = [f"План: {d}" for d in destinations if resolve_place(d) is not None]
    return text, [Question(text="Что планировать?", options=options)] if len(options) > 1 else []


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _rub(n: int) -> str:
    return f"{n:,} ₽".replace(",", " ")


def _ordinal(text: str) -> int | None:
    m = ORDINAL_RE.search(text.lower())
    if not m:
        return None
    return ORDINALS[m.group(1)[:4]] if m.group(1) else int(m.group(2)) - 1


LegCommand = tuple[str, str | int, str | None]


def _leg_row(clause: str) -> tuple[int, str | None] | None:
    """(row from 0, mode of the noun or None) of «второй поезд», «рейс 3»: the row counts
    among the offers of the named mode; «рейс» names no mode."""
    for rx, num, noun in LEG_ROW_RES:
        if m := rx.search(clause):
            n = m.group(num + 1)
            row = (
                int(n) - 1 if n.isdigit() else next(i for i, st in enumerate(LEG_STEMS) if st in n)
            )
            word = m.group(noun + 1)
            return row, "train" if word.startswith("поезд") else "flight" if word[
                :4
            ] == "перел" else None
    return None


def leg_commands(text: str) -> list[LegCommand]:
    """Picks of transport in the message: (kind, mode or row, direction or mode filter).

    A «row» command holds the row among the offers of its mode filter (None: the whole table).
    Empty when the message is not a pick: no verb and no ordinal with a noun, or a negation.
    """
    t = text.lower().replace("ё", "е")
    if NEGATION_RE.search(t) or BOOK_RE.search(t):
        return []
    verb = bool(LEG_PICK_RE.search(t))
    out: list[LegCommand] = []
    for clause in LEG_CLAUSE_RE.split(t):
        if row := _leg_row(clause):
            out.append(("row", row[0], row[1]))
            continue
        direction = "there" if LEG_THERE_RE.search(clause) else None
        direction = "back" if LEG_BACK_RE.search(clause) else direction
        out += [
            ("mode", m, direction)
            for m, p in LEG_MODES.items()
            if (verb or direction) and p.search(clause)
        ]
    return out


def _explain(plan: Plan) -> list[Explanation]:
    """Why of the selected trip, from the facts of the offers as the optimizer writes them.

    The old text names the totals of the old selection, so it must not stay after a switch.
    """
    trips = enumerate_trips(plan.offers)
    chosen = set(plan.selected)
    trip = next((t for t in trips if set(t.ids) <= chosen), None)
    if trip is None:
        return []
    pool, fits = _pool(plan.spec, trips)
    hotel = Explanation(
        offer_id=trip.hotel.offer_id,
        why=_hotel_why(plan.spec, trip, pool),
        tradeoffs=_sacrificed(plan.spec, trip, pool),
        risks=_risks(plan.spec, trip, fits, trips),
    )
    return [hotel, *(_leg_why(trip, leg, plan.offers) for leg in trip.legs)]


def _cheapest(legs: list[TransportOffer]) -> TransportOffer | None:
    return min(legs, key=lambda x: (x.price_rub, x.offer_id), default=None)


def switch_transport(plan: Plan, text: str) -> tuple[Plan | None, str]:
    """The plan with the transport the user asked for, or None and the reason ("" if the
    message is not a pick).

    Only offers of the plan are chosen (TRAP-4). A mode word takes the selected leg of that
    mode, or else the cheapest one. The hotel stays; its transfers are derived again for the
    new legs. The car goes both ways, as in the optimizer.
    """
    commands = leg_commands(text)
    if not commands:
        return None, ""
    hotel = next(
        (o for o in plan.offers if isinstance(o, HotelOffer) and o.offer_id in plan.selected), None
    )
    if hotel is None:
        return None, "Сначала выберите отель."
    table = [o for o in plan.offers if isinstance(o, TransportOffer)]
    dirs = {
        "there": [x for x in table if x.destination == hotel.city],
        "back": [x for x in table if x.origin == hotel.city],
    }
    now = {d: next((x for x in xs if x.offer_id in plan.selected), None) for d, xs in dirs.items()}
    new, touched = dict(now), set()
    for kind, what, direction in commands:
        # The spec filtered this mode out of the offers (offers.py): «возьми поезд» then
        # is a new constraint, and the caller re-plans the trip instead.
        want = direction if kind == "row" else what  # the mode asked for, if any
        if want and plan.spec.modes and want not in plan.spec.modes:
            return None, ""
        if kind == "row":
            rows = [x for x in table if direction in (None, x.mode)]
            if not 0 <= what < len(rows):
                return None, f"Рейса {what + 1} нет: в таблице их {len(rows)}."
            d = "there" if rows[what] in dirs["there"] else "back"
            new[d] = rows[what]
            touched.add(d)
            continue
        found = False
        for d in [direction] if direction else ["there", "back"]:
            same = [x for x in dirs[d] if x.mode == what]
            leg = now[d] if now[d] in same else _cheapest(same)
            if leg:
                new[d], found = leg, True
                touched.add(d)
        if not found:
            return None, LEG_NONE[what]
    # The car goes both ways: the direction the user did not name follows the other one.
    legs = [x for x in new.values() if x]
    if len(legs) == 2 and (legs[0].mode == "car") != (legs[1].mode == "car"):
        loose = [d for d in new if d not in touched]
        car = new[next(iter(touched))].mode == "car" if len(touched) == 1 else None
        fit = _cheapest([x for x in dirs[loose[0]] if (x.mode == "car") == car]) if loose else None
        if fit is None:
            return None, "Машина едет туда и обратно: смешать её с поездом или рейсом нельзя."
        new[loose[0]] = fit
    chosen = [x.offer_id for x in new.values() if x]
    if chosen == [x.offer_id for x in now.values() if x]:
        return None, "Этот транспорт уже выбран."
    by_id = {o.offer_id: o for o in plan.offers}
    kept = [i for i in plan.selected if not isinstance(by_id.get(i), TransportOffer)]
    out = plan.model_copy(
        update={
            "selected": pick_hotel([*kept, *chosen], plan.offers, hotel.offer_id),
            "changes": [],
        }
    )
    out.timeline, out.issues = build_timeline(out)
    out.explanations = _explain(out)
    titles = [x.title for x in new.values() if x]
    return out, "Транспорт заменён: " + "; ".join(titles) + "."


def _confirmed(text: str) -> bool:
    return bool(CONFIRM_RE.search(text)) and not NEGATION_RE.search(text)


def _tail(spec: TripSpec, hint: str) -> list[str]:
    head = ["Допущения: " + "; ".join(spec.assumptions) + "."] if spec.assumptions else []
    return head + [hint]


def _text_clarify(questions: list[Question]) -> str:
    return "Чтобы подобрать поездку, уточню: " + " ".join(q.text for q in questions)


def _text_inspire(plan: Plan) -> str:
    spec = plan.spec
    guests = spec.adults + len(spec.children_ages)
    lines = ["Подходящие направления (цены, климат и время в пути — оценка, синтетика):"]
    for i, d in enumerate(plan.destinations, 1):
        climate = "".join(
            f", {label} {v:g} °C" for label, v in (("воздух", d.air_c), ("вода", d.water_c)) if v
        )
        line = (
            f"{i}. {d.name} ({d.country}) — от {_rub(d.est_total_rub)} на {guests} чел., "
            f"в пути {d.travel_hours:g} ч{climate}. {_cap(d.why)}."
        )
        if d.tradeoffs:
            line += f" Минусы: {'; '.join(d.tradeoffs)}."
        if d.violates:
            line += f" Нарушает требования: {'; '.join(d.violates)}."
        lines.append(line)
    if "тепло" in spec.wishes and spec.month in COLD_MONTHS and spec.month != 11:
        lines.append(
            "Тепло оцениваю по климату ноября из каталога: других месяцев в данных нет, "
            "в вашем месяце может быть холоднее."
        )
    return "\n".join(lines + _tail(spec, "Напишите номер или название направления."))


def _selected_total(plan: Plan) -> int:
    return sum(o.price_rub for o in plan.offers if o.offer_id in plan.selected)


# #114: the mode the user asked for, when the data has none: «Поезда … в данных нет».
NO_MODE = {"train": "поезда", "flight": "перелёта", "car": "поездки на машине"}
OFFER_MODE = {"train": "поезд", "flight": "перелёт", "car": "машину"}


def missing_mode(plan: Plan) -> str:
    """The line that names an asked mode the offers do not have (offers.py then takes all
    modes); empty if the spec asks for no mode or at least one leg of it exists."""
    legs = [o for o in plan.offers if isinstance(o, TransportOffer)]
    modes = plan.spec.modes
    if not legs or not modes or any(o.mode in modes for o in legs):
        return ""
    way = f"{plan.spec.origin} → {plan.spec.destinations[0]}"
    asked = " и ".join(NO_MODE[m] for m in modes)
    have = " и ".join(OFFER_MODE[m] for m in dict.fromkeys(o.mode for o in legs))
    return f"{_cap(asked)} {way} в данных нет: предлагаю {have}."


def _text_compare(plan: Plan) -> str:
    spec = plan.spec
    kids = f" + дети {', '.join(map(str, spec.children_ages))} лет" if spec.children_ages else ""
    lines = [
        (
            f"{spec.destinations[0]}, {period(spec)}, "
            f"{spec.nights} ноч., {spec.adults} взр.{kids}. Цены — синтетика."
        ),
        *([line] if (line := missing_mode(plan)) else []),
        "Отели:",
    ]
    hotels = hotel_rows(plan.offers, plan.selected)  # one row per hotel, not per tariff
    pet = spec.pets
    for i, h in enumerate(hotels, 1):
        cancel = "бесплатная отмена" if h.cancellation.refundable else "невозвратный"
        lines.append(
            f"{i}. {h.title} — {h.stars}★, рейтинг {h.rating}, {h.meal}, {cancel} — "
            f"{_rub(h.price_rub)}"
            + (" · нет отметки о животных" if pet and not h.pets_allowed else "")
        )
    legs = [o for o in plan.offers if o.kind == "transport"]
    if legs:
        lines.append("Транспорт:")
    for i, o in enumerate(legs, 1):
        no_pet = " · животных не берут" if pet and not o.pets_allowed else ""
        lines.append(f"Рейс {i}. {o.title} — {_rub(o.price_rub)}{no_pet}")
    chosen = [o.title for o in plan.offers if o.offer_id in plan.selected]
    lines.append(f"Предлагаю: {', '.join(chosen)}. Итого {_rub(_selected_total(plan))}.")
    for e in plan.explanations[:1]:
        lines.append(f"Почему: {e.why}." + "".join(f" {_cap(x)}." for x in e.tradeoffs))
        lines += [f"Риск: {r}." for r in e.risks]
    for c in plan.changes:
        lines.append(f"Изменено {c.what}: {c.before} → {c.after}.")
    return "\n".join(
        lines
        + _tail(spec, "Напишите «беру» или номер отеля; транспорт — «возьми поезд» или «рейс 2».")
    )


def _text_assemble(plan: Plan) -> str:
    lines = ["План поездки:"]
    lines += [f"{e.at:%d.%m %H:%M} — {e.title}" for e in plan.timeline]
    lines += [f"Внимание: {i.text}." for i in plan.issues]
    lines.append(f"Итого {_rub(_selected_total(plan))} (синтетика). Оформляем? Напишите «да».")
    return "\n".join(lines)


def _alternatives(spec: TripSpec, skip: list[str]) -> list[str]:
    """Up to 3 destination names from the catalogue (code, never invented), without `skip`
    and without the places the user refused."""
    names = [d.name for d in suggest_destinations(spec, 4, refused_before(spec))]
    return [n for n in names if n not in skip][:3]


def _hotel_selected(plan: Plan) -> bool:
    return any(o.kind == "hotel" and o.offer_id in plan.selected for o in plan.offers)


def _pet_dead_cities(spec: TripSpec) -> list[str]:
    """Cities where no trip takes the pet: they are no way out of a dead end (#136)."""
    if not spec.pets:
        return []
    return [
        d.name
        for d in suggest_destinations(spec, len(CATALOG))
        if not pets_possible(spec, find_offers(spec, d.name))
    ]


def _pets_dead_end(plan: Plan) -> tuple[Stage, Plan, str, list[Question]]:
    """No trip takes the pet (#131): the reason, other cities where it can go, no cart.

    The legs that take the pet stay selected, a hotel is picked by the user (a number)."""
    spec = plan.spec
    text = dead_end_text(spec, plan.offers)
    if plan.selected:  # the hotels, as the user must see them to pick one
        text = _text_compare(plan).split("\nПредлагаю:")[0] + "\n" + text
    text, questions = _no_offers(plan, text, _pet_dead_cities(spec))
    return ("compare" if plan.selected else "select"), plan, text, questions


def with_offers(names: list[str]) -> list[str]:
    """The names that have offers: the chip «План: город» exists only for them (#142)."""
    return [d for d in names if resolve_place(d) is not None]


def _no_offers(plan: Plan, text: str, skip: list[str] | None = None) -> tuple[str, list[Question]]:
    """The «choose another» exit of the select stage: 2–3 destinations as the text and buttons."""
    names = _alternatives(plan.spec, [*plan.spec.destinations, plan.spec.origin, *(skip or [])])
    if not names:
        return text, []
    plan.destinations = [
        d for d in suggest_destinations(plan.spec, 4, refused_before(plan.spec)) if d.name in names
    ]
    options = f" Например: {', '.join(names)}."
    return text + options, [Question(text="Куда поедем вместо этого?", options=names)]


HINTS: dict[Stage, str] = {
    "select": "Выберите другое направление.",
    "compare": "Напишите «беру» или номер отеля; транспорт — «возьми поезд» или «рейс 2».",
    "assemble": "Напишите «да», чтобы положить план в корзину.",
    "checkout": "Чтобы оформить бронь, напишите «подтверждаю».",
}


def step(prev: ChatReply | None, sid: str, message: str, use_llm: bool = False) -> ChatReply:
    """One user message -> the next ChatReply of the session.

    use_llm: ask the model (llm.py) when it is configured; this blocks on the network, so the
    caller runs it outside the store transaction.
    """
    low = message.lower().replace("ё", "е")
    restart = RESTART_RE.search(message) and not NOT_RESTART_RE.search(low)
    if prev and restart and not REFUSE_RE.search(low):
        # The plan, the cart and the voucher go; what is left of the message starts the trip.
        rest = RESTART_RE.sub(" ", message).strip(" ,.!;:-")
        fresh = step(None, sid, rest, use_llm)
        kept = f" Бронь {prev.voucher.booking_id} остаётся оформленной." if prev.voucher else ""
        return fresh.model_copy(update={"reply": f"Начинаем заново.{kept} {fresh.reply}"})
    if prev and prev.voucher:
        text = f"Бронь {prev.voucher.booking_id} уже оформлена."
        return prev.model_copy(update={"reply": text, "questions": [], "llm": False})
    old = prev or ChatReply(
        session_id=sid,
        stage="clarify",
        reply="",
        plan=Plan(spec=TripSpec()),
        cart=Cart(),
        llm=False,
    )
    t = message.lower()
    booking = old.stage == "checkout" and BOOK_RE.search(t) and not NOT_BOOK_RE.search(t)
    if REFUSE_RE.search(t.replace("ё", "е")) and not booking:
        # The message goes to no model and changes nothing: the plan and the cart stay.
        # At checkout «подтверждаю» books first: the prices come from the catalogue anyway.
        hint = HINTS.get(old.stage, "Опишите поездку: куда, когда и кто едет.")
        return old.model_copy(update={"reply": f"{REFUSAL} {hint}", "questions": [], "llm": False})
    # S6 (#31): «возьми поезд», «второй рейс» pick offers of the plan. It goes to no model and is
    # no constraint: the spec stays, only plan.selected and the timeline change. A message that
    # also states trip fields or moves the dates («давай в Сочи поездом») is a re-plan, as before.
    pure = set(extract(message, bool(old.plan.spec.destinations))[0]) <= {
        "modes"
    } and not SHIFT_RE.search(t.replace("ё", "е"))
    if pure and prev and old.stage in ("compare", "assemble", "checkout"):
        plan, note = switch_transport(old.plan, message)
        if old.stage == "checkout" and (plan or note):
            note = "Корзина уже собрана, транспорт в ней не меняю."
            hint = HINTS["checkout"]
            return old.model_copy(update={"reply": f"{note} {hint}", "questions": [], "llm": False})
        if plan or note:
            body = _text_assemble(plan or old.plan) if old.stage == "assemble" else None
            body = body or _text_compare((plan or old.plan).model_copy(update={"changes": []}))
            text = f"{note}\n{body}" if plan else f"{note} {HINTS[old.stage]}"
            return old.model_copy(
                update={"plan": plan or old.plan, "reply": text, "questions": [], "llm": False}
            )
    idx = _ordinal(message)
    deadline = llm.deadline()
    # A service turn («беру», «да», «подтверждаю», a number) goes to no model: a field it
    # adds would re-plan the trip and send the user back to compare. Free text on assemble or
    # checkout («хотим с бассейном») is a wish: the model reads it and the DAG re-plans (#49).
    # The stage decides: «оформи мне отдых на Алтае» on the first turn is a request, not consent.
    # A new session, clarify and select are never service turns.
    consent = old.stage in ("compare", "assemble", "checkout") and (
        _confirmed(t) or bool(BOOK_RE.search(t))
    )
    pick = old.stage in ("inspire", "compare") and idx is not None
    service = prev is not None and (consent or pick)
    found, notes = extract(
        message,
        bool(old.plan.spec.destinations),
        old.plan.spec.nights or DEFAULT_NIGHTS,
        old.plan.spec.adults,
    )
    new_refused = excluded_places(message)  # #113: «кроме Турции», «в Египте уже были»
    # «Другое направление»: the city goes, the rest of the trip stays; no model reads it.
    other = (
        prev is not None
        and "destinations" not in found
        and bool(
            OTHER_RE.search(low)
            or (OTHER_VARIANTS_RE.search(low) and old.stage in ("inspire", "select"))
            or any(is_excluded(d, new_refused) for d in old.plan.spec.destinations)
        )
    )
    if other:
        found["destinations"] = []
    llm_found = (
        None
        if service or other or not use_llm or is_chip_message(message)  # chips: S9, #76
        else llm.extract_spec(message, old.plan.spec, deadline)
    )
    if SHIFT_RE.search(t.replace("ё", "е")):
        found.pop("nights", None)  # «на неделю позже» moves the dates, the length stays
        if llm_found:
            # The rules read the shift (which end, how far); the model must not add other dates.
            for k in ("nights", *(DATE_KEYS if "date_from" not in found else ())):
                llm_found.pop(k, None)
    if llm_found:
        # The model may not set a length the rules would not take; note and plan then agree (#160).
        rejected = any(is_too_long_note(n) for n in notes)
        said = llm_found.get("nights", 0)
        if said > MAX_NIGHTS and "nights" not in found and not rejected:
            notes.append(too_long_note(f"{said} ночей", old.plan.spec.nights or DEFAULT_NIGHTS))
            rejected = True
        if rejected or said > MAX_NIGHTS:
            llm_found.pop("nights", None)
            llm_found.pop("date_to", None)  # derived from the length again
    if prev is None and "adults" not in found and "adults" not in (llm_found or {}):
        notes.append(PARTY_UNKNOWN)
    if old.stage in ("inspire", "select") and idx is not None and idx < len(old.plan.destinations):
        found["destinations"] = [old.plan.destinations[idx].name]
    # The departure city is no destination (#139): «Москва — Питер — Казань» starts in Москва.
    # It runs after the model too (#141): the model may return the departure city as a stay.
    start_default = not found.get("origin") and (
        not old.plan.spec.origin or any(_label(a) == "откуда" for a in old.plan.spec.assumptions)
    )

    told: list[str] = []

    def without_origin(f: dict) -> dict:
        named_here = f.get("destinations", [])
        start = f.get("origin") or old.plan.spec.origin or DEFAULT_ORIGIN
        if len(named_here) > 1 and start in named_here:
            f = {**f, "destinations": [d for d in named_here if d != start]}
            if start == DEFAULT_ORIGIN and start_default:
                told[:] = [f"откуда: {start} — город вылета по умолчанию, напишите «из …»"]
        return f

    def merged(f: dict) -> TripSpec:
        base, extra = old.plan.spec, []
        if told and not f.get("origin"):  # the model named a departure city: no default to tell
            # One «откуда» line: the new one replaces the old.
            kept = [a for a in base.assumptions if _label(a) != "откуда"]
            base, extra = base.model_copy(update={"assumptions": kept}), told
        return merge(base, f, [*notes, *extra])

    found = without_origin(found)
    spec = merged(found)
    used = False  # the LLM output changed this reply
    if llm_found:
        with_model = without_origin(
            with_llm(found, llm_found, [*refused_before(old.plan.spec), *new_refused])
        )
        spec_model = merged(with_model)
        if spec_model != spec:
            found, spec, used = with_model, spec_model, True
    swapped = [d for d in old.plan.spec.destinations if d not in spec.destinations]
    # A refusal holds for the next turns; a place named later (even a city of that country) ends it.
    named = found.get("destinations", [])
    refused = [
        r
        for r in dict.fromkeys([*refused_before(old.plan.spec), *new_refused])
        if not any(is_excluded(d, [r]) for d in named)
    ]
    spec = with_refused(spec, refused)
    route = severals(message, found.get("destinations", []))
    if route:  # the lost city stays visible (#128, #139)
        spec = spec.model_copy(
            update={"assumptions": [*spec.assumptions, route_note(spec.destinations, route)]}
        )
    elif old.stage not in ("compare", "assemble", "checkout"):
        # A route of an earlier message: the notice comes with the first plan (after a question)
        route = next((k for k in (ROUTE, CANDIDATES) if _noted(spec, k)), None)

    def reply(stage: Stage, plan: Plan, text: str, cart: Cart = old.cart, **kw) -> ChatReply:
        return ChatReply(
            session_id=sid, stage=stage, reply=text, plan=plan, cart=cart, llm=used, **kw
        )

    if prev is None and (questions := blocking_questions(spec)):
        return reply("clarify", Plan(spec=spec), _text_clarify(questions), questions=questions)
    spec = assume(spec)
    if not spec.destinations:
        dests = suggest_destinations(spec, 5, refused)  # 5: four are left without the origin
        if other:
            gone = old.plan.spec.destinations
            ranked = suggest_destinations(spec, len(CATALOG), refused)
            shown = [d.name for d in old.plan.destinations] if old.stage == "inspire" else []
            # Without the options already shown, if enough others are left.
            # The origin is no destination: a stay in the city of departure has no transport (#94).
            dests = [d for d in ranked if d.name not in [*gone, *shown, spec.origin]]
            dests = (
                dests
                if len(dests) >= 2
                else [d for d in ranked if d.name not in [*gone, spec.origin]]
            )[:3]
        dests = [d for d in dests if d.name != spec.origin][:4]
        plan = Plan(spec=spec, destinations=dests)
        if not other:
            return reply("inspire", plan, _text_inspire(plan), questions=party_question(spec))
        gone_text = f"Убираю «{', '.join(gone)}» из плана, корзину очищаю. " if gone else ""
        question = Question(text="Куда поедем?", options=[d.name for d in dests])
        text = gone_text + _text_inspire(plan)
        return reply("inspire", plan, text, Cart(), questions=[question, *party_question(spec)])

    dated = bool(old.plan.spec.date_from and old.plan.spec.date_to)
    te = t.replace("ё", "е")
    if not found and prev and dated and not SHIFT_RE.search(te) and DATE_EDIT_RE.search(te):
        # The message asks to move the dates but says how far or which way in no known words (S2).
        s = old.plan.spec
        text = (
            f"Не понял, что изменить в датах. Сейчас {period(s)}. "
            "Напишите, например: «на два дня позже», «обратно на день раньше» "
            "или «с 20 ноября на 5 ночей»."
        )
        return old.model_copy(update={"reply": text, "questions": [], "llm": False})
    if "date_from" not in found:
        try:
            spec = shift_dates(spec, message)  # «на два дня позже» moves the current dates (S2)
        except ValueError as e:
            text = (
                f"{e} Из этого сообщения ничего не применено. "
                f"Сейчас {period(spec)}. {HINTS.get(old.stage, '')}"
            )
            return old.model_copy(update={"reply": text, "questions": [], "llm": False})
    # «Было → стало» belongs to the reply of the re-plan only, not to the next steps.
    plan = old.plan.model_copy(update={"spec": spec, "changes": []})
    if dirty_nodes(old.plan.spec, spec) or not plan.offers:
        # The DAG recomputes only the nodes that depend on the changed fields (T6, #10).
        plan, cart, done = replan(old.plan, spec, old.cart)
        if not plan.offers:
            # TODO(T4, #8): the geocoder and synthetic offers cover any city.
            text = (
                f"Направления «{spec.destinations[0]}» пока {NOT_IN_CATALOG}, предложений нет. "
                f"{HINTS['select']}"
            )
            text, questions = _no_offers(plan, text)
            # «Ханой или Сочи»: Сочи is named (#142)
            chips = with_offers(spec.destinations[1:MAX_CANDIDATES]) if route else []
            if chips:
                text = f"{text} Из названных в каталоге есть: {', '.join(chips)} — кнопкой ниже."
                options = [f"План: {d}" for d in chips]
                questions = [Question(text="Что планировать?", options=options), *questions]
            return reply("select", plan, text, cart, questions=questions)
        if not pets_possible(spec, plan.offers):
            stage, plan, text, questions = _pets_dead_end(plan)
            if plan.changes:  # the dropped hotel must show as a change (#136)
                text = "\n".join(["Было → стало:", *describe(plan.changes), text])
            return reply(stage, plan, text, cart, questions=questions)
        why = llm.explain(plan, deadline) if use_llm and "ranking" in done else None
        if why and why != plan.explanations:
            plan.explanations, used = why, True  # checked against the plan facts (TRAP-4)
        text = _text_compare(plan.model_copy(update={"changes": []}))
        if spec.origin in spec.destinations:
            text += f" Город вылета {spec.origin} совпадает с направлением: перевозки в плане нет."
        if plan.changes:
            text = "\n".join(["Было → стало:", *describe(plan.changes), text])
        if old.stage == "select" and swapped and not old.plan.offers:
            # The user named a city that is not in the catalogue and picked another one:
            # the reply says so, the new city never comes silently (#93).
            gone, now = ", ".join(swapped), spec.destinations[0]
            text = f"«{gone}» {NOT_IN_CATALOG}. По вашему выбору — {now}.\n{text}"
        questions = party_question(spec)
        if route:
            notice, chips = route_reply(spec.destinations, route)
            return reply("compare", plan, f"{notice}\n{text}", cart, questions=[*chips, *questions])
        return reply("compare", plan, text, cart, questions=questions)

    if old.stage == "compare" and (idx is not None or _confirmed(t)):
        hotels = hotel_rows(plan.offers, plan.selected)
        picks = idx is not None and idx < len(hotels)
        if not picks and not pets_possible(spec, plan.offers) and not _hotel_selected(plan):
            stage, plan, text, questions = _pets_dead_end(plan)  # «беру» is no consent (#131)
            return reply(stage, plan, text, questions=questions)
        if idx is not None and idx < len(hotels):
            # The hotel brings its own transfers for the selected legs (T7, #13).
            plan.selected = pick_hotel(plan.selected, plan.offers, hotels[idx].offer_id)
        plan.timeline, plan.issues = build_timeline(plan)
        return reply("assemble", plan, _text_assemble(plan))
    if old.stage == "assemble" and _confirmed(t):
        cart = fill_cart(plan)
        text = (
            f"В корзине {len(cart.items)} поз. на {_rub(cart.total_rub)} "
            f"(синтетика, оплата имитируется). {HINTS['checkout']}"
        )
        return reply("checkout", plan, text, cart)
    if booking:
        try:  # the same rules as POST /api/checkout; the user saw the stored cart total
            return checkout_reply(old, old.cart.total_rub).model_copy(update={"llm": used})
        except CartRefusal as e:
            return reply(old.stage, plan, e.text)
    text = HINTS.get(old.stage, "Уточните запрос.")
    if old.stage == "select":
        text, questions = _no_offers(plan, text, _pet_dead_cities(spec))
        return reply("select", plan, text, questions=questions)
    return reply(old.stage, plan, text)


def chat(req: ChatRequest, store: SessionStore) -> ChatReply:
    """POST /api/chat: an unknown session_id starts a new session with that id.

    The LLM runs before the store transaction: BEGIN IMMEDIATE locks the whole SQLite file,
    and a slow model must not hold the other sessions. The draft is written only if the
    session did not change meanwhile; otherwise the step runs again on the new state, rules
    only, inside the transaction.
    """
    sid = req.session_id or uuid4().hex
    prev = store.get(sid)
    draft = step(prev, sid, req.message, use_llm=True)
    return store.update(sid, lambda cur: draft if cur == prev else step(cur, sid, req.message))
