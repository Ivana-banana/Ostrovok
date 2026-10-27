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
from calendar import monthrange
from datetime import date, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from api import catalog, llm
from api.answers import is_hotel_compare, plan_fact, span, without_plan_fields
from api.cart import CartRefusal, checkout_reply, edit_cart, fill_cart, keep_refusal, sync_plan
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
    shift_matches,
)
from api.dag import changes as plan_changes
from api.extract import (
    ASKING_NOTE,
    DATE_KEYS,
    DEFAULT_NIGHTS,
    HOTEL_NOTE,
    MAX_NIGHTS,
    MONTH,
    MONTH_NAMES,
    MONTHS,
    NUM,
    NUMS,
    PAST_WINDOW,
    PER_NIGHT_NOTE_RE,
    SEASONS,
    excluded_places,
    extract,
    is_child_question,
    is_chip_message,
    is_excluded,
    is_past_note,
    is_too_long_note,
    llm_past_dates,
    past_year_note,
    too_long_note,
    with_llm,
)
from api.fixtures import CATALOG, build_timeline, future_date
from api.inspire import suggest_destinations
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
    # «ваши инструкции по багажу» asks for the rules of the trip, not for the prompt (#81);
    # a hidden or initial instruction stays an attack with any topic
    r"(?:(?:свои|твои|ваши)\s+(?:\w+\s+)?инструкц"
    r"(?!\w*\s+(?:по|для)\s+(?:багаж|заселен|выселен|заезд|выезд|трансфер|прилет|вылет|отмен))"
    r"|(?:скрыт\w+|исходн\w+|начальн\w+)\s+(?:\w+\s+)?инструкц)"
    r"|\bигнорир\w*\s+(?:\w+\s+){0,2}инструкц"
    r"|(?:постав|установ|сдела|измен|смен)\w*\s+(?:\w+\s+)?цен\w*\s+(?:на\s+|в\s+)?(?:0|н[оу]л\w*)\b"
    r"|\bцен\w*\s+должн\w*\s+быть\s+(?:0|н[оу]л\w*)\b"
    r"|\bне\s+подтверждай\b"
    r"|\bне\s+(?:спрашивай|запрашивай|проси|жди|требуй)\w*\s+(?:\w+\s+)?(?:подтвержд|согласи)"
    r"|\b(?:за)?(?:оформ|бронир|выда|выпиш)\w*\s+(?:\w+\s+){0,2}без\s+(?:\w+\s+)?"
    r"(?:подтвержд|оплат\w*\b(?!\s+(?:на\s+месте|на\s+ресепшен|при\s+(?:заселении|заезде)"
    r"|в\s+отеле|заранее)))"  # «без оплаты сейчас» is an order to skip the payment (#81)
    r"|\bсразу\s+(?:\w+\s+)?(?:выда|оформ|выпиш)\w*\s+(?:\w+\s+)?ваучер"
    r"|ваучер\w*\s+без\s+(?:\w+\s+)?(?:подтвержд|оплат)[^?]*$"  # a question is no order
)
REFUSAL = (
    "Цены задаёт каталог, а бронь оформляю только после вашего подтверждения итоговой суммы. "
    "Служебные инструкции не показываю."
)
NOT_BOOK_RE = re.compile(r"\bне\s+подтверждаю\b")
# #81: a question at assemble or checkout is no edit: the spec and the cart stay. A question has
# «?» or a question word; a request («хотим», «можно», «с нами едет»), a date move, a new place or
# party size, or a budget makes it an edit. A bare «?» yields to a consent word; a question word
# yields to a strong one («да», «беру»), not to «корзин…» (as in «сколько стоит корзина?»).
QUESTION_WORD_RE = re.compile(r"\b(?:сколько|есть\s+ли|включен\w*|входит|входят|почему|зачем|ли)\b")
STRONG_CONFIRM_RE = re.compile(r"\b(?:да|ок|окей|хорошо|подходит|давай\w*|бер[уиё]\w*)\b")
# «нужен ли трансфер?» and «можно ли …?» ask; «нужен трансфер» and «можно с бассейном» order.
# A comparative («подешевле», «ближе») or «а есть что-нибудь…» asks for other offers: an edit.
# «а трансфер нужен?» and «можно будет отменить?» ask too (#181). A wish after «есть» or «можно
# ли с» («а с бассейном есть?», «можно ли с бассейном?») is a request: the model reads it.
REQUEST_RE = re.compile(
    r"\b(?:можно|нужен|нужн\w+|надо)\b(?!\s+(?:ли|будет)\b)"
    r"|\bс\s+\w+\s+есть\b|\bесть\s+с\s+\w|\bможно\s+(?:ли|будет)\s+с\s+\w"
    r"|\b(?:хоч\w+|хотим|добав\w+|убер\w+|замен\w+|смен\w+|поменя\w+|измен\w+|перенес\w+"
    r"|подвин\w+|сдвин\w+|исключ\w+|с\s+нами|поедем|планиру\w+"
    r"|(?:по)?(?:дешевле|дороже|ближе|лучше|больше|меньше)"
    r"|а\s+есть\s+(?:что-нибудь|что-то|вариант\w*|друг\w+))\b"
)
# A bare «трансфер нужен?» or «можно?» asks. «с бассейном можно?», «поездом можно?», «у моря
# нужно?» name a wish before the word: a request, as before #181.
BARE_ASK_RE = re.compile(r"\b(?:можно|нужен|нужн\w+|надо)\s*\?")
WISH_BEFORE_RE = re.compile(
    r"\b(?:с|со|без|у)\s+\w+|\b(?:поезд|самол|перел|рейс|авиа|автобус|машин|такси|мор)\w*"
)
# «что если рейс задержат?» asks what would happen: it picks no transport. «возьми поезд, если
# есть?» has a choice verb: it picks.
HYPOTHESIS_RE = re.compile(r"\bесли\b[^?]*\?|\b(?:задерж|опозда)\w*")
CHOICE_RE = re.compile(
    r"\b(?:(?:возьм|давай|хоч|хотим|поменя|смен|замен|переключ|выбер|оформ)\w*|бер(?:у|и|ем|ите|ете)\b)"
)
# #193: «давайте без трансфера» is an edit even with a consent word in it. «нужен ли трансфер?»
# and «а трансфер нужен?» ask; they are not here.
NO_TRANSFER_RE = re.compile(
    r"(?<!\bне\s)\bбез\s+(?:\w+\s+)?трансфер\w*"
    r"|\b(?:убер\w+|убра\w+|удал\w+|исключ\w+)\s+(?:\w+\s+)?трансфер\w*"
    r"|\bоткаж\w*\s+от\s+(?:\w+\s+)?трансфер\w*"
    r"|\bне\s+нуж(?:ен|на|но|ны)\s+(?:\w+\s+)?трансфер\w*"
    r"|\bтрансфер\w*\s+(?:нам\s+)?не\s+(?:нуж(?:ен|на|но|ны)|надо)\b"
)
# «как добраться без трансфера?», «сколько стоит без трансфера?», «без трансфера дороже?» ask.
# A bare «трансфер не нужен?» asks too; «а давайте без трансфера?» and «можно без трансфера?» order.
ASKS_RE = re.compile(r"(?<!\bтак\s)\bкак\b(?!-)|\bсколько\b|\bли\b")
ASKS_COMPARE_RE = re.compile(r"\b(?:дороже|дешевле|выгодн\w*)\b")

EDIT_KEYS = {
    "destinations", "date_from", "date_to", "month", "nights", "adults", "children_ages",
    "origin", "budget_rub", "hotel_budget_rub", "pets", "max_travel_hours",
}  # fmt: skip
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
    "hotel_budget_rub": HOTEL_NOTE,
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


def _label(assumption: str) -> str:
    return assumption.split(":", 1)[0]


def resolve_hotel_budget(data: dict) -> None:
    """The hotel budget stated per night is a total of the nights the plan has now (#41).

    TripSpec holds the total only, so the price of a night lives in the assumption
    «бюджет отеля: 10 000 ₽ за ночь…». A new `nights` then moves the total with it.
    """
    for i, a in enumerate(data["assumptions"]):
        if (m := PER_NIGHT_NOTE_RE.match(a)) and data["nights"]:
            night, n = int(m.group(1).replace(" ", "")), data["nights"]
            data["hotel_budget_rub"] = night * n
            data["assumptions"][i] = (
                f"{HOTEL_NOTE}: {_rub(night)} за ночь, {_rub(night * n)} за {n} ноч."
            )


def _noted(spec: TripSpec, label: str) -> bool:
    return any(_label(a) == label for a in spec.assumptions)


def in_month(day: date, month: int) -> tuple[date, str | None]:
    """The same day of the next such month, not in the past; the last day if the month is short.

    The note (#159) comes when the day passed in the last month: that may be a slip.
    """
    today = date.today()
    for year in (today.year, today.year + 1):
        moved = date(year, month, min(day.day, monthrange(year, month)[1]))
        if moved >= today:
            break
    here = date(today.year, month, min(day.day, monthrange(today.year, month)[1]))
    slip = moved.year > today.year and today - PAST_WINDOW <= here < today
    return moved, past_year_note(here, moved.year) if slip else None


def dates_named(spec: TripSpec) -> bool:
    """The dates are the user's own: there is a day, and no note says it was a guess."""
    return bool(spec.date_from) and not any(
        _label(a) == "даты" and not is_shift_note(a) and not is_past_note(a)
        for a in spec.assumptions
    )


def _month_number(word: str) -> int:
    return next(n for k, n in MONTHS.items() if word.startswith(k))  # «март» before «ма»


def month_call(text: str, month: int) -> tuple[str, int | None]:
    """What a month in the message means for dates the user named (#190).

    Returns (verdict, month): "move" is a clear request; "stay" is a rejection («не в январе»,
    «январь не подходит»); "ask" is unclear (a question, «может», a past tense, «не в X, а в Y»),
    the month is the one to ask about; "skip" is no wish for a month («отпуск до января»).
    """
    t = text.lower().replace("ё", "е")
    m = re.search(rf"\b{MONTH}", t) or re.search(rf"\b({'|'.join(SEASONS)})\b", t)
    if not m:
        return "move", month
    before, rest = t[: m.start()], t[m.end() :]
    if re.search(r"\b(?:до|после|с|со|перед)\s+$", before):
        return "skip", month
    # «не дорого, в январе»: a comma ends the reach of the «не» (#198)
    negated = bool(re.search(r"\b(?:не|кроме)\s+(?:[^\s,;]+\s+){0,2}$|\bбез\s+$", before))
    if negated and re.match(r"\s*,?\s*(?:а|но|лучше|давай)\b", rest):
        other = re.search(rf"\b{MONTH}", rest)  # «не в январе, а в феврале»: the month to ask about
        return "ask", _month_number(other.group(1)) if other else None
    unclear = (
        r"\bможет\b|\bможно\b|\bа если\b|\bчто если\b|\bкак насч|\b(?:был|была|были|было)\b|побывал"
    )
    if "?" in t or re.search(unclear, t):
        return "ask", month
    clause = re.split(r"[.;!?]", rest)[0]
    # Without a comma a «не» after the month is a refusal («в январе не поедем»). After a comma,
    # «но», «и» or «а» it may belong to a neighbour («в январе, не дорого»): only a verb of
    # refusing makes it one (#198).
    parts = re.split(r",|\s(?:но|и|а)\s", clause, maxsplit=1)
    near, after = parts[0], parts[1] if len(parts) > 1 else ""
    stop = r"нельзя|отпадает|отпадают|исключен|невозможн"
    verbs = (
        r"подход|получ|выйд|выход|смож|мож|годит|хоч|хот|над|нуж|вариант|устраива|катит"
        r"|рассматр|будет|удобн"
    )
    # «не поедем» ends the part: «не поедем на самолёте» only drops a way to travel
    moves = r"поед|полет|лет|ед[еи]м"
    end = r"(?=\s*(?:,|;|$))"
    gone = rf"\s*(?:\S+\s+){{0,2}}?(?:не\b|{stop})"
    refusing = (
        rf"\s*(?:\S+\s+){{0,2}}?(?:не\s+(?:очень\s+)?(?:{verbs})"
        rf"|не\s+(?:{moves})\w*{end}|не{end}|{stop})"
    )
    if negated or re.match(gone, near) or re.match(refusing, after):
        return "stay", month
    return "move", month


def month_reply(spec: TripSpec, target: int | None, hint: str) -> tuple[str, list[Question]]:
    """The reply to a month that moves nothing: the dates stay, or a question about the move."""
    own = f"Даты {period(spec)} вы назвали сами."
    if target is None:
        return f"{own} Назовите нужные даты или месяц, на который перенести.", []
    name = MONTH_NAMES[target - 1]
    if target == spec.date_from.month:
        return f"{own} Они остаются. {hint}".strip(), []
    if target == spec.date_to.month:
        return f"{own} Они уже заходят в {name}. Если нужны другие, назовите даты.", []
    move = f"Перенести на {name}"
    text = f"{own} {move} (тот же день и длина) или назовёте даты?"
    return text, [Question(text="Что сделать с датами?", options=[move])]


def merge(spec: TripSpec, found: dict, notes: list[str]) -> TripSpec:
    """Stated fields override the spec; their old assumptions are dropped."""
    data = spec.model_dump()
    stated = {LABELS[k] for k in found if k in LABELS}
    if any(_label(n) == HOTEL_NOTE for n in notes):  # a price per night: no total until nights
        stated.add(HOTEL_NOTE)
        data["hotel_budget_rub"] = found.get("hotel_budget_rub")
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
        elif "даты" not in stated and data["date_from"] and found.get("month"):
            # Named dates move to the new month, day and length kept (#190).
            if data["date_from"].month != found["month"]:
                data["date_from"], past = in_month(data["date_from"], found["month"])
                stated.add("даты")  # the old notes about the old dates go
                notes = [*notes, *([past] if past else [])]
    if any(is_too_long_note(n) for n in notes):
        stated.add("длительность")  # a length not taken: its note replaces the old one (#160)
    kept = [a for a in data["assumptions"] if _label(a) not in stated]
    data["assumptions"] = kept + [n for n in notes if n not in kept]
    for k, v in found.items():
        data[k] = data[k] + [w for w in v if w not in data[k]] if k == "wishes" else v
    resolve_hotel_budget(data)
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
    if spec.budget_rub is None and spec.hotel_budget_rub is None:
        options = ["до 50 000 ₽", "до 100 000 ₽", "до 200 000 ₽", "не важно"]
        out.append(Question(text="Какой бюджет на всю поездку?", options=options))
    return out


def assume(spec: TripSpec) -> TripSpec:
    """Fill the fields needed for a plan and list each assumption for the user."""
    data = spec.model_dump()
    notes = data["assumptions"]

    def note(label: str, text: str) -> None:
        # A note of a past date (#159, #164) is not a guess: the guess note comes beside it.
        if all(_label(a) != label or is_past_note(a) for a in notes):
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
    else:
        # TRAP-4 (#104): no flight from a city off the hub list; the nearest hub, said aloud.
        hub, why = catalog.origin_hub(data["origin"])
        data["origin"] = hub["name"]
        if why:
            note("откуда", why[:1].lower() + why[1:])
    if data["hotel_budget_rub"] is not None:  # the rules wrote their note; the LLM did not
        note(HOTEL_NOTE, f"{_rub(data['hotel_budget_rub'])} за все ночи")
    resolve_hotel_budget(data)
    if data["budget_rub"] is None and data["hotel_budget_rub"] is None:
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
            "selected": keep_refusal(
                plan, pick_hotel([*kept, *chosen], plan.offers, hotel.offer_id)
            ),
            "changes": [],
        }
    )
    out.timeline, out.issues = build_timeline(out)
    out.explanations = _explain(out)
    titles = [x.title for x in new.values() if x]
    return out, "Транспорт заменён: " + "; ".join(titles) + "."


# #184: short rebuild phrases at a built plan. «без перелёта» and «на день дольше» edit the
# spec (the DAG re-plans); «без пересадок» and «отель получше» pick other offers
# of the plan («убери трансфер» is #193). A condition that already holds is named, and the plan stays.
NO_FLIGHT_RE = re.compile(
    r"\b(?:без|не)\s+(?:на\s+)?(?!долг|длинн)(?:перелет\w*|самолет\w*|авиа\w*)"
    r"|\bне\s+(?:хочу\s+|будем\s+|надо\s+)?(?:лететь|летим|полетим|летать)\b"
)
# «Продли на 2 дня» and «сократи на 2 дня» stay unclear on purpose (test_dag.py, PR #70).
_STAY_AMOUNT = rf"(?:(?P<n>{NUM}|пар[уы])\s+)?(?P<unit>ноч\w*|дн(?:я|ей)|день|сут\w*|недел\w*)"
STAY_MORE_RES = (
    re.compile(rf"\bна\s+{_STAY_AMOUNT}\s+(?:по)?(?:дольше|больше)\b"),
    re.compile(rf"\b(?:еще|добав\w*|прибав\w*)\s+(?:на\s+)?{_STAY_AMOUNT}\b"),
)
STAY_LESS_RES = (
    re.compile(rf"\bна\s+{_STAY_AMOUNT}\s+(?:по)?(?:короче|меньше)\b"),
    re.compile(rf"\b(?:убер\w*|убав\w*)\s+(?:на\s+)?{_STAY_AMOUNT}\b"),
)
NO_CHANGES_RE = re.compile(r"\bбез\s+пересад\w*|\bпрям\w+\s+(?:рейс|перел|поезд|билет)\w*")
BETTER_HOTEL_RE = re.compile(
    r"\bотел\w*\s+(?:по)?(?:лучше|круче|солиднее|приличнее)\b"
    r"|\b(?:по)?лучше\s+отел\w*|\bболее\s+(?:хорош|класс|высок|достойн)\w*\s+отел\w*"
    r"|\bотел\w*\s+(?:с\s+)?(?:рейтинг\w*|класс\w*|звезд\w*)\s+выше"
)
REBUILD_PICKS = (
    ("direct", NO_CHANGES_RE),
    ("hotel", BETTER_HOTEL_RE),
)
WAY_OF = {"train": "поездом", "car": "на машине", "flight": "самолётом"}


def pick_kinds(low: str) -> list[str]:
    """The picks a message asks for («без пересадок», «отель получше»)."""
    return [kind for kind, rx in REBUILD_PICKS if rx.search(low)]


def is_rebuild(low: str) -> bool:
    """A phrase that asks to rebuild the plan: it is never a consent (#184)."""
    return bool(pick_kinds(low) or NO_FLIGHT_RE.search(low) or stay_delta(low))


EXISTS_RE = re.compile(r"\bесть\b")
ORDER_RE = re.compile(r"\b(?:можно|хочу|хотим|давай\w*|сделай\w*)\b")


def asks_about(low: str) -> bool:
    """«Сколько стоит ещё ночь?», «а есть прямой рейс?», «отель получше — сколько стоит?» ask;
    «а можно ли без перелёта?» and «хочу отель получше, сколько это?» order (#184). A question
    word does not turn a rebuild phrase into an edit (#81, #181)."""
    clean = re.sub(r"\bможно\s+(?:ли|будет)\b", "можно", low)
    asked = QUESTION_WORD_RE.search(clean) or EXISTS_RE.search(clean)
    return bool(asked and not ORDER_RE.search(clean))


def stay_delta(low: str) -> tuple[int, str] | None:
    """Nights to add (+) or to remove (-) and the matched words: «на день дольше» is +1."""
    for sign, patterns in ((1, STAY_MORE_RES), (-1, STAY_LESS_RES)):
        for rx in patterns:
            if m := rx.search(low):
                n = m.group("n")
                count = (
                    1 if not n else 2 if n.startswith("пар") else int(n) if n.isdigit() else NUMS[n]
                )
                if m.group("unit").startswith("недел"):
                    count *= 7
                return sign * count, m.group(0)
    return None


def _legs_of(plan: Plan) -> list[TransportOffer]:
    return [o for o in plan.offers if isinstance(o, TransportOffer) and o.offer_id in plan.selected]


def _kept_date_notes(spec: TripSpec, start: date, end: date) -> list[str]:
    """The notes about the dates that survive an edit of the length: a shift stays, a guess is
    written again for the new dates."""
    out = []
    for a in spec.assumptions:
        if _label(a) != "даты":
            continue
        out.append(
            a
            if is_shift_note(a) or is_past_note(a)
            else f"даты: {start:%d.%m}–{end:%d.%m} (точных дат не было)"
        )
    return out


def rebuild_spec_edit(
    spec: TripSpec, plan: Plan, low: str, found: dict, notes: list[str], lines: list[str]
) -> list[str] | None:
    """«Без перелёта», «на день дольше», «на 3 ночи» at a built plan: write the spec edit into
    `found` and `notes` (the DAG then re-plans), or add to `lines` the reason there is none.

    The edit reads the current spec, which the extractor does not see. A condition that already
    holds («уже поездом») changes nothing; the modes the user named are returned, to be written
    into the spec without a re-plan (the offers are those of the plan), so they hold later."""
    legs = _legs_of(plan)
    ways = list(dict.fromkeys(WAY_OF[x.mode] for x in legs))
    done = f"Уже {' и '.join(ways)}: {'; '.join(x.title for x in legs)}. План не меняю."
    pinned = None
    if NO_FLIGHT_RE.search(low) and "modes" not in found:
        if legs and all(x.mode != "flight" for x in legs):
            lines.append(done)
            pinned = ["train", "car"]
        else:
            found["modes"] = ["train", "car"]
    elif set(found) == {"modes"} and legs and all(x.mode in found["modes"] for x in legs):
        pinned = found.pop("modes")  # «поездом» for a trip by train: the condition holds
        lines.append(done)
    if not (spec.date_from and spec.nights):
        return pinned
    delta = stay_delta(low)
    absolute = (
        set(found) & DATE_KEYS == {"nights"}
        and not shift_matches(low)
        and not any(is_too_long_note(n) for n in notes)
    )
    if delta is None and not absolute:
        return pinned
    if delta is not None:
        found.pop("nights", None)  # «на 2 дня дольше» is no length of 1 night
        notes[:] = [n for n in notes if _label(n) != "длительность"]
        if set(found) & DATE_KEYS:
            return pinned
        nights, said = spec.nights + delta[0], delta[1]
        if nights < 1:
            lines.append(
                f"Меньше одной ночи не получится: сейчас {spec.nights} ноч. План не меняю."
            )
            return pinned
        if nights > MAX_NIGHTS:
            lines.append(
                f"Дольше {MAX_NIGHTS} ночей не планирую: сейчас {spec.nights} ноч. План не меняю."
            )
            return pinned
    else:
        nights, said = found["nights"], ""
        if nights == spec.nights:
            del found["nights"]
            lines.append(f"Уже {nights} ноч. План не меняю.")
            return pinned
    end = spec.date_from + timedelta(days=nights)
    found.update(nights=nights, date_from=spec.date_from, date_to=end)
    if said:
        notes.append(f"длительность: {nights} ноч. («{said}», было {spec.nights})")
    notes.extend(n for n in _kept_date_notes(spec, spec.date_from, end) if n not in notes)
    return pinned


NOT_CHANGED = " План не меняю."


def _replanned(line: str) -> str:
    """A reason line in a reply where the DAG did change the plan: «План не меняю» would lie,
    and the titles of the old legs would be stale."""
    if line.startswith("Уже ") and ": " in line:
        return line.split(": ", 1)[0] + "."
    return line.removesuffix(NOT_CHANGED)


def _merged(lines: list[str]) -> list[str]:
    """«Уже поездом: X. План не меняю.» and «Уже без пересадок: X. План не меняю.» -> one line."""
    heads, rest, body = [], [], ""
    for line in lines:
        head, _, tail = line.partition(": ")
        if head.startswith("Уже ") and tail.endswith(NOT_CHANGED) and body in ("", tail):
            heads.append(head.removeprefix("Уже "))
            body = tail
        else:
            rest.append(line)
    return [*([f"Уже {' и '.join(heads)}: {body}"] if heads else []), *rest]


def _transport_changed(old: Plan, new: Plan) -> str:
    """The line that names a change of the transport the user did not ask for: the DAG ranks
    the trip again for the new dates, and the data give no other reason."""
    a, b = _legs_of(old), _legs_of(new)
    if len(a) != len(b) or [x.mode for x in a] == [x.mode for x in b]:
        return ""
    names = ["туда", "обратно"] if len(a) == 2 else [""] * len(a)
    gen = {"train": "поезда", "car": "машины", "flight": "самолёта"}
    parts = [
        f"{n} {WAY_OF[y.mode]} вместо {gen[x.mode]}".strip()
        for n, x, y in zip(names, a, b)
        if x.mode != y.mode
    ]
    return (
        f"Транспорт сменился: {', '.join(parts)}. Набор пересчитан под новые условия; "
        "чтобы оставить прежний, напишите «поездом» или «самолётом»."
    )


def _hotel_of(plan: Plan) -> HotelOffer | None:
    return next(
        (o for o in plan.offers if isinstance(o, HotelOffer) and o.offer_id in plan.selected), None
    )


def _without_changes(plan: Plan) -> tuple[Plan | None, str]:
    """«Без пересадок»: a direct leg replaces each selected leg with changes; transfers follow."""
    hotel, legs = _hotel_of(plan), _legs_of(plan)
    if hotel is None or not legs:
        return None, "Сначала выберите отель и рейсы."
    if not any(x.changes for x in legs):
        return None, f"Уже без пересадок: {'; '.join(x.title for x in legs)}. План не меняю."
    table = [o for o in plan.offers if isinstance(o, TransportOffer)]
    new = {}
    for leg in (x for x in legs if x.changes):
        direct = [
            x
            for x in table
            if (x.origin, x.destination) == (leg.origin, leg.destination)
            and x.mode != "car"  # the car goes both ways, never mixed with a leg
            and not x.changes
        ]
        if (best := _cheapest(direct)) is None:
            way = f"{leg.origin} → {leg.destination}"
            return None, f"Прямых рейсов {way} в предложениях нет, оставляю прежние. План не меняю."
        new[leg.offer_id] = best.offer_id
    by_id = {o.offer_id: o for o in plan.offers}
    kept = [i for i in plan.selected if not isinstance(by_id.get(i), TransportOffer)]
    chosen = [new.get(x.offer_id, x.offer_id) for x in legs]
    out = plan.model_copy(
        update={
            "selected": keep_refusal(
                plan, pick_hotel([*kept, *chosen], plan.offers, hotel.offer_id)
            ),
            "changes": [],
        }
    )
    out.timeline, out.issues = build_timeline(out)
    out.explanations = _explain(out)
    titles = [by_id[i].title for i in chosen]
    return out, "Без пересадок: " + "; ".join(titles) + "."


def _better_hotel(plan: Plan) -> tuple[Plan | None, str]:
    """«Отель получше»: the next hotel up by rating (then stars) among the hotels of the plan.

    The step is the smallest one: the nearest score above the current hotel, the cheaper tariff
    first. A hotel the spec refuses (pets, meal, refund) is skipped."""
    cur, spec = _hotel_of(plan), plan.spec
    if cur is None:
        return None, "Сначала выберите отель."

    def score(h: HotelOffer) -> tuple:
        return (h.stars or 0,) if cur.rating is None else (h.rating, h.stars or 0)

    better = [
        h
        for h in hotel_rows(plan.offers, plan.selected)
        if (h.title, h.city) != (cur.title, cur.city)
        and (cur.rating is None or h.rating is not None)
        and score(h) > score(cur)
        and (not spec.pets or h.pets_allowed)
        and (spec.meal is None or h.meal == spec.meal)
        and (spec.refundable is None or h.cancellation.refundable == spec.refundable)
    ]
    if not better:
        facts = ", ".join(
            [*([f"рейтинг {cur.rating}"] if cur.rating is not None else []), f"{cur.stars}★"]
        )
        return None, f"Лучше «{cur.title}» ({facts}) в предложениях нет. План не меняю."
    nxt = min(better, key=lambda h: (score(h), h.price_rub, h.offer_id))
    what = []
    if cur.rating is not None:
        what.append(f"рейтинг выше: {cur.rating} → {nxt.rating}")
    if cur.stars != nxt.stars:
        what.append(f"звёзды: {cur.stars or '—'}★ → {nxt.stars or '—'}★")
    out = plan.model_copy(
        update={
            "selected": keep_refusal(plan, pick_hotel(plan.selected, plan.offers, nxt.offer_id)),
            "changes": [],
        }
    )
    out.timeline, out.issues = build_timeline(out)
    out.explanations = _explain(out)
    note = f"Отель получше — {nxt.title}: {'; '.join(what)} (синтетика)."
    if spec.hotel_budget_rub is not None and nxt.price_rub > spec.hotel_budget_rub:
        note += f" Дороже бюджета отеля ({_rub(spec.hotel_budget_rub)})."
    if spec.budget_rub is not None and _selected_total(out) > spec.budget_rub:
        note += f" Итог выше бюджета поездки ({_rub(spec.budget_rub)})."
    return out, note


def rebuild_picks(plan: Plan, kinds: list[str]) -> tuple[Plan, list[str]]:
    """Runs the picks in order on the plan; the plan and the reply lines (the reasons too)."""
    lines = []
    for kind in kinds:
        if kind == "direct":
            new, line = _without_changes(plan)
        else:
            new, line = _better_hotel(plan)
        plan = new or plan
        lines.append(line)
    return plan, lines


def _confirmed(text: str) -> bool:
    return bool(CONFIRM_RE.search(text)) and not NEGATION_RE.search(text)


def is_question(low: str, found: dict) -> bool:
    """A question about the plan or the cart, not an edit (#81). `low` is lower case, no «ё»."""
    if (
        is_rebuild(low)
        and asks_about(low)
        and not (BOOK_RE.search(low) or STRONG_CONFIRM_RE.search(low))
        and not (SHIFT_RE.search(low) or DATE_EDIT_RE.search(low))
    ):
        return True  # «сколько стоит ещё ночь?» asks, whatever length it names (#184)
    if (
        BOOK_RE.search(low)
        or any(
            not (BARE_ASK_RE.match(low, m.start()) and not WISH_BEFORE_RE.search(low[: m.start()]))
            for m in REQUEST_RE.finditer(low)
        )
        or SHIFT_RE.search(low)
        or DATE_EDIT_RE.search(low)
        or OTHER_RE.search(low)
        or OTHER_VARIANTS_RE.search(low)
        or EDIT_KEYS & set(found)
        or is_rebuild(low)  # «а можно без перелёта?», «на день дольше» ask for a change (#184)
    ):
        return False
    if QUESTION_WORD_RE.search(low):
        return not STRONG_CONFIRM_RE.search(low)
    return "?" in low and not CONFIRM_RE.search(low)


FACT_TOPICS = (
    (re.compile(r"отмен|возврат|вернут|вернет"), "cancel"),
    (re.compile(r"трансфер"), "transfer"),
    (re.compile(r"\bдет|реб[её]н"), "kids"),
    (re.compile(r"задерж|опозда"), "delay"),
)


def _question_fact(old: ChatReply, low: str) -> str:
    """One line with the fact the question asks about. It comes from the plan or the spec only
    (TRAP-4); no data in the plan means we say so. Each price is marked as synthetic (TRAP-7)."""
    plan = old.plan
    chosen = [o for o in plan.offers if o.offer_id in plan.selected]
    topic = next((t for r, t in FACT_TOPICS if r.search(low)), "")
    if topic == "delay":
        return (
            "Данных о задержках рейсов нет, прогноз не даю: время в плане — по расписанию "
            "каталога (синтетика)."
        )
    if topic == "cancel":
        hotel = next((o for o in chosen if o.kind == "hotel"), None)
        if hotel is None:
            return "Отель в плане не выбран, условий отмены назвать не могу."
        c = hotel.cancellation
        if not c.refundable:
            return f"{hotel.title}: тариф невозвратный, бесплатной отмены нет."
        until = (
            f" до {c.free_until.astimezone(ZoneInfo(hotel.tz)):%d.%m %H:%M}" if c.free_until else ""
        )
        return f"{hotel.title}: бесплатная отмена{until}."
    if topic == "transfer":
        rows = [
            f"{o.title} — {_rub(o.price_rub)} (синтетика), в пути {span(o)}"
            for o in chosen
            if o.kind == "transfer"
        ]
        return "Трансфер в плане: " + "; ".join(rows) + "." if rows else "Трансфера в плане нет."
    if topic == "kids":
        ages = plan.spec.children_ages
        if not ages:
            return "Детей в составе нет. Чтобы добавить, напишите возраст: «с ребёнком 5 лет»."
        return (
            f"В составе: {plan.spec.adults} взр. + дети {', '.join(map(str, ages))} лет. "
            "Отдельной строки «дети бесплатно» в данных нет."
        )
    return ""


def _question_answer(old: ChatReply, low: str = "", message: str = "") -> str:
    """The reply to a question at assemble or checkout: the facts of the plan or the cart only
    (TRAP-4), the stage and the cart stay. Weather, visas and «почему не <отель>» get the fact
    and the hint of the stage, not a repeat of the plan (#186)."""
    head = "Это вопрос: план и корзину не меняю."
    if old.stage == "compare":
        head = "Это вопрос: план не меняю."
    if fact := plan_fact(old.plan, low, message):
        return f"{head}\n{fact}\n{HINTS[old.stage]}"
    if fact := _question_fact(old, low):
        head += "\n" + fact
    if old.stage == "compare":
        return f"{head}\n{HINTS['compare']}"
    if old.stage == "assemble":
        return f"{head}\n{_text_assemble(old.plan)}"
    rows = [f"{o.title} — {_rub(o.price_rub)} (синтетика)" for o in old.cart.items]
    cart = (
        "В корзине:\n" + "\n".join(rows) + f"\nИтого {_rub(old.cart.total_rub)} (синтетика)."
        if rows
        else "Корзина пуста."
    )
    if old.plan.timeline:  # times come from the timeline tool, never from the model
        cart += "\nПлан поездки:\n" + "\n".join(
            f"{e.at:%d.%m %H:%M} — {e.title}" for e in old.plan.timeline
        )
    edit = "Чтобы изменить поездку, напишите, что поменять: например, «хотим с бассейном»."
    return f"{head}\n{cart}\n{edit} {HINTS['checkout']}"


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


PLAN_STAGES = ("inspire", "select", "compare", "assemble", "checkout")
PLAN3 = ("compare", "assemble", "checkout")  # a plan with offers exists


def _text_kids_question(old: ChatReply) -> str:
    """The answer to «А дети бесплатно?» (#179): the party stays, the data has no free places."""
    spec = old.plan.spec
    kids = f" + дети {', '.join(map(str, spec.children_ages))} лет." if spec.children_ages else ""
    return (
        f"Состав не меняю: {spec.adults} взр.{kids or ''} "
        "Отдельной строки «дети бесплатно» в данных нет: "
        "в расчёте цена ребёнка зависит от его возраста (синтетика). "
        "Чтобы добавить ребёнка, напишите возраст: «с ребёнком 5 лет». " + HINTS.get(old.stage, "")
    ).strip()


def drop_transfers(old: ChatReply, plan: Plan | None = None) -> ChatReply:
    """#193: «давайте без трансфера» at assemble or checkout. The transfers leave the plan (at
    checkout also the cart); the timeline is built again (TRAP-5). The stage stays: a plain
    consent fills the cart. The model does not read the message; the prices stay from the plan."""
    plan = plan or old.plan
    gone = [o for o in plan.offers if o.kind == "transfer" and o.offer_id in plan.selected]
    if not gone:
        text = f"Трансфера в плане нет, менять нечего. {HINTS[old.stage]}"
        return old.model_copy(update={"plan": plan, "reply": text, "questions": [], "llm": False})
    cart, ids = old.cart, {o.offer_id for o in gone}
    if old.stage == "checkout":
        for o in gone:
            cart = edit_cart(plan, cart, "remove", o.offer_id)
        plan = sync_plan(plan, old.cart, cart)
    else:
        plan = plan.model_copy(update={"selected": [i for i in plan.selected if i not in ids]})
        plan.timeline, plan.issues = build_timeline(plan)
    rows = "; ".join(f"{o.title} — {_rub(o.price_rub)} (синтетика)" for o in gone)
    head = f"Убираю трансфер из плана: {rows}."
    if old.stage == "assemble":
        text = f"{head} В корзину пока ничего не кладу.\n{_text_assemble(plan)}"
    else:
        text = f"{head} В корзине теперь {_rub(cart.total_rub)} (синтетика). {HINTS['checkout']}"
    return old.model_copy(
        update={"plan": plan, "cart": cart, "reply": text, "questions": [], "llm": False}
    )


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
    # #179: «А дети бесплатно?» at a built plan is a question, not a party: nobody is added.
    asking = old.stage in PLAN_STAGES and is_child_question(message)
    rules_found, rules_notes = extract(message, bool(old.plan.spec.destinations), asking=asking)
    # #193: «без трансфера» is an edit even with «давай» in it; it never reaches the cart as a consent.
    no_transfer = (
        prev
        and old.stage in ("assemble", "checkout")
        and NO_TRANSFER_RE.search(low)
        and not (HYPOTHESIS_RE.search(low) and not CHOICE_RE.search(low))
    )
    if (
        no_transfer
        and set(rules_found) <= {"wishes"}
        and not SHIFT_RE.search(low)
        and (
            ASKS_RE.search(low)
            or QUESTION_WORD_RE.search(low)
            or (
                "?" in low
                and (ASKS_COMPARE_RE.search(low) or re.search(r"\bне\s+(?:нуж|надо)", low))
            )
        )
    ):
        # #193: a question about the transfer is answered from the plan and changes nothing.
        return old.model_copy(
            update={"reply": _question_answer(old, low), "questions": [], "llm": False}
        )
    if no_transfer and not rules_found:
        return drop_transfers(old)
    # #186, #216: «какая погода в декабре?» at a December plan names no new field: still a
    # question. A question with a fact to give («погода?», «почему не Бульвар?», «почему не
    # поезд?») goes to no model, changes nothing, and is no transport pick.
    fact = plan_fact(old.plan, low, message) if prev and old.stage in PLAN3 else ""
    asked = without_plan_fields(rules_found, old.plan) if fact else rules_found
    if (
        fact
        and ASKING_NOTE not in rules_notes
        and (
            is_question(low, asked)
            or (is_hotel_compare(old.plan, low) and not EDIT_KEYS & set(asked))
        )
    ):
        return old.model_copy(
            update={"reply": _question_answer(old, low, message), "questions": [], "llm": False}
        )
    pure = (
        set(rules_found) <= {"modes"}
        and not SHIFT_RE.search(low)
        and not (HYPOTHESIS_RE.search(low) and not CHOICE_RE.search(low))
    )
    if pure and prev and old.stage in ("compare", "assemble", "checkout"):
        plan, note = switch_transport(old.plan, message)
        if no_transfer and old.stage == "assemble" and plan:  # «давай поездом, без трансфера»
            done = drop_transfers(old, plan)
            return done.model_copy(update={"reply": f"{note}\n{done.reply}"})
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
    if (
        prev
        and old.stage == "compare"
        and ASKING_NOTE not in rules_notes  # «а дети бесплатно?» has its own answer (#179)
        and is_question(low, asked)
        and _question_fact(old, low)
    ):
        # #186: a question at compare with a fact to give («сколько стоит трансфер?») goes to
        # no model and changes nothing; other questions go on as before.
        return old.model_copy(
            update={"reply": _question_answer(old, low, message), "questions": [], "llm": False}
        )
    if prev and old.stage in ("assemble", "checkout") and is_question(low, asked):
        # #81: «сколько ехать до моря?» is no wish: no model reads it, the DAG does not run.
        kids_only = asking and ASKING_NOTE in rules_notes and not rules_found
        text = _text_kids_question(old) if kids_only else _question_answer(old, low, message)
        return old.model_copy(update={"reply": text, "questions": [], "llm": False})
    # A month after dates the user named (#190): a clear request moves them below (merge);
    # a refusal or a doubt moves nothing and is never a consent («давай в январе?»).
    month_verdict, target, now = "", None, old.plan.spec
    if (
        prev is not None
        and "month" in rules_found
        and not {"date_from", "date_to"} & rules_found.keys()
        and dates_named(now)
    ):
        month_verdict, target = month_call(message, rules_found["month"])
        if month_verdict == "move":
            if target == now.date_to.month != now.date_from.month:
                month_verdict = "ask"  # the stay already reaches into that month
            elif target == now.date_from.month:
                month_verdict = ""  # the dates are in that month: nothing to move
    # What the user is told about the dates; the other fields of the message are applied (#197).
    month_target, month_note = target, None
    if month_verdict in ("ask", "stay"):
        month_target = target if month_verdict == "ask" else now.date_from.month
        month_note = month_reply(now, month_target, "")
    refused_month = month_verdict in ("ask", "stay", "skip")
    idx = _ordinal(message)
    deadline = llm.deadline()
    # A service turn («беру», «да», «подтверждаю», a number) goes to no model: a field it
    # adds would re-plan the trip and send the user back to compare. Free text on assemble or
    # checkout («хотим с бассейном») is a wish: the model reads it and the DAG re-plans (#49).
    # The stage decides: «оформи мне отдых на Алтае» on the first turn is a request, not consent.
    # A new session, clarify and select are never service turns.
    consent = (
        old.stage in ("compare", "assemble", "checkout")
        and not refused_month
        and (_confirmed(t) or bool(BOOK_RE.search(t)))
    )
    pick = old.stage in ("inspire", "compare") and idx is not None
    found, notes = extract(
        message,
        bool(old.plan.spec.destinations),
        old.plan.spec.nights or DEFAULT_NIGHTS,
        old.plan.spec.adults,
        asking,
    )
    if asking and ASKING_NOTE in notes and not found:
        return old.model_copy(update={"reply": _text_kids_question(old), "llm": False})
    if refused_month:
        found.pop("month", None)  # «не в январе», «отпуск до января»: no new month (#190)
        notes[:] = [n for n in notes if _label(n) != "когда"]
    # #184: «без перелёта», «на день дольше», «отель получше» at a built plan are edits, never
    # a consent. The spec edits go into `found`; the picks run on the plan after the DAG.
    # A question («сколько стоит ещё ночь?») is none of this: the question rules answer it.
    planned = (
        prev is not None and old.stage in PLAN3 and bool(old.plan.offers) and not asks_about(low)
    )
    rebuild = planned and is_rebuild(low)
    picks = pick_kinds(low) if planned else []
    lines: list[str] = []  # reasons there is nothing to change («уже поездом»)
    pinned = (
        rebuild_spec_edit(old.plan.spec, old.plan, low, found, notes, lines) if planned else None
    )
    # «давай на 400 ночей» asks for a length: no consent, and the model reads the message (#168)
    asks_length = any(is_too_long_note(n) for n in notes)
    # «давай на месяц позже» moves the dates: no consent, and the model reads the message (#176).
    # «беру, на месяц вперёд спасибо» is a consent: shift_matches skips a remark after a booking.
    shifts = bool(shift_matches(t.replace("ё", "е"), prev is not None))
    yes = _confirmed(t) and not asks_length and not shifts and not rebuild and not refused_month
    service = prev is not None and (
        pick or (consent and not asks_length and not shifts and not rebuild)
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
        if service
        or other
        or not use_llm
        or is_chip_message(message)  # chips: S9, #76
        or (refused_month and old.stage == "checkout")  # the cart stays (#197)
        else llm.extract_spec(message, old.plan.spec, deadline)
    )
    if llm_found and ASKING_NOTE in notes:
        llm_found.pop("children_ages", None)  # a question names no child (#179)
    if refused_month:
        for k in DATE_KEYS:  # the model may not bring the month back (#190)
            (llm_found or {}).pop(k, None)
    if shifts:
        if (
            found.pop("nights", None) is not None
        ):  # «на неделю позже» moves the dates, the length stays
            # the length note of that reading goes with it; a length refused stays named (#160)
            notes[:] = [n for n in notes if _label(n) != "длительность" or is_too_long_note(n)]
        if llm_found:
            # The rules read the shift (which end, how far); the model must not add other dates.
            for k in ("nights", *(DATE_KEYS if "date_from" not in found else ())):
                llm_found.pop(k, None)
    # Dates only the model read pass the check of the rules' dates (#164).
    past = llm_past_dates(llm_found) if llm_found and not DATE_KEYS & found.keys() else None
    if past:
        notes.append(past)
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
    used = bool(past)  # the LLM output changed this reply; a note about its dates is a change
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

    before = old.plan.spec

    def with_month(spec: TripSpec, text: str, questions: list[Question]):
        """The dates stay: the line is built from the final spec, not from the old dates. The
        general question is only for a message that changed nothing; here the dates stay."""
        if not (month_note and spec.date_to):
            return text, questions
        if (
            month_verdict == "ask"
            and month_target == spec.date_from.month != before.date_from.month
        ):
            # «может в январе? на месяц позже»: the shift put the dates there already (#198)
            return f"{text}\nДаты сдвинуты по вашей просьбе: {period(spec)}.", questions
        line, chips = month_reply(spec, month_target or spec.date_from.month, "")
        return f"{text}\n{line}", [*chips, *questions]

    def reply(stage: Stage, plan: Plan, text: str, cart: Cart = old.cart, **kw) -> ChatReply:
        text, kw["questions"] = with_month(plan.spec, text, kw.get("questions", []))
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

    if month_note and not (dirty_nodes(old.plan.spec, spec) or shifts or picks or lines):
        # Nothing else changed: the answer is about the dates only.
        text, questions = month_note
        if month_verdict == "stay":
            text = f"{text} {HINTS.get(old.stage, '')}".strip()
        plan = old.plan.model_copy(update={"changes": []})
        return old.model_copy(
            update={"plan": plan, "reply": text, "questions": questions, "llm": False}
        )
    dated = bool(old.plan.spec.date_from and old.plan.spec.date_to)
    te = t.replace("ё", "е")
    date_edit = DATE_EDIT_RE.search(te)
    if not found and prev and dated and not SHIFT_RE.search(te) and date_edit and not lines:
        # The message asks to move the dates but says how far or which way in no known words (S2).
        s = old.plan.spec
        text = (
            f"Не понял, что изменить в датах. Сейчас {period(s)}. "
            "Напишите, например: «на два дня позже», «на месяц позже», «обратно на день раньше» "
            "или «с 20 ноября на 5 ночей»."
        )
        return old.model_copy(update={"reply": text, "questions": [], "llm": False})
    if "date_from" not in found:
        try:
            # «на два дня позже» moves the current dates (S2)
            shifted = shift_dates(spec, message, prev is not None)
            refused = rules_found.get("month")
            moved = (shifted.date_from, shifted.date_to) != (spec.date_from, spec.date_to)
            if (
                month_verdict == "stay"
                and moved
                and refused not in (spec.date_from.month, spec.date_to.month)
                and refused in (shifted.date_from.month, shifted.date_to.month)
            ):
                # The user refused that month: the shift into it is not applied (#198)
                lines.append(
                    f"Сдвиг не применён: он переносит даты на {period(shifted)}, "
                    f"а {MONTH_NAMES[refused - 1]} вы отвергли."
                )
            else:
                spec = shifted
        except ValueError as e:
            text = (
                f"{e} Из этого сообщения ничего не применено. "
                f"Сейчас {period(spec)}. {HINTS.get(old.stage, '')}"
            )
            return old.model_copy(update={"reply": text, "questions": [], "llm": False})
    # «Было → стало» belongs to the reply of the re-plan only, not to the next steps.
    plan = old.plan.model_copy(update={"spec": spec, "changes": []})
    plan_dirty = bool(dirty_nodes(old.plan.spec, spec) or not plan.offers)
    if pinned and spec.modes != pinned:
        # «поездом» on a trip by train: the offers are right, only the constraint is kept (#184)
        spec = spec.model_copy(update={"modes": pinned})
        plan = plan.model_copy(update={"spec": spec})
    if plan_dirty:
        # The DAG recomputes only the nodes that depend on the changed fields (T6, #10).
        plan, cart, done = replan(old.plan, spec, old.cart)
        if not plan.offers:
            # TRAP-2 (#88): neither catalog.json nor the geocoder knows the place (or no network).
            # Its coordinates are unknown, so the variants are not «nearby»: the reply says why.
            text = (
                f"Направления «{spec.destinations[0]}» пока {NOT_IN_CATALOG}, "
                "и геокодер его не нашёл (или нет сети). Где это место, не знаю. "
                f"{HINTS['select']} Подойдёт и ближайший к нему крупный город. "
                "Ниже — направления под ваши даты и пожелания, не по близости к этому месту."
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
        if picks:  # «и отель получше»: the picks run on the re-planned trip (#184)
            plan, more = rebuild_picks(plan, picks)
            lines = [*lines, *more]
            plan.changes = plan_changes(old.plan, plan, old.cart, cart)
        lines = [_replanned(x) for x in lines]
        same_trip = (old.plan.spec.destinations, old.plan.spec.origin, old.plan.spec.modes) == (
            spec.destinations,
            spec.origin,
            spec.modes,
        )
        if same_trip and (line := _transport_changed(old.plan, plan)):
            lines.append(line)
        text = _text_compare(plan.model_copy(update={"changes": []}))
        if spec.origin in spec.destinations:
            text += f" Город вылета {spec.origin} совпадает с направлением: перевозки в плане нет."
        if plan.changes:
            text = "\n".join(["Было → стало:", *describe(plan.changes), text])
        text = "\n".join([*lines, text])
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

    if (picks or (lines and not yes)) and not plan_dirty:
        # Nothing to re-plan: a pick on the plan, or the reason there is no change (#184).
        if picks:
            new, more = rebuild_picks(plan, picks)
            lines = [*lines, *more]
        else:
            new = plan
        if new is not plan and old.stage == "checkout":
            note = "Корзина уже собрана, поэтому в ней ничего не меняю. Позицию убирает кнопка «Убрать» в корзине."
            text, questions = with_month(spec, f"{note} {HINTS['checkout']}", [])
            return old.model_copy(update={"reply": text, "questions": questions, "llm": False})
        if new is plan:
            text, questions = with_month(spec, " ".join([*_merged(lines), HINTS[old.stage]]), [])
            return old.model_copy(
                update={"plan": plan, "reply": text, "questions": questions, "llm": False}
            )
        new.changes = plan_changes(old.plan, new, old.cart, old.cart)
        body = (
            _text_assemble(new)
            if old.stage == "assemble"
            else _text_compare(new.model_copy(update={"changes": []}))
        )
        text = "\n".join([*_merged(lines), "Было → стало:", *describe(new.changes), body])
        text, questions = with_month(spec, text, [])
        return old.model_copy(
            update={"plan": new, "reply": text, "questions": questions, "llm": False}
        )

    if old.stage == "compare" and (idx is not None or yes):
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
    if old.stage == "assemble" and yes:
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
