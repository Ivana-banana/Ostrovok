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

import difflib
import logging
import re
import time
from calendar import monthrange
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from api import catalog, llm, ops
from api.answers import (
    closed_line,
    consent_fact,
    destination_line,
    is_hotel_compare,
    plan_fact,
    price_mark,
    rooms_line,
    span,
    without_plan_fields,
)
from api.cart import (
    CartRefusal,
    checkout_reply,
    edit_cart,
    fill_cart,
    keep_refusal,
    remember_refusal,
    sync_plan,
)
from api.contracts import (
    Cart,
    ChatReply,
    ChatRequest,
    HotelOffer,
    Plan,
    Question,
    Stage,
    TransportOffer,
    TripSpec,
)
from api.dag import (
    DATE_EDIT_RE,
    MEALS,
    SHIFT_RE,
    describe,
    dirty_nodes,
    is_shift_note,
    own_tickets,
    period,
    replan,
    shift_dates,
    shift_matches,
)
from api.dag import changes as plan_changes
from api.extract import (
    ASKING_NOTE,
    CAPITAL_PLACES,
    DATE_KEYS,
    DEFAULT_NIGHTS,
    EACH_STAY_RE,
    HOTEL_NOTE,
    MAX_NIGHTS,
    MODES,
    MONTH,
    MONTH_NAMES,
    MONTHS,
    NO_AGE_NOTE,
    NUM,
    NUMS,
    PAST_WINDOW,
    PER_NIGHT_NOTE_RE,
    PLACE_RES,
    SEASONS,
    _known,
    _night_word,
    asks_booking,
    asks_stay,
    children_uncounted,
    excluded_places,
    extract,
    is_child_question,
    is_chip_message,
    is_excluded,
    is_past_note,
    is_too_long_note,
    is_unchecked,
    llm_past_dates,
    past_year_note,
    too_long_note,
    with_llm,
)
from api.fixtures import CATALOG, build_timeline, future_date, rank
from api.inspire import (
    ROAD_LINE,
    _level,
    _month,
    _rule_breaks,
    card,
    suggest_destinations,
    unchecked_wishes,
)
from api.offers import SyntheticTransport, _complete, find_offers, resolve_place, road_nights
from api.optimizer import (
    bundles,
    dead_end_text,
    enumerate_trips,
    explain,
    hotel_rows,
    pets_ok,
    pets_possible,
    pick_hotel,
)
from api.store import SessionStore
from api.timeline import NO_ROAD

log = logging.getLogger(__name__)
NOT_IN_CATALOG = "нет в каталоге"  # shared with the simulator in checks.py (#149)
NO_PET_HOTEL = (
    "Внимание: у отеля нет отметки о приёме животных — уточните у отеля до брони."  # #556
)
# #575: a plane takes the pet on the terms of the airline; the reply names them, no fee (TRAP-4)
NO_PET_FLIGHT = (
    "Внимание: питомец в самолёте — не отказ, а условие авиакомпании, которое не подтверждено: "
    "вес, переноска и доплата. Уточните у авиакомпании до оплаты."
)
NO_PET_MARK = "Нет отметки о животных:"  # the count line of the compare reply (#475)
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
    # #98: «давай заново» resets at the end of a phrase or before a mark, like «сбрось»:
    # «давай заново посчитаем цену» edits the plan. A bare «заново» is a reset too.
    r"|\b(?:давай(?:те)?|хочу|попробу\w+|поехали)\s+(?:вс[её]\s+)?"
    r"(?:заново|сначала|с\s+нуля|по\s+нов(?:ой|ому))(?=\s*(?:[.!,;:]|$))"
    r"|\bвс[её]\s+заново\b"
    r"|\bсбрось(?:те)?(?:\s+(?:вс[её]|план|поиск|поездку|диалог))?\s*(?:[.!,;]|$)"
    r"|^\s*(?:заново|по\s+нов(?:ой|ому)|с\s+нуля)\s*[.!]*\s*$"
    # «новая поездка» as a command; «сколько стоит новая поездка» and «это не новая поездка» ask
    r"|(?:^\s*|[.!,;:]\s*|\b(?:давай(?:те)?|хочу|начн\w+|начать|начинаем)\s+)"
    r"нов(?:ая|ую)\s+поездк\w*(?=\s*(?:[.!,;:]|$))"
    r"|\bнов(?:ый|ую)\s+(?:поиск|запрос)(?=\s*(?:[.!,;:?]|$))",
    re.IGNORECASE,
)
# A negation before the phrase («не хочу с нуля») or after it («с нуля не надо»).
NOT_RESTART_RE = re.compile(
    r"\bне\s+(?:\w+\s+)?(?:начин\w+|начат\w*|сбрас\w+|с\s+нуля|заново|сначала)"
    r"|\b(?:с\s+нуля|заново|сначала|поиск\w*|запрос\w*|поездк\w*)\s+(?:\w+\s+)?"
    r"не\s+(?:надо|нужн\w*|хочу|будем)"
)
RESTART_CHIP = "Начать заново"
# #503: after the voucher «Изменить бронь» opens a new trip with the same terms and the edit the
# user wrote before the press. The edit lives in the text of the chip question: the reply is the
# only state of the session, and the user sees what will be applied.
CHANGE_CHIP = "Изменить бронь"
CHANGE_RE = re.compile(rf"^\s*{CHANGE_CHIP}\s*[.!]*\s*$", re.IGNORECASE)
EDIT_NOTE = "Правка для новой поездки: «"
EDIT_NOTE_RE = re.compile(rf"{EDIT_NOTE}(.*)»\.$", re.DOTALL)
AFTER_BOOKING = (
    "Другое направление, другие даты или состав — это новая поездка. Нажмите "
    f"«{CHANGE_CHIP}»: возьму те же условия и вашу правку. Или нажмите «{RESTART_CHIP}» "
    "или напишите «новая поездка»: начнём с нуля."
)
OTHER_RE = re.compile(
    r"\b(?:друг(?:ое|ой|ую|ие|их)|ино[ейю])\s+(?:направлен\w*|город\w*|мест\w*|стран\w*|курорт\w*)"
    r"|\bне\s+(?:хочу|нравится|подходит)\s+(?:это|этот|эта)\s+(?:направлен|город|мест)"
)
OTHER_VARIANTS_RE = re.compile(r"\bдруг(?:ие|их|ой)\s+вариант\w*")
# #451: «ещё», «а ещё?», «покажи другие варианты» at inspire: the next places of the same pool.
# The whole message only (read with ё -> е): «и ещё ребёнок 5 лет» is an edit, not this.
MORE_RE = re.compile(
    r"\s*(?:(?:а|и|ну|давай(?:те)?)\s+)?(?:(?:покажи(?:те)?|предложи(?:те)?|есть|что)\s+)?"
    r"(?:еще|други[ех])(?:\s+(?:что-?нибудь|вариант\w*|направлен\w*|мест\w*|город\w*|есть))?"
    r"\s*[?!.…]*\s*$"
)
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
# C12 (#366): «возьми рейс в 06:15» picks the leg by its local departure time. The time stands
# right after the noun: «давай поезд чтобы прибыть к 18:00» names no departure.
LEG_TIME_RE = re.compile(
    r"\b(рейс\w*|перел[её]т\w*|поезд\w*|вылет\w*|самол[её]т\w*)\s+"
    r"(?:(?:с\s+)?(?:вылет\w*|отправлени\w*)\s+)?(?:в|на)\s+([01]?\d|2[0-3])[:.]([0-5]\d)\b"
)
# #316: the mark in the note of a budget the agent set after «подешевле».
AGENT_BUDGET = "задан агентом"
# A new date, length or party ends that budget: it was fitted to the old trip.
BUDGET_RESET = ("date_from", "date_to", "nights", "adults", "children_ages")
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
# A link word makes a route when a later city of the message stands in its clause:
# «потом в Казань», «потом поедем в Казань»; not «потом решим», «потом на море».
LINK_RE = re.compile(
    r"\b(?:потом|затем|далее|оттуда|следом|а\s+после(?:\s+(?:этого|того|чего))?"
    r"|после\s+(?:этого|того|чего))\b"
    r"(?:[\s,]*(?:может быть|может|наверное|скорее всего|возможно|вероятно|пожалуй)(?=[\s,])[\s,]*)?"
    r"([^,.;!?\n]*)"
)
ROUTE_RE = re.compile(
    r"\s[—–]\s(?!(?:что|как\S*|где|куда|стоит|лучше|выгодн\S*|чем|какой)\b)|→|->"  # #141: «— что лучше?»
)
DEFAULT_ORIGIN = "Москва"  # the departure city when none is named
# #351: an unnamed origin is asked when the fit of a whole-trip budget depends on it: the round
# trip from DEFAULT_ORIGIN costs ROAD_GAP % of the budget or more over the cheapest big hub, or
# takes ROAD_SHARE % of it or more alone. Sums from the offers (TRAP-4). Team estimates (README).
ROAD_GAP = 15  # % of the budget, integer: 15.0 % exactly asks (#380)
ROAD_SHARE = 50
BIG_HUBS = 10  # the hubs by population compared with DEFAULT_ORIGIN
# #380: a hub nearer the place is its neighbour (Ростов-на-Дону — Сочи 403 км, Анапа 318 км):
# its cheap road measures the distance to the place, not the traveller's origin. F15: 1 434 км.
NEIGHBOUR_HUB_KM = 500
# #436: an unnamed origin is the first question, not a silent DEFAULT_ORIGIN. The chips say «Из …»:
# the rules read them as the origin, never as a destination.
ORIGIN_ASK = "Откуда вы поедете?"
ORIGIN_OPTIONS = ("Москвы", "Санкт-Петербурга", "Екатеринбурга", "Новосибирска", "Казани")
ORIGIN_FUZZY = 0.8  # difflib ratio of a hub name: «Масква» -> Москва 0.83 (TRAP-3)
STAY_ASK = "В каком городе нужна гостиница?"
BOOKING_ASK = "Куда хотите поехать? Напишите город."
MAX_CANDIDATES = 3  # the first is planned, the 2nd and the 3rd get a chip
STAY_RE = re.compile(
    r"\b(?:\d+|од(?:ин|на|ну)|дв(?:а|е|ое)|тр(?:и|ое)|четыре|пять|шесть|семь|восемь|девять|десять)"
    r"\s*(?:ноч|дн|сут)"
)
AHEAD_RE = re.compile(r"\bчерез\s+\S+\s*(?:ноч|дн|сут)")  # «вылет через 3 дня»: no stay length
OR_RE = re.compile(r"\b(?:или|либо)\b")  # cities joined by «или», «либо» are candidates
# The adults are not named: 2 by default. With a child this becomes a question (#112).
PARTY_UNKNOWN = "взрослых: 2 (состав не назван)"
KIDS_UNKNOWN = "возраст ребёнка: «с детьми» — число и возраст не названы"  # #182: asked, not 7
# #113: the refused places live in this assumption (TripSpec has no field for them).
EXCLUDED = "исключено"
# #451: the places of the earlier «ещё» batches; a turn without «ещё» drops the note.
SHOWN = "уже показаны"
# #452: the country the user named on «Откуда?»; its places are shown until a place is named.
COUNTRY = "страна"


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


# #167: «бюджет: 3 000 ₽ × 2 взрослых × 4 ночи»: a budget «в сутки» keeps its factors in the note.
PER_NIGHT_BUDGET_RE = re.compile(
    r"^бюджет: (\d[\d ]*) ₽ ×(?: (\d+) взросл\w*(?: \(дети не считаются\))? ×)? (\d+) ноч\w*"
)


def resolve_trip_budget(data: dict) -> None:
    """The trip budget stated «в сутки» is a total of the nights the plan has now (#167)."""
    for i, a in enumerate(data["assumptions"]):
        m = PER_NIGHT_BUDGET_RE.match(a)
        if not m or not data["nights"] or data["nights"] == int(m.group(3)):
            continue
        day, people, n = int(m.group(1).replace(" ", "")), int(m.group(2) or 1), data["nights"]
        data["budget_rub"] = day * people * n
        party = f" {people} взрослых ×" if m.group(2) else ""
        data["assumptions"][i] = f"бюджет: {_rub(day)} ×{party} {n} {_night_word(n)}"


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
        r"|\bли\b"  # «а не поедем ли в феврале» (#220)
    )
    if "?" in t or re.search(unclear, t):
        return "ask", month
    clause = re.split(r"[.;!?]", rest)[0]
    # Without a comma a «не» after the month is a refusal («в январе не поедем»). After a comma,
    # «но», «и» or «а» it may belong to a neighbour («в январе, не дорого»): only a verb of
    # refusing makes it one (#198).
    # A dash before «не» + a neighbour wish ends the part too: «в январе — не дорого» (#220).
    # Any other «— не X» stays a refusal: «январь — не пойдёт», «в январе — не»
    neighbour = r"(?=не\s+(?:очень\s+)?(?:дорог|дешев|жарк|шумн|далек)\w*)"
    parts = re.split(rf",|\s[—–-]\s+{neighbour}|\s(?:но|и|а)\s", clause, maxsplit=1)
    near, after = parts[0], parts[1] if len(parts) > 1 else ""
    strong = r"нельзя|отпадает|отпадают|исключен|невозможн"  # a refusal anywhere near the month
    weak = r"отменя|неудобн"  # a refusal only as the whole part (#215)
    verbs = (
        r"подход|получ|выйд|выход|смож|мож|годит|хоч|хот|надо\b|нуж|вариант|устраива|катит"
        r"|рассматр|будет|удобн"
    )
    # «не поедем» ends the part: «не поедем на самолёте» only drops a way to travel
    moves = r"(?:по)?(?:ед|езж|лет(?!ом\b|о\b|н))\w*"  # not «летом», «лето», «летний»
    end = r"(?=\s*(?:,|;|$))"
    # «январь нет», «январь — нет», «январь мимо», «в январе никак»: a bare no ends the part (#215)
    bare = rf"(?:нет|никак|мимо){end}"
    # The weak words follow the month directly or after a filler: «январь или никак» is a move
    filler = r"(?:(?:—|–|-|точно|уже|вообще|совсем|тоже|нам)\s+)*"
    gone = rf"\s*(?:(?:\S+\s+){{0,2}}?(?:не\b|{strong})|{filler}(?:{weak}|{bare}))"
    # After a comma «не» + a verb and the strong words may follow up to two words («увы, не
    # получится», «увы, нельзя»). A «нет» or «неудобно» deeper in the part belongs to a
    # neighbour: «детей нет», «в декабре никак».
    refusing = (
        rf"\s*(?:(?:\S+\s+){{0,2}}?(?:не\s+(?:очень\s+)?(?:{verbs})"
        rf"|не\s+{moves}{end}|не{end}|{strong})|(?:{weak})\w*{end}|{bare})"
    )
    if negated or re.match(gone, near) or re.match(refusing, after):
        return "stay", month
    return "move", month


def month_other(text: str, month: int) -> int | None:
    """The second month of a doubt («в январе, а не поедем ли в феврале»), to ask about (#220)."""
    low = text.lower().replace("ё", "е")
    if not re.search(r"\bли\b", low):  # a doubt «…ли…» only; «не в декабре, лучше в январе» is not
        return None
    names = re.findall(rf"\b{MONTH}", low)
    others = [n for n in map(_month_number, names) if n != month]
    return others[0] if others else None


def month_reply(
    spec: TripSpec, target: int | None, hint: str, other: int | None = None
) -> tuple[str, list[Question]]:
    """The reply to a month that moves nothing: the dates stay, or a question about the move."""
    own = f"Даты {period(spec)} вы назвали сами."
    if target is not None and other is not None:  # two months in a doubt: ask which one
        moves = [f"Перенести на {MONTH_NAMES[n - 1]}" for n in (target, other)]
        text = f"{own} {MONTH_NAMES[target - 1].capitalize()} или {MONTH_NAMES[other - 1]}?"
        return text, [Question(text="Что сделать с датами?", options=moves)]
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
    if "budget_rub" not in found:  # a budget stated now is not recounted
        resolve_trip_budget(data)
    return TripSpec.model_validate(data)


def refused_before(spec: TripSpec) -> list[str]:
    """The places the user refused in the earlier messages."""
    return [
        p
        for a in spec.assumptions
        if _label(a) == EXCLUDED
        for p in a.split(": ", 1)[1].split(", ")
    ]


def shown_before(spec: TripSpec) -> list[str]:
    """#451: the places shown in the earlier batches of «ещё»."""
    return [
        p for a in spec.assumptions if _label(a) == SHOWN for p in a.split(": ", 1)[1].split(", ")
    ]


def country_before(spec: TripSpec) -> str | None:
    """#452: the country kept from an earlier message while the trip has no place."""
    if spec.destinations:
        return None
    return next((a.split(": ", 1)[1] for a in spec.assumptions if _label(a) == COUNTRY), None)


def with_country(spec: TripSpec, country: str | None) -> TripSpec:
    kept = [a for a in spec.assumptions if _label(a) != COUNTRY]
    note = [f"{COUNTRY}: {country}"] if country and not spec.destinations else []
    return spec.model_copy(update={"assumptions": kept + note})


def with_shown(spec: TripSpec, shown: list[str]) -> TripSpec:
    kept = [a for a in spec.assumptions if _label(a) != SHOWN]
    note = [f"{SHOWN}: {', '.join(shown)}"] if shown else []
    return spec.model_copy(update={"assumptions": kept + note})


def relax_hint(spec: TripSpec) -> str:
    """#451: the conditions the user may relax when no other place is left."""
    parts = ["увеличить бюджет"] if spec.budget_rub is not None else []
    if spec.max_travel_hours:
        parts.append(f"разрешить дорогу дольше {spec.max_travel_hours:g} ч")
    parts.append("выбрать другой месяц")
    return f"Других направлений под эти условия нет. Ослабьте одно условие: {', '.join(parts)}."


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


def _round_trip(probe: TripSpec, origin: str) -> int | None:
    """The cheapest round trip origin -> destination -> origin for the party, by synthetic
    transport: the sweep of up to 10 origins is an estimate, a live port is only for the plan
    (ADR-0002, #261)."""
    spec = probe.model_copy(update={"origin": origin})
    home, place = resolve_place(origin), resolve_place(spec.destinations[0])
    if home is None or place is None or spec.own_transport:
        return None
    legs = SyntheticTransport().legs(spec, _complete(home), _complete(place))
    there = _cheapest([o for o in legs if o.origin == origin])
    back = _cheapest([o for o in legs if o.destination == origin])
    return there.price_rub + back.price_rub if there and back else None


ADJ_GENITIVE = (("кий", "кого"), ("ний", "него"), ("ий", "ого"), ("ый", "ого"), ("ой", "ого"))


def _adj_genitive(word: str) -> str | None:
    return next((word[: -len(a)] + g for a, g in ADJ_GENITIVE if word.endswith(a)), None)


def _noun_genitive(word: str) -> str | None:
    if word.endswith("ль"):
        return word[:-1] + "я"  # «Ярославль», «Ставрополь»
    if word.endswith("ь"):
        return word[:-1] + "и"  # «Казань», «Пермь»
    if word.endswith("ы"):
        return None  # «Чебоксары»: a plural, the rule does not decline it
    if word.endswith("а"):
        return word[:-1] + ("и" if word[-2] in "гкхжчшщ" else "ы")
    if word[-1] in "бвгджзклмнпрстфхцчшщ":
        return word + "а"
    return word  # «Сочи», «Улан-Удэ»: not declined


def _genitive(name: str) -> str | None:
    """«Новосибирск» -> «Новосибирска», «Ростов-на-Дону» -> «Ростова-на-Дону», «Нижний Новгород»
    -> «Нижнего Новгорода», «Санкт-Петербург» -> «Санкт-Петербурга» for the chip «Из …». None
    when the rules would not read the form back as this city (the chip is then left out)."""
    if "-на-" in name:
        head, sep, tail = name.partition("-на-")
        parts = [_noun_genitive(head), sep + tail]
        form = None if None in parts else "".join(parts)
    elif " " in name:
        *adjs, noun = name.split()
        parts = [*map(_adj_genitive, adjs), _noun_genitive(noun)]
        form = None if None in parts else " ".join(parts)  # «Набережные Челны»: None
    elif "-" in name:
        first, last = name.rsplit("-", 1)
        if (adj := _adj_genitive(last)) and (noun := _noun_genitive(first)):
            form = f"{noun}-{adj}"  # «Петропавловска-Камчатского»
        else:
            form = (noun := _noun_genitive(last)) and f"{first}-{noun}"  # «Санкт-Петербурга»
    else:
        form = _noun_genitive(name)
    if not form:
        return None
    origin = extract(f"из {form}", has_destination=True)[0].get("origin")
    return form if origin and catalog.origin_hub(origin)[0]["name"] == name else None


def _from(name: str) -> str:
    """«из Новосибирска»; «из города X» when the rule has no form for X."""
    return f"из {gen}" if (gen := _genitive(name)) else f"из города {name}"


def _share(rub: int, budget: int) -> str:
    """Rounded down: a share that passes ROAD_GAP or ROAD_SHARE never reads below it (#380)."""
    return f"{100 * rub // budget} %"


def far_origin_question(spec: TripSpec) -> list[Question]:
    """#302, #351: no origin and a budget for the whole trip. The origin is asked when the road
    from DEFAULT_ORIGIN (a round trip for the party) costs ROAD_GAP % of the budget or more over
    the cheapest big hub farther than NEIGHBOUR_HUB_KM from the place (#380), or ROAD_SHARE % of the
    budget or more alone. The question names what
    is measured: the road sums (offers, TRAP-4), their difference and its share of the budget.
    It claims nothing about the whole trip. Otherwise the origin is an assumption."""
    if spec.origin or spec.budget_rub is None or not spec.destinations:
        return []
    probe = assume(spec)  # the dates and the party the plan would have
    home = _round_trip(probe, DEFAULT_ORIGIN)
    if home is None:
        return []
    hubs = sorted(catalog.hubs(), key=lambda h: -h["pop"])
    names = [h["name"] for h in hubs if h["name"] != DEFAULT_ORIGIN][:BIG_HUBS]
    if place := resolve_place(spec.destinations[0]):  # #404: a geocoder place too
        # The place itself and its neighbour hubs are no origin to compare: their road is always
        # small (Ростов-на-Дону — Геленджик 5 000 ₽), so the gap would measure the distance to
        # the nearest city, not the origin (#380).
        near = {h["name"] for h in hubs if catalog.km_between(place, h) < NEIGHBOUR_HUB_KM}
        names = [n for n in names if n not in near]
    sums = [(r, n) for n in dict.fromkeys(names) if (r := _round_trip(probe, n)) is not None]
    best = min(sums, default=None)
    budget = spec.budget_rub
    gap = best is not None and 100 * (home - best[0]) >= ROAD_GAP * budget
    if not (gap or 100 * home >= ROAD_SHARE * budget):
        return []
    text = f"{ORIGIN_ASK} Из Москвы дорога туда и обратно ≈ {_rub(home)}"
    if best and best[0] < home:
        diff = home - best[0]
        text += (
            f", {_from(best[1])} ≈ {_rub(best[0])} — разница ≈ {_rub(diff)}, "
            f"это {_share(diff, budget)} бюджета."
        )
    else:
        text += "."
    if 100 * home >= ROAD_SHARE * budget:
        text += f" Дорога из Москвы — {_share(home, budget)} бюджета {_rub(budget)}."
    text += " Если города нет в кнопках, напишите «из …»."
    options = [f"Из {w}" for w in ORIGIN_OPTIONS]
    if best and (gen := _genitive(best[1])) and f"Из {gen}" not in options:
        options.append(f"Из {gen}")  # after the five chips of #436, not between them (#442)
    return [Question(text=text, options=options)]


def origin_question(spec: TripSpec) -> list[Question]:
    """#436: no origin and no own tickets: the origin is asked. The road sums go into the text
    when a whole budget depends on them (far_origin_question)."""
    if spec.origin or spec.own_transport:
        return []
    text = f"{ORIGIN_ASK} От города вылета зависят цены, рейсы и стыковки. Если города нет в кнопках, напишите его."
    return far_origin_question(spec) or [
        Question(text=text, options=[f"Из {w}" for w in ORIGIN_OPTIONS])
    ]


def origin_name(text: str) -> str | None:
    """A departure city as the user wrote it (TRAP-3): «Питер», «мск», «из Масквы» -> the name of
    the catalogue; None when the text is no city or a country."""
    q = re.sub(r"^(?:из|от)\s+", "", text.strip().lower().replace("ё", "е")).strip(" .,!?«»")
    if not q or len(q.split()) > 3 or catalog.country_of(q):
        return None
    if place := catalog.resolve(q):
        return place["name"]
    forms = {}
    for h in catalog.hubs():
        forms[h["name"].lower()] = h["name"]
        if gen := _genitive(h["name"]):
            forms[gen.lower()] = h["name"]
    close = difflib.get_close_matches(q, forms, n=1, cutoff=ORIGIN_FUZZY)
    return forms[close[0]] if close else None


def _home_countries() -> set[str]:
    """The countries of the departure hubs (Россия)."""
    return {h["country"] for h in catalog.hubs()}


def _abroad(place: dict | None) -> bool:
    return place is not None and place["country"] not in _home_countries()


def bare_origin(message: str, has_place: bool = True) -> str | None:
    """The answer to the origin question without «из»: «Питер», «мск, вдвоём». A clause with «в»,
    «на», «до» names a destination or a sum, not the origin. A country is never the origin.

    has_place: the trip has its place already; then any city is the origin (abroad: the nearest
    hub, as before). Without a place (#442), a catalogue city abroad without a sea («Минск»,
    «Алматы») is the origin too; a sea place abroad («Анталья», «Дубай») or a place only the
    geocoder knows («Бодрум») names where the user goes, not where from."""
    for clause in re.split(r"[,;.!?\n]+", message):
        c = clause.strip()
        if not c or re.match(r"(?i)(?:в|во|на|до|к|ко|по|за)\s", c) or catalog.country_of(c):
            continue
        if name := origin_name(c):
            place = catalog.resolve(name)
            if not has_place and _abroad(place) and place["sea"]:
                continue  # «Анталья» on «Хочу отдохнуть»: the destination, read by extract
            return name
        words = c.split()
        capital = len(c) >= 4 and len(words) <= 3 and all(w[:1].isupper() for w in words)
        # «Орск»: a name off the catalogue, from the geocoder (TRAP-6: None offline)
        if capital and (place := catalog.lookup_online(c)) and (has_place or not _abroad(place)):
            return place["name"]
    return None


def kids_ask() -> list[Question]:
    options = ["Один ребёнок, 5 лет", "Двое детей, 5 и 10 лет", "Трое детей, 3, 6 и 10 лет"]
    return [Question(text="Сколько детей едет и сколько им лет?", options=options)]


def children_question(spec: TripSpec) -> list[Question]:
    """F25 (#302, #182): «с детьми» without a number and ages: asked, not two children of 7.
    The first message keeps no child in the spec; KIDS_UNKNOWN stands for them until named."""
    if spec.children_ages or KIDS_UNKNOWN not in spec.assumptions:
        return []
    return kids_ask()


def blocking_questions(spec: TripSpec, message: str = "") -> list[Question]:
    """At most 2 questions in one turn of a new session; the rest becomes assumptions named in
    the reply.

    #436: an unnamed origin is the first question (origin_question), not a silent Москва. Own
    tickets need none. Next to it goes one question of the old order (#302): the city of a hotel
    request (F14) or the place of a booking (#351); the children without a number (F25); the
    adults next to a child (#112). The guest count sets the price and the booking, so it is
    asked, not assumed. A message with neither a place nor a wish asks the month, then the
    budget. Without a place the adults question only goes next to the options
    (party_question), it never replaces them.
    """
    origin = origin_question(spec)  # #436: the origin goes first, in the same turn as the rest
    if not (spec.destinations or spec.wishes) and asks_stay(message):
        return [*origin, Question(text=STAY_ASK, options=[])]
    if not (spec.destinations or spec.wishes) and asks_booking(message):
        return [*origin, Question(text=BOOKING_ASK, options=[])]  # #351: tickets
    kids = children_question(spec)
    if spec.destinations:
        return [*origin, *(kids or party_question(spec))[:1]]
    if spec.wishes or spec.max_travel_hours is not None:
        # #365 (C21): «недалеко» is a wish of the road: the agent shows the near places at once
        return origin
    out = [*origin]
    if not spec.month and not spec.date_from:
        m = date.today().month
        options = [MONTH_NAMES[(m + i) % 12] for i in range(3)] + ["пока не знаю"]
        out.append(Question(text="Когда хотите поехать?", options=options))
    if spec.budget_rub is None and spec.hotel_budget_rub is None:
        options = ["до 50 000 ₽", "до 100 000 ₽", "до 200 000 ₽", "не важно"]
        out.append(Question(text="Какой бюджет на всю поездку?", options=options))
    return out[:2]  # the origin first; a budget not asked is an assumption the user sees


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
    if data["own_transport"] and not data["origin"]:
        pass  # own tickets (#408): no leg is searched, the origin is not needed
    elif not data["origin"]:
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


def _city_in_clause(message: str, low: str, link: re.Match, later: list[str]) -> bool:
    """A city of `later` is named for the first time inside the clause of the link word.

    Only the cities extract() found: no geocoder, no catalogue lookup (TRAP-6). Surface forms
    come from the patterns of extract: «Питер», «в Казани».
    """
    start, end = link.start(1), link.end(1)
    words = [(w.start(), w.group()) for w in re.finditer(r"[\w-]+", low)]
    for name in later:
        found = [m.start() for n, rx in PLACE_RES if n == name and (m := rx.search(low))]
        if len(message) == len(low):  # the capital-letter names: «Нижний Новгород»
            found += [
                m.start() for n, rx in CAPITAL_PLACES if n == name and (m := rx.search(message))
            ]
        # any case of a one-word name («Ригу», «Уфу»), or the name as it is written (not known)
        known = next((pos for pos, w in words if _known(w) == name or w == name.lower()), None)
        if known is not None:
            found.append(known)
        # the first mention is inside the clause: a city named before the link is no next stop
        if found and start <= min(found) < end:
            return True
    return False


def is_route(message: str, destinations: list[str]) -> bool:
    """#128: several cities named as a route («потом в Казань», two cities each with nights).

    «Сочи или Анапа» are candidates, not a route: see severals().
    """
    low = message.lower().replace("ё", "е")
    if len(destinations) < 2 or OR_RE.search(low):
        return False
    stays = STAY_RE.findall(AHEAD_RE.sub(" ", low))
    linked = any(_city_in_clause(message, low, m, destinations[1:]) for m in LINK_RE.finditer(low))
    # #365: «по 3 дня» with two cities is a stay in each: a route
    return linked or bool(ROUTE_RE.search(low)) or len(stays) > 1 or bool(EACH_STAY_RE.search(low))


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


def _priced(n: int, offers) -> str:
    """A price with the label of the offers it sums (TRAP-7, #567): «синтетика», the real source
    or «часть цен — синтетика»."""
    return f"{_rub(n)} ({price_mark(offers)})"


def _chosen(plan: Plan) -> list:
    return [o for o in plan.offers if o.offer_id in plan.selected]


def _ordinal(text: str) -> int | None:
    m = ORDINAL_RE.search(text.lower())
    if not m:
        return None
    return ORDINALS[m.group(1)[:4]] if m.group(1) else int(m.group(2)) - 1


def named_in(text: str, names: list[str]) -> str | None:
    """#458: the first of the names that the text mentions (case and ending ignored), or None."""
    t = catalog.norm(text)
    hits = []
    for n in dict.fromkeys(names):
        stem = catalog._stem(catalog.norm(n))
        if m := re.search(rf"(?<!\w){re.escape(stem)}", t):
            hits.append((m.start(), -len(stem), n))  # earliest, then the longest: Вена < Венеция
    return min(hits)[2] if hits else None


# A «time» command holds (HH:MM, mode of the noun or None) as its second item.
LegCommand = tuple[str, str | int | tuple[str, str | None], str | None]
# «второй отель и поезд»: the hotel row named in a pick of transport (same rows as «беру 2»).
HOTEL_ROW_RES = (
    re.compile(rf"\b({LEG_ORDINAL})\s+отел\w*"),
    # A digit is a row only when it ends the clause: «отель 3 звезды», «отель 4*», «отель 5 км
    # от центра», «до отеля 2 часа», «отель 4.5 и выше», «отель 3 и 4 звезды» state a need.
    re.compile(
        r"\bотел\w*\s+(?:№\s*|номер\s+)?(\d{1,2})(?![.,]\d)(?=\s*(?:$|[;.!?]|,(?!\s*\d)|и(?:$|\s+(?!выше|больше|более|ниже|\d))))"
    ),
)


def hotel_row(text: str) -> int | None:
    """The row from 0 of the hotel named in the message («второй отель», «отель 3»), or None."""
    t = text.lower().replace("ё", "е")
    for rx in HOTEL_ROW_RES:
        if m := rx.search(t):
            n = m.group(1)
            return (
                int(n) - 1 if n.isdigit() else next(i for i, st in enumerate(LEG_STEMS) if st in n)
            )
    return None


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
    verb = bool(LEG_PICK_RE.search(t)) or hotel_row(t) is not None  # #388: «отель 1 и поезд»
    out: list[LegCommand] = []
    for clause in LEG_CLAUSE_RE.split(t):
        if verb and (m := LEG_TIME_RE.search(clause)):
            direction = "there" if LEG_THERE_RE.search(clause) else None
            direction = "back" if LEG_BACK_RE.search(clause) else direction
            noun = m.group(1)
            mode = "train" if noun.startswith("поезд") else None
            mode = "flight" if noun.startswith(("перел", "самол")) else mode
            out.append(("time", (f"{int(m.group(2)):02d}:{m.group(3)}", mode), direction))
            continue
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


def _cheapest(legs: list[TransportOffer]) -> TransportOffer | None:
    return min(legs, key=lambda x: (x.price_rub, x.offer_id), default=None)


def _no_time(
    table: list[TransportOffer], dirs: dict, ways: list[str], hhmm: str, mode: str | None
) -> str:
    """C12: «Рейса в 06:15 нет. Туда: 08:00 (рейс 1), 15:10 (рейс 2).» The times and rows are
    those of the plan offers (TRAP-4); the row is the one «рейс N» takes."""
    lines = []
    for d in ways:
        have = [
            f"{x.depart_at:%H:%M} (рейс {table.index(x) + 1})"
            for x in dirs[d]
            if x.mode != "car" and mode in (None, x.mode)
        ]
        if have:
            lines.append(f"{'Туда' if d == 'there' else 'Обратно'}: {', '.join(have)}.")
    tail = " ".join(lines) if lines else "Других рейсов в данных нет."
    what = {"train": "Поезда", "flight": "Перелёта"}.get(mode or "", "Рейса")
    return f"{what} в {hhmm} нет. {tail}"


def switch_transport(
    plan: Plan, text: str, hotel_row: int | None = None
) -> tuple[Plan | None, str]:
    """The plan with the transport the user asked for, or None and the reason ("" if the
    message is not a pick).

    hotel_row: the row from 0 of the hotel table («второй отель и поезд»): the pick changes the
    hotel in the same step (hotel_rows, as «беру 2»).

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
    old_hotel, hotel_note = hotel, ""
    if hotel_row is not None:
        rows = hotel_rows(plan.offers, plan.selected)
        if not 0 <= hotel_row < len(rows):
            return None, f"Отеля {hotel_row + 1} нет: в таблице их {len(rows)}."
        hotel = rows[hotel_row]
        if hotel.offer_id != old_hotel.offer_id:
            hotel_note = f"Отель заменён: {hotel.title}. "
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
        hhmm, mode = what if isinstance(what, tuple) else ("", None)
        want = (
            direction if kind == "row" else mode if kind == "time" else what
        )  # the mode asked for
        if want and plan.spec.modes and want not in plan.spec.modes:
            return None, ""
        if kind == "time":
            ways = [direction] if direction else ["there", "back"]
            hit = [
                (d, x)
                for d in ways
                for x in dirs[d]
                if x.mode != "car" and mode in (None, x.mode) and f"{x.depart_at:%H:%M}" == hhmm
            ]
            if not hit:
                return None, _no_time(table, dirs, ways, hhmm, mode)
            d, new[d] = hit[0]
            touched.add(d)
            continue
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
    if chosen == [x.offer_id for x in now.values() if x] and not hotel_note:
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
    # The «Почему» and the labels of the bundles name the selected trip, not the old one.
    out.explanations = explain(out.spec, out.offers, out.selected)
    out.bundles = bundles(out.spec, out.offers, out.selected)
    titles = [x.title for x in new.values() if x]
    return out, hotel_note + "Транспорт заменён: " + "; ".join(titles) + "."


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


# #278: a cheaper plan without a sum. «Дешевле, чем в …?» and «дешевле ли …?» compare; they
# lower no budget.
CHEAPER_RE = re.compile(
    r"\b(?:по)?дешевле\b|\bбюджет\w*\s+(?:по)?меньше|\b(?:по)?меньше\s+бюджет"
    r"|\bэкономнее\b|\bслишком\s+дорого\b|\bдорогов\w+"
)
CHEAPER_WORD = r"(?:по)?дешевле|экономнее"
# #310: a refusal is adjacent to the word: «не надо дешевле», «дешевле не надо». A refusal
# elsewhere in the phrase («подешевле, не хочу переплачивать») refuses something else.
CHEAPER_REFUSAL_RE = re.compile(
    rf"\bне\s+(?:(?:надо|нужно|нужн\w+|хочу|хотим|стоит)\s+)?(?:{CHEAPER_WORD})\b"
    rf"|\b(?:{CHEAPER_WORD})\s+не\s+(?:надо|нужно|нужн\w+|хочу|хотим|стоит)\b"
)
CHEAPER_ASK_RE = re.compile(
    rf"\b(?:{CHEAPER_WORD})\s*,?\s*(?:чем|ли)\b|\bли\b|\bпочему\b|\bсколько\b"
    rf"|{CHEAPER_REFUSAL_RE.pattern}"
)
# #316: «дешевле не бывает?» asks for the minimum of the offers.
CHEAPER_MIN_RE = re.compile(rf"\b(?:{CHEAPER_WORD})\s+не\s+(?:бывает|будет|бывало)\b")
# «можно ли», «есть ли», «нельзя ли» ask for the thing: a request.
REQUEST_LI_RE = re.compile(r"\b(?:можно|есть|нельзя|будет)\s+ли\b")
BUDGET_STEP = 15  # percent off the current total per «подешевле»


def asks_cheaper(low: str) -> bool:
    """«Можно ли подешевле?» is a request; «дешевле ли …?», «дешевле, чем …?» and a refusal are not."""
    clean = REQUEST_LI_RE.sub("можно", low)
    if CHEAPER_MIN_RE.search(clean):
        return False  # «дешевле не бывает?» is a remark or a question
    return bool(CHEAPER_RE.search(clean) and not CHEAPER_ASK_RE.search(clean))


def is_rebuild(low: str) -> bool:
    """A phrase that asks to rebuild the plan: it is never a consent (#184)."""
    return bool(pick_kinds(low) or NO_FLIGHT_RE.search(low) or stay_delta(low))


def _ranked_set(plan: Plan) -> list:
    """The best set the ranking gives for the spec of the plan, without the hotel the user
    picked by hand (dag._keep_pick): its total is the real minimum of the offers."""
    ids, _ = rank(plan.spec, plan.offers)
    return [o for o in plan.offers if o.offer_id in ids]


def _ranked_total(plan: Plan) -> int:
    return sum(o.price_rub for o in _ranked_set(plan))


# The wish «недорого» in words other than «дешевле» (see api/extract.py WISHES).
WISH_CHEAP_RE = re.compile(r"недорог|бюджетн|эконом|cheap")


def _other_edits(found: dict) -> bool:
    """The message changes a condition besides the budget. The wish «недорого» comes from the
    word «подешевле» itself: it is no other condition (#316)."""
    rest = set(found) - {"budget_rub"}
    if set(found.get("wishes", [])) <= {"недорого"}:
        rest.discard("wishes")
    return bool(rest)


WHY_RE = re.compile(r"\bпочему\b")


def talks_cheaper(low: str) -> bool:
    """«Не надо дешевле» and «дешевле не бывает?» talk about the word, they ask for no change.
    «Почему не дешевле?» is no refusal: it stays the wish «недорого», as before (#316)."""
    if CHEAPER_MIN_RE.search(low):
        return True
    return bool(CHEAPER_REFUSAL_RE.search(low) and not WHY_RE.search(low))


def drop_cheaper_wish(found: dict, low: str) -> dict:
    """«Не надо дешевле», «дешевле не бывает?» name the word, not a wish: «недорого» does not
    enter the spec and does not re-plan the trip (#316)."""
    wishes = found.get("wishes")
    if not wishes or "недорого" not in wishes or not talks_cheaper(low):
        return found
    if WISH_CHEAP_RE.search(re.sub(rf"\b(?:{CHEAPER_WORD})\b", " ", low)):
        return found  # «недорогой отель, не надо дешевле»: the wish is said in other words
    rest = [w for w in wishes if w != "недорого"]
    kept = {k: v for k, v in found.items() if k != "wishes"}
    return {**kept, "wishes": rest} if rest else kept


def _cheapest_set(plan: Plan, cart: Cart) -> list:
    """The cheapest set of offers for the trip: a trial re-plan with a budget nobody meets."""
    trial, _, _ = replan(plan, merge(plan.spec, {"budget_rub": 0}, []), cart)
    return _ranked_set(trial)


def cheaper_talk_line(old: ChatReply, low: str) -> str:
    """The answer to «не надо дешевле» (the budget stays) and «дешевле не бывает?» (the minimum
    of the offers): no budget changes, no plan changes (#316, TRAP-4)."""
    total = _selected_total(old.plan)
    now = _priced(total, _chosen(old.plan))
    if CHEAPER_MIN_RE.search(low):
        cheapest = _cheapest_set(old.plan, old.cart)
        floor = sum(o.price_rub for o in cheapest)
        if total <= floor:
            return f"Дешевле не бывает: минимум в предложениях — {now}, он уже в плане."
        return (
            f"Бывает: минимум в предложениях — {_priced(floor, cheapest)}, сейчас {now}. "
            "Напишите «подешевле», и я пересоберу план."
        )
    return f"Хорошо, бюджет не меняю: итог остаётся {now}."


def agent_budget(spec: TripSpec) -> bool:
    """The budget is a step of the agent from «подешевле», not a sum the user named (#316)."""
    return spec.budget_rub is not None and any(
        _label(a) == "бюджет" and AGENT_BUDGET in a for a in spec.assumptions
    )


def end_agent_budget(old: TripSpec, spec: TripSpec, found: dict) -> TripSpec:
    """The next edit of dates, length or party ends the budget of «подешевле»: the user never
    named that sum, and it was fitted to the old trip (#316). A sum said now stays."""
    if (
        "budget_rub" in found
        or spec.budget_rub != old.budget_rub
        or not agent_budget(old)
        or all(getattr(old, f) == getattr(spec, f) for f in BUDGET_RESET)
    ):
        return spec
    kept = [a for a in spec.assumptions if _label(a) != "бюджет"]
    return spec.model_copy(update={"budget_rub": None, "assumptions": kept})


def refit_agent_budget(old: Plan, cart: Cart, spec: TripSpec) -> tuple[TripSpec, str]:
    """#550: new dates alone (the party is the same) keep the wish of «подешевле»: the budget is
    the same step again, under the total of the new dates (lower_budget), not the stale sum.
    `spec` comes from end_agent_budget, without the budget. Nothing cheaper: no budget."""
    trial, _, _ = replan(old, spec, cart)
    found: dict = {}
    notes: list[str] = []
    if trial.offers:
        lower_budget(trial, cart, found, notes)
    if "budget_rub" not in found:
        return spec, ""
    line = (
        f"Бюджет «подешевле» пересчитан под новые даты: {_rub(found['budget_rub'])} "
        f"вместо {_rub(old.spec.budget_rub or 0)}."  # a budget is no provider price: no mark
    )
    return merge(spec, {"budget_rub": found["budget_rub"]}, notes), line


def lower_budget(plan: Plan, cart: Cart, found: dict, notes: list[str]) -> None:
    """«Бюджет поменьше» at a built plan: the trip budget is one step (15 %) under the current
    total, rounded down to 100 ₽. The DAG then re-plans the ranking only (#278).

    The budget never goes under the cheapest total the offers give for the spec with the other
    edits of the same message («подешевле, с завтраком»): a trial re-plan finds it (#310). If
    that is the target or more, the budget is the cheapest total; if nothing is cheaper than the
    plan and nothing else changes, the budget stays."""
    total = _selected_total(plan)
    if total <= 0 or "budget_rub" in found:
        return
    target = total * (100 - BUDGET_STEP) // 100 // 100 * 100
    edited = merge(plan.spec, {**found, "budget_rub": target}, list(notes))
    trial, _, _ = replan(plan, edited, cart)
    cheapest = _ranked_total(trial)
    if cheapest >= total and not _other_edits(found):
        found.pop("wishes", None)  # nothing is cheaper: the word «подешевле» changes nothing
        return
    found["budget_rub"] = max(target, cheapest)
    new = found["budget_rub"]
    how = "ниже итога" if new < total else "итог был"
    notes.append(f"бюджет: {_rub(new)} ({AGENT_BUDGET}; {how} {_rub(total)})")


def cheaper_line(old: Plan, new: Plan, edited: bool = False) -> str:
    """The honest line after «подешевле»: the minimum comes from the offers, never invented.
    `edited`: the message also changed other conditions, so the total may rise for them."""
    a, b = _selected_total(old), _selected_total(new)
    goal = new.spec.budget_rub
    best = _ranked_set(new)
    cheapest = sum(o.price_rub for o in best)
    if b > cheapest:  # a hotel picked by hand costs more than the best set (TRAP-4)
        hotel = _hotel_of(new)
        who = f"С отелем «{hotel.title}» " if hotel else "С вашим выбором "
        return (
            f"{who}дешевле нет; самый дешёвый набор — {_priced(cheapest, best)}. "
            "Назовите сумму, если нужно другое."
        )
    if b >= a:
        if edited:
            return (
                f"С вашими условиями итог {_priced(b, _chosen(new))} (было {_priced(a, _chosen(old))}): "
                "дешевле под них набора нет. Назовите сумму, если нужно другое."
            )
        return (
            f"Дешевле в предложениях нет: сейчас {_priced(b, _chosen(new))} — это минимум под ваши "
            "условия. Назовите сумму, если нужно другое."
        )
    if goal is not None and b >= goal:
        return (
            f"Снизил итог до {_priced(b, _chosen(new))} — это минимум под ваши условия. "
            "Назовите сумму, если нужно другое."
        )
    return ""


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
    out.explanations = explain(out.spec, out.offers, out.selected)
    out.bundles = bundles(out.spec, out.offers, out.selected)
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
    out.explanations = explain(out.spec, out.offers, out.selected)
    out.bundles = bundles(out.spec, out.offers, out.selected)
    note = f"Отель получше — {nxt.title}: {'; '.join(what)} ({price_mark([nxt])})."
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
            f"источника ({price_mark([o for o in chosen if o.kind == 'transport'])})."
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
            f"{o.title} — {_priced(o.price_rub, [o])}, в пути {span(o)}"
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


# #369: «сколько это на 1 человека?», «покажи цену на человека»: a price per head from the plan
# total; the party stays. «Есть номер на 2 человека?» asks about a room, not about the party.
PER_HEAD_RE = re.compile(r"\bна\s+(?:(?:1|одного|каждого)\s+)?(?:человека|душу|персону)\b")
PER_HEAD_CUE_RE = re.compile(r"сколько|\bцен[ауы]\b|стоимост|стоит|покаж\w*\s+цен")
ROOM_FOR_RE = re.compile(r"\bномер\w*\s+на\s+(?:\d{1,2}|двоих|троих|четверых)\b")


def _per_head_reply(old: ChatReply, low: str) -> str:
    """The reply to a price-per-head question, from the plan only (TRAP-4); "" if it is none."""
    spec = old.plan.spec
    people = spec.adults + len(spec.children_ages)
    if PER_HEAD_RE.search(low) and PER_HEAD_CUE_RE.search(low):
        items = old.cart.items or _chosen(old.plan)
        total = old.cart.total_rub or sum(o.price_rub for o in items)
        if not total:
            return f"Состав не меняю: {people} чел. Итог пока не посчитан, цену на человека назвать не могу."
        return (
            f"Состав не меняю: {people} чел. Итого {_priced(total, items)}, "
            f"{'в среднем ' if spec.children_ages else ''}на человека около "
            f"{_priced(round(total / people), items)}"
            + (", дети по детскому тарифу." if spec.children_ages else ".")
        )
    if ROOM_FOR_RE.search(low) and "?" in low:
        return (
            f"Состав не меняю: в плане {people} чел. Чтобы пересчитать на другой состав, "
            "напишите, кто едет: например, «нас двое»."
        )
    return ""


# #311: «дешевле, чем в Турции?», «это дешевле, чем в среднем?» ask for a comparison. They name no
# new place and no «недорого» wish; the plan stays. A request («хочу дешевле, чем сейчас») is an edit.
# #390: «а в Турции дешевле?» asks the same without «чем». Only the whole message «[а] в <place in
# the prepositional case> дешевле?»: «поехали в Египет, там дешевле?» and «в Турцию, там дешевле?»
# move the trip, as on main.
PRICE_VERSUS_RE = re.compile(
    r"\b(?:дешевле|дороже|выгоднее)\b[^?]*\bчем\b[^?]*\?"
    r"|^\s*(?:а\s+)?(?:в|на)\s+(?:\w+\s+)?\w*[^\Wую]\s+(?:будет\s+|вс[её]\s+же\s+)?"
    r"(?:дешевле|дороже|выгоднее)\s*\?\s*$"
    # #483: «поезд дешевле самолёта?» has no «чем»
    r"|\b(?:поезд|самол[её]т|машин)\w*\s+(?:будет\s+)?(?:дешевле|дороже|выгоднее)\s+"
    r"(?:поезд|самол[её]т|машин)\w*\s*\?"
)
VERSUS_ORDER_RE = re.compile(
    r"\b(?:сделай\w*|подбер\w*|найд\w*|найти|покаж\w*|предлож\w*|можно|есть)\b"
)
MARKET_RE = re.compile(r"\bв\s+среднем\b|рынк|\bобычно\b")  # «дешевле, чем в среднем?»
# «а дешевле, чем в Турции» inside a longer message: the comparison words only (#483)
VERSUS_CLAUSE_RE = re.compile(
    r"(?i)\b(?:а\s+)?(?:это\s+)?(?:(?:на\s+)?(?:поезд|самол[её]т|машин)\w*\s+)?"
    r"(?:дешевле|дороже|выгоднее),?\s+чем\s+(?:в\s+|на\s+)?[\w-]+"
    # no «чем»: «поезд дешевле самолёта»
    r"|\b(?:поезд|самол[её]т|машин)\w*\s+(?:будет\s+)?(?:дешевле|дороже|выгоднее)\s+"
    r"(?:поезд|самол[её]т|машин)\w*"
)


MODE_NAMES = {"train": "поезд", "flight": "самолёт", "car": "машина"}


def _mode_versus_reply(old: ChatReply, modes: list[str]) -> str:
    """«поезд дешевле, чем самолёт?»: the cheapest offer of each mode in the plan (TRAP-4)."""
    rows = []
    for mode in modes:
        found = [o for o in old.plan.offers if getattr(o, "mode", None) == mode]
        name = MODE_NAMES[mode].capitalize()
        cheapest = min(found, key=lambda o: o.price_rub, default=None)
        rows.append(
            f"{name}: от {_priced(cheapest.price_rub, [cheapest])}"
            if cheapest
            else f"{name}: в плане нет"
        )
    return f"Это вопрос: план не меняю. {'; '.join(rows)}. {HINTS[old.stage]}"


def _price_versus_reply(old: ChatReply, low: str, found: dict) -> str:
    """The reply to a price comparison question, from the plan only (TRAP-4); "" if it is none.
    The plan has the price of its own trip only: the price of the other place and the market
    average are not in the data, and we say so."""
    if not PRICE_VERSUS_RE.search(low) or CHOICE_RE.search(low) or VERSUS_ORDER_RE.search(low):
        return ""
    spec = old.plan.spec
    others = [d for d in found.get("destinations", []) if d not in spec.destinations]
    if not others and len(modes := [m for m, rx in MODES if re.search(rx, low)]) == 2:
        return _mode_versus_reply(old, modes)  # #483: «а поезд дешевле, чем самолёт?»
    if not (others or MARKET_RE.search(low)):
        return ""  # no other place and no market: not a price comparison, the old rules read it
    items = old.cart.items or _chosen(old.plan)
    total = old.cart.total_rub or sum(o.price_rub for o in items)
    place = f"в направлении «{', '.join(others)}»" if others else "в среднем по рынку"
    mine = ", ".join(spec.destinations) or "текущее направление"
    now = f"Сейчас в плане: {mine}, итого {_priced(total, items)}. " if total else ""
    ask = f"«{others[0]}»" if others else "«подешевле»"
    return (
        f"Это вопрос: план не меняю. {now}Цен {place} для сравнения в данных нет — "
        f"цифр не выдумываю. Чтобы собрать такой план, напишите: {ask}. {HINTS[old.stage]}"
    )


def _own_answer(text: str, message: str, use_llm: bool) -> tuple[str, bool]:
    """#617: with a key the model words the answer; checked against the template (TRAP-4)."""
    own = llm.answer_question(message, text) if use_llm and message else None
    return (own, True) if own else (text, False)


def _question_answer(
    old: ChatReply, low: str = "", message: str = "", found: dict | None = None
) -> str:
    """The reply to a question at assemble or checkout: the facts of the plan or the cart only
    (TRAP-4), the stage and the cart stay. Weather, visas and «почему не <отель>» get the fact
    and the hint of the stage, not a repeat of the plan (#186). `found`: what extract read from
    the message, for a question about a city the plan does not have (#123)."""
    head = "Это вопрос: план и корзину не меняю."
    if old.stage == "compare":
        head = "Это вопрос: план не меняю."
    if fact := plan_fact(old.plan, low, message, found):
        return f"{head}\n{fact}\n{HINTS[old.stage]}"
    if fact := _question_fact(old, low):
        head += "\n" + fact
    if old.stage == "compare":
        return f"{head}\n{HINTS['compare']}"
    if old.stage == "assemble":
        return f"{head}\n{_text_assemble(old.plan)}"
    rows = [f"{o.title} — {_priced(o.price_rub, [o])}" for o in old.cart.items]
    cart = (
        "В корзине:\n" + "\n".join(rows) + f"\nИтого {_priced(old.cart.total_rub, old.cart.items)}."
        if rows
        else "Корзина пуста."
    )
    if old.plan.timeline:  # times come from the timeline tool, never from the model
        cart += "\nПлан поездки:\n" + "\n".join(
            f"{_at(e.at)} — {e.title}" for e in old.plan.timeline
        )
    edit = "Чтобы изменить поездку, напишите, что поменять: например, «хотим с бассейном»."
    return f"{head}\n{cart}\n{edit} {HINTS['checkout']}"


def _with_tail(text: str, plan: Plan, low: str, message: str, found: dict) -> str:
    """#212: a consent with a question («беру, а что с визой?») gets the fact line before the text."""
    fact = consent_fact(plan, low, message, found)
    return f"{fact}\n{text}" if fact else text


def _tail(spec: TripSpec, hint: str, extra: list[str] | None = None) -> list[str]:
    # #458: the note «уже показаны» is internal state, not text for the user
    said = [*(a for a in spec.assumptions if _label(a) != SHOWN), *(extra or [])]
    head = ["Допущения: " + "; ".join(said) + "."] if said else []
    return head + [hint]


def _road_note(plan: Plan) -> list[str]:
    """#321: nights on the road move the car start before the check-in and the return home
    after the check-out. The dates and the nights come from the selected legs (TRAP-4)."""
    by_id = {o.offer_id: o for o in plan.offers}
    cars = sorted(
        (
            o
            for i in plan.selected
            if isinstance(o := by_id.get(i), TransportOffer) and o.mode == "car"
        ),
        key=lambda o: o.depart_at,
    )
    if len(cars) != 2 or not any(map(road_nights, cars)):
        return []
    there, back = cars
    n_there, n_back = road_nights(there), road_nights(back)
    nights = (
        f"{n_there} в каждую сторону" if n_there == n_back else f"туда {n_there}, обратно {n_back}"
    )
    days = f"выезд {there.depart_at:%d.%m}, возврат {back.arrive_at:%d.%m}"
    return [f"дорога: {days}, ночёвок в пути: {nights} (оценка)"]


def _text_clarify(questions: list[Question]) -> str:
    return "Чтобы подобрать поездку, уточню: " + " ".join(q.text for q in questions)


def _unchecked_lines(spec: TripSpec) -> list[str]:
    """#361: a wish with no data is named in the reply, not dropped."""
    unchecked = unchecked_wishes(spec) + [w for w in spec.wishes if is_unchecked(w)]
    return [f"Не проверяю по данным: {', '.join(unchecked)}."] if unchecked else []


def _text_inspire(plan: Plan) -> str:
    spec = plan.spec
    guests = spec.adults + len(spec.children_ages)
    lines = ["Подходящие направления (цены, климат и время в пути — оценка, синтетика):"]
    for i, d in enumerate(plan.destinations, 1):
        climate = "".join(
            f", {label} {v:g} °C" for label, v in (("днём", d.air_c), ("вода", d.water_c)) if v
        )
        # #321: a car with nights on the road shows its hours at the wheel (inspire.ROAD_LINE).
        road = next((t for t in d.tradeoffs if t.startswith(ROAD_LINE)), None)
        minuses = [t for t in d.tradeoffs if t != road]
        line = (
            f"{i}. {d.name} ({d.country}) — от {_rub(d.est_total_rub)} на {guests} чел., "
            f"{road or f'в пути {d.travel_hours:g} ч'}{climate}. {_cap(d.why)}."
        )
        if minuses:
            line += f" Минусы: {'; '.join(minuses)}."
        if d.violates:
            line += f" Нарушает требования: {'; '.join(d.violates)}."
        lines.append(line)
    lines += _unchecked_lines(spec)
    return "\n".join(lines + _tail(spec, "Напишите номер или название направления."))


def _selected_total(plan: Plan) -> int:
    return sum(o.price_rub for o in plan.offers if o.offer_id in plan.selected)


# #114: the mode the user asked for, when the data has none: «Поезда … в данных нет».
NO_MODE = {"train": "поезда", "flight": "перелёта", "car": "поездки на машине"}
OFFER_MODE = {"train": "поезд", "flight": "перелёт", "car": "машину"}
# #532: «беру» on a plan without a hotel (the hotel source failed): no cart of the road alone.
NO_HOTEL = (
    "В корзину пока не кладу: отеля в плане нет — источник отелей не ответил или ни один "
    "отель не подошёл. Можно повторить запрос, выбрать другие даты или другое направление."
)


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


def _span_at(o: TransportOffer) -> str:
    """«10.12 18:50 → 11.12 07:10 (UTC+3)»: local times of the leg, one zone when they share it."""
    a, b = _zone(o.depart_at), _zone(o.arrive_at)
    start = f"{o.depart_at:%d.%m %H:%M}" + ("" if a == b else f" ({a})")
    return f"{start} → {o.arrive_at:%d.%m %H:%M} ({b})"


def _offer_lines(plan: Plan) -> list[str]:
    """#468: the selected trip, one line per item. Names, prices and times are the fields of
    the selected offers (TRAP-4); «Включено» names only what an offer field says."""
    chosen = [o for o in plan.offers if o.offer_id in plan.selected]
    lines = ["Предлагаю:"]
    included = []
    for h in (o for o in chosen if isinstance(o, HotelOffer)):
        stars = f", {h.stars}★" if h.stars else ""
        cancel = "бесплатная отмена" if h.cancellation.refundable else "невозвратный"
        lines.append(
            f"Отель: {h.title}{stars}, {MEALS[h.meal]}, {cancel} — {_priced(h.price_rub, [h])}"
        )
        if h.meal != "RO":
            included.append(MEALS[h.meal])
        if h.cancellation.refundable:
            until = h.cancellation.free_until
            included.append(
                "бесплатная отмена"
                + (f" до {until.astimezone(ZoneInfo(h.tz)):%d.%m}" if until else "")
            )
    city = next((o.city for o in chosen if isinstance(o, HotelOffer)), plan.spec.destinations[0])
    legs = [o for o in chosen if isinstance(o, TransportOffer)]
    for o in sorted(legs, key=lambda o: o.depart_at):  # own tickets are no offers (offers.py)
        way = "Обратно" if o.origin == city else "Туда"
        title = o.title.replace(f", {o.depart_at:%d.%m %H:%M}", "", 1)
        # C14 (TRAP-5): the duration stands as a fact of the leg, so the chat shows it next to
        # local times of different zones (web/src/format.ts reads it as a fact of the ride)
        lines.append(
            f"{way}: {title[:1].lower()}{title[1:]}, в пути {span(o)}, {_span_at(o)}"
            f" — {_priced(o.price_rub, [o])}"
        )
    rides = [o for o in chosen if o.kind == "transfer"]
    if rides:
        names = "; ".join(o.title.removeprefix("Трансфер: ") for o in rides)
        lines.append(f"Трансферы: {names} — {_priced(sum(o.price_rub for o in rides), rides)}")
    # paid extras of the hotel («Ранний заезд …»): the items sum to «Итого»
    lines += [
        f"Доп. услуга: {o.title} — {_priced(o.price_rub, [o])}" for o in chosen if o.kind == "extra"
    ]
    if included:
        lines.append(f"Включено: {', '.join(included)}.")
    lines.append(f"Итого {_priced(_selected_total(plan), chosen)}.")
    return lines


# TRAP-7 (#567): the header names the real sources of the offers, the rest is synthetic.
REAL_PARTS = {
    "hotel": "отели — 101hotels",
    "flight": "перелёты — Авиасейлс",
    "train": "поезда — tutu",
}


def _prices_line(offers) -> str:
    real = [o for o in offers if not o.synthetic]
    if not real:
        return "Цены — синтетика."
    parts = dict.fromkeys(
        REAL_PARTS.get(getattr(o, "mode", o.kind), "прочие — реальные данные") for o in real
    )
    rest = "; остальные — синтетика" if len(real) < len(offers) else ""
    return f"Цены: {', '.join(parts)}{rest}."


def _text_compare(plan: Plan) -> str:
    spec = plan.spec
    kids = f" + дети {', '.join(map(str, spec.children_ages))} лет" if spec.children_ages else ""
    rows = hotel_rows(plan.offers, plan.selected)  # one row per hotel, not per tariff
    lines = [
        f"{spec.destinations[0]}, {period(spec)}, {spec.nights} ноч., {spec.adults} взр.{kids}"
        + ("." if kids else ""),
        _prices_line(plan.offers),
        *([_cap(line) + "."] if (line := own_tickets(spec)) else []),
        *([line] if (line := destination_line(plan)) else []),
        *([line] if (line := closed_line(plan)) else []),
        *([line] if (line := missing_mode(plan)) else []),
        *([line] if (line := rooms_line(spec)) else []),
        # #349: the hotels and legs are on the board; the chat keeps a short summary.
        (
            "Отели и рейсы — на доске: детали и номера там. "
            f"Отелей: {len(rows)}, вариантов транспорта: {sum(o.kind == 'transport' for o in plan.offers)}."
        ),
    ]
    if spec.pets and (n := sum(not h.pets_allowed for h in rows)):
        lines.append(f"{NO_PET_MARK} у {n} из {len(rows)} отелей.")
    if _pet_flies(plan):
        lines.append(NO_PET_FLIGHT)
    # C14 (#364, TRAP-5): the chosen legs keep local times with zones (_offer_lines, #468).
    lines += _offer_lines(plan)
    for e in plan.explanations[:1]:
        lines.append(f"Почему: {e.why}." + "".join(f" {_cap(x)}." for x in e.tradeoffs))
        lines += [f"Риск: {r}." for r in e.risks]
    for c in plan.changes:
        lines.append(f"Изменено {c.what}: {c.before} → {c.after}.")
    lines += _unchecked_lines(spec)
    return "\n".join(
        lines
        + _tail(
            spec,
            "Напишите «беру» или номер отеля."
            if spec.own_transport
            else "Напишите «беру» или номер отеля; транспорт — «возьми поезд» или «рейс 2».",
            _road_note(plan),
        )
    )


def _zone(dt: datetime) -> str:
    """«UTC+10», «UTC+5:30», «UTC-3»: the offset of a local time from the tools (TRAP-5)."""
    off = dt.utcoffset() or timedelta()
    minutes = int(off.total_seconds() // 60)
    sign, minutes = ("+" if minutes >= 0 else "-"), abs(minutes)
    h, m = divmod(minutes, 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def _at(dt: datetime) -> str:
    """A local time of a leg or an event with its zone (C14, #364)."""
    return f"{dt:%d.%m %H:%M} ({_zone(dt)})"


def _pet_flies(plan: Plan) -> bool:
    """#575: the pet goes on a chosen flight, a condition of the airline (offers.PET_FLIGHT)."""
    return plan.spec.pets and any(
        isinstance(o, TransportOffer)
        and o.mode == "flight"
        and o.pets_allowed
        and o.offer_id in plan.selected
        for o in plan.offers
    )


def _text_assemble(plan: Plan) -> str:
    lines = ["План поездки:"]
    lines += [f"{_at(e.at)} — {e.title}" for e in plan.timeline]
    lines += [f"Внимание: {i.text.rstrip('.')}." for i in plan.issues]
    chosen = [o for o in plan.offers if o.kind == "hotel" and o.offer_id in plan.selected]
    if plan.spec.pets and any(not h.pets_allowed for h in chosen):
        lines.append(NO_PET_HOTEL)  # #556: the reminder stays on «Оформляем?»
    if _pet_flies(plan):
        lines.append(NO_PET_FLIGHT)
    lines += _unchecked_lines(plan.spec)
    total = _priced(_selected_total(plan), _chosen(plan))
    lines.append(f"Итого {total}. Оформляем? Напишите «да».")
    return "\n".join(lines)


def _text_blocked(plan: Plan) -> str:
    """#276: the timeline has an error, so the plan stays at compare. The fixes are offers of
    the plan (TRAP-4): a leg is named by its row in the compare table («рейс N»)."""
    by_id = {o.offer_id: o for o in plan.offers}
    rows = [o.offer_id for o in plan.offers if o.kind == "transport"]
    road = all(i.text.startswith(NO_ROAD) for i in plan.issues if i.severity == "error")
    why = "в плане нет дороги." if road else "в расписании ошибка стыковки."
    lines = [f"В корзину пока не кладу: {why}"]  # #532: no leg there or back is no docking
    for i in plan.issues:
        if i.severity != "error":
            continue
        lines.append(f"Ошибка: {i.text}")
        fixes = [
            (f"рейс {rows.index(x) + 1} — " if x in rows else "") + by_id[x].title
            for x in i.fixes
            if x in by_id
        ]
        if fixes:
            lines.append("Можно исправить: " + "; ".join(fixes) + ".")
    lines.append(HINTS["compare"])
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
        if not pets_possible(spec, find_offers(spec, d.name, estimate=True))
    ]


# #550: the pets dead end offers this many nearest places, and the trip without the pet.
PET_NEAR = 3
PET_DROP = "Без питомца"


def _pet_neighbours(spec: TripSpec) -> list[tuple[float, dict]]:
    """#550: the catalogue places nearest to the chosen one (catalog.km_between) where some trip
    takes the pet, up to 3, with the distance. Without the origin and the refused places.
    #575: and without the places of another kind than the wishes ask (no sea for «море»)."""
    here = resolve_place(spec.destinations[0])
    if here is None:
        return []
    refused = refused_before(spec)
    skip = {*spec.destinations, here["name"], spec.origin, *refused}
    hub, month = catalog.origin_hub(spec.origin or DEFAULT_ORIGIN)[0], _month(spec)
    out = []
    for p in sorted(catalog.places(), key=lambda p: catalog.km_between(here, p)):
        if p["name"] in skip or p.get("country") in refused:
            continue
        if _level(_rule_breaks(spec, p, hub, month)) >= 2:  # off the theme of the wishes
            continue
        trips = enumerate_trips(find_offers(spec, p["name"], estimate=True))
        if any(pets_ok(t) for t in trips):  # no trips is no way out (pets_possible says yes)
            out.append((catalog.km_between(here, p), p))
        if len(out) == PET_NEAR:
            break
    return out


def _pet_exits(plan: Plan, text: str) -> tuple[str, list[Question]]:
    """#550: the way out of a pets dead end: the nearest places where the pet goes (code, never
    invented; the cards are the options) and the same trip without the pet."""
    near = _pet_neighbours(plan.spec)
    if not near:
        text, questions = _no_offers(plan, text, _pet_dead_cities(plan.spec))
    else:
        hub, note = catalog.origin_hub(plan.spec.origin or DEFAULT_ORIGIN)
        plan.destinations = [card(plan.spec, hub, p, note) for _, p in near]  # «1» picks by order
        names = [p["name"] for _, p in near]
        where = ", ".join(f"{p['name']} (≈{round(km)} км)" for km, p in near)
        text += f" Ближайшие места, куда поездка с питомцем собирается: {where}."
        questions = [Question(text="Куда поедем вместо этого?", options=names)]
    text += f" Или напишите «{PET_DROP.lower()}» — соберу ту же поездку без него."
    return text, [*questions, Question(text="Едем без питомца?", options=[PET_DROP])]


def _pets_dead_end(plan: Plan) -> tuple[Stage, Plan, str, list[Question]]:
    """No trip takes the pet (#131): the reason, other cities where it can go, no cart.

    The legs that take the pet stay selected, a hotel is picked by the user (a number)."""
    spec = plan.spec
    text = dead_end_text(spec, plan.offers)
    if plan.selected:  # the hotels, as the user must see them to pick one
        head = _text_compare(plan).split("\nПредлагаю:")[0].split("\n")
        # #475: the dead end paragraph says it about the pets; no count line before it
        head = [x for x in head if not x.startswith(NO_PET_MARK)]
        text = "\n".join(head) + "\n" + text
        if not _hotel_selected(plan):
            text += "\n" + PET_NO_HOTEL
    text, questions = _pet_exits(plan, text)
    return ("compare" if plan.selected else "select"), plan, text, questions


# #550: a plan without a hotel is no plan to take: the reply says it plainly.
PET_NO_HOTEL = "Отеля в плане нет, поэтому «беру» не положит план в корзину."
PET_TODO = (
    "Что можно сделать: выбрать отель по номеру, сначала уточнив у отеля приём животных; "
    "поехать в другой город рядом; поехать без питомца."
)


def with_offers(names: list[str]) -> list[str]:
    """The names that have offers: the chip «План: город» exists only for them (#142)."""
    return [d for d in names if resolve_place(d) is not None]


def _unknown_place(plan: Plan, message: str, asked: list[Question]) -> tuple[str, list[Question]]:
    """A place that neither the catalogue nor the geocoder knows (TRAP-2, #88, #346).

    Its coordinates are unknown, so no city is «nearby». The country is read from the message
    («Ушуайя, Аргентина»; the answer «Аргентина» to the question): then the catalogue cities of
    that country are offered; a later message without a country keeps the one of the last question.
    Without a country the reply asks for it: no unrelated cities."""
    spec, place = plan.spec, plan.spec.destinations[0]
    text = (
        f"Направления «{place}» пока {NOT_IN_CATALOG}, "
        "и геокодер его не нашёл (или нет сети). Где это место, не знаю."
    )
    kept = next((m for q in asked if (m := re.search(r"страны «(.+?)»", q.text))), None)
    country = catalog.country_in(message) or (kept.group(1) if kept else None)
    skip = [spec.origin, *refused_before(spec)]
    found = [
        p
        for p in catalog.by_country(country or "")
        if p["name"] not in skip and resolve_place(p["name"]) is not None
    ][:3]
    names = [p["name"] for p in found]
    if not names:
        ask = f"В какой стране находится «{place}»?"
        if any(q.text == ask for q in asked):
            # The country was asked and not recognised (a region, a country outside the catalogue):
            # no dialog loop — the catalogue variants, said honestly to be unrelated to the place.
            text += " Страну по вашему ответу не определил: я узнаю только страну, не регион."
            text, questions = _no_offers(plan, text, [place])
            if questions:
                return text + " Они не обязательно рядом с этим местом.", questions
        hint = "Назовите страну — предложу город этой страны из каталога."
        return f"{text} {hint} Или выберите другое направление.", [Question(text=ask)]
    hub, note = catalog.origin_hub(spec.origin or "Москва")
    plan.destinations = [card(spec, hub, p, note) for p in found]  # «1» picks by this order
    text += (
        f" Раз это {country}, предлагаю города этой страны из каталога: {', '.join(names)}. "
        "Они не обязательно рядом с этим местом: расстояние до него я не знаю."
    )
    return text, [Question(text=f"Какой город страны «{country}» берём?", options=names)]


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


# An e-mail, or a phone with +7 or 8 and 10 digits, in a message after the booking (#367).
GUEST_DATA_RE = re.compile(r"\S+@\S+\.\w+|(?<!\d)(?:\+7|8)[\s()-]*(?:\d[\s()-]*){10}(?!\d)")

HINTS: dict[Stage, str] = {
    "select": "Выберите другое направление.",
    "compare": "Напишите «беру» или номер отеля; транспорт — «возьми поезд» или «рейс 2».",
    "assemble": "Напишите «да», чтобы положить план в корзину.",
    "checkout": (
        "Введите имя в форме и нажмите «Подтвердить бронь»; в чате «подтверждаю» оформит "
        "бронь без имени. В имитации данные гостя не обязательны."
    ),
}


PLAN_STAGES = ("inspire", "select", "compare", "assemble", "checkout")
PLAN3 = ("compare", "assemble", "checkout")  # a plan with offers exists


def no_change(found: dict, old: ChatReply) -> bool:
    """#182: «Сочи, если можно с детьми» at a plan to Сочи: the place named again is no edit."""
    return not found or (
        found.keys() == {"destinations"} and found["destinations"] == old.plan.spec.destinations
    )


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
    plan = remember_refusal(plan, {"out", "back"})
    gone = [o for o in plan.offers if o.kind == "transfer" and o.offer_id in plan.selected]
    if not gone:
        text = f"Трансфера в плане нет, менять нечего. {HINTS[old.stage]}"
        return old.model_copy(update={"plan": plan, "reply": text, "questions": [], "llm": False})
    cart, ids = old.cart, {o.offer_id for o in gone}
    if old.stage == "checkout":
        for o in gone:
            cart = edit_cart(plan, cart, "remove", o.offer_id)
        plan = sync_plan(plan, old.cart, cart)
    plan = plan.model_copy(update={"selected": [i for i in plan.selected if i not in ids]})
    plan.timeline, plan.issues = build_timeline(plan)
    rows = "; ".join(f"{o.title} — {_priced(o.price_rub, [o])}" for o in gone)
    head = f"Убираю трансфер из плана: {rows}."
    if old.stage == "assemble":
        text = f"{head} В корзину пока ничего не кладу.\n{_text_assemble(plan)}"
    else:
        text = f"{head} В корзине теперь {_priced(cart.total_rub, cart.items)}. {HINTS['checkout']}"
    return old.model_copy(
        update={"plan": plan, "cart": cart, "reply": text, "questions": [], "llm": False}
    )


def _with_origin(found: dict, name: str) -> None:
    """The origin is set; the same city read as a destination goes."""
    found["origin"] = name
    if "destinations" in found:
        found["destinations"] = [d for d in found["destinations"] if origin_name(d) != name]
        if not found["destinations"]:
            del found["destinations"]


def _said_origin(old: ChatReply, message: str, found: dict) -> str | None:
    """The departure city of this message, normalised (TRAP-3); it is set in `found`.

    «из Масквы» on any turn. A bare city («Питер», «мск») only answers a pending origin question,
    and only when no city of the trip is asked next to it (#436)."""
    asked = [q.text for q in old.questions] if old.stage == "clarify" else []
    other = {STAY_ASK, BOOKING_ASK} & set(asked)  # a city of the stay is asked: no bare origin
    pending = any(a.startswith(ORIGIN_ASK) for a in asked) and not other
    if found.get("origin"):
        if catalog.resolve(found["origin"]) is None and (name := origin_name(found["origin"])):
            found["origin"] = name
        name = found["origin"]
        if not (pending and _abroad(catalog.resolve(name)) and name in _trip_places(old, found)):
            return None  # «из Антальи» to an Antalya trip is checked below, as «Анталья»
    elif not pending:
        return None
    else:
        has_place = bool(old.plan.spec.destinations)
        country = catalog.country_of(message)
        if name := bare_origin(message, has_place):
            _with_origin(found, name)
        elif country and (has_place or country in _home_countries()):
            # «Россия» names no place; «Турция» to a Сочи trip does not replace Сочи (#442)
            if found.get("destinations") == [country]:
                del found["destinations"]
        elif country and "destinations" not in found:
            found["destinations"] = [country]  # «Турция» on «Хочу отдохнуть»: where to go
    abroad = name and _abroad(catalog.resolve(name)) and name in _trip_places(old, found)
    if abroad and any(SAME_PLACE in a for a in asked):
        found.pop("origin", None)  # the place abroad named twice: no origin, Москва is said aloud
        return None
    return name


SAME_PLACE = "— это и место поездки."
ORIGIN_DEFAULT_LINE = "Город вылета не назван — беру Москву."  # #453, DEFAULT_ORIGIN


def _trip_places(old: ChatReply, found: dict) -> set[str]:
    """The places of the trip after this message: its new destinations, else the old ones."""
    dests = found.get("destinations") or old.plan.spec.destinations
    return {(p or {}).get("name", d) for d in dests for p in [catalog.resolve(d)]}


def same_place_question(old: ChatReply, name: str, found: dict) -> list[Question]:
    """#442: the origin answer is the place of the trip («Казань на 3 ночи» -> «Казань»). It is
    asked once more. A place in Россия: a second «Казань» or «Из Казани» is the user's choice and
    goes on without a road. A place abroad: the plan always flies from a hub, so no «уже там» —
    the origin is asked again with the usual chips (_said_origin drops a second «Анталья»)."""
    if name not in _trip_places(old, found) or any(SAME_PLACE in q.text for q in old.questions):
        return []
    chips = [f"Из {w}" for w in ORIGIN_OPTIONS]
    if _abroad(catalog.resolve(name)):
        text = f"{ORIGIN_ASK} {name} {SAME_PLACE} Выберите город вылета или напишите «из …»."
        return [Question(text=text, options=chips)]
    here = f"Из {gen}" if (gen := _genitive(name)) else f"Из города {name}"
    text = (
        f"{ORIGIN_ASK} {name} {SAME_PLACE} Если вы уже там, дорога не нужна: нажмите «{here}». "
        "Если нет — выберите город вылета или напишите «из …»."
    )
    return [Question(text=text, options=[here, *(o for o in chips if o != here)])]


def _booking_edit(prev: ChatReply, message: str) -> str:
    """#503: the edits of the trip the user wrote after the voucher: the earlier ones from the
    chip question of prev, then this message when it edits the trip."""
    old = next((m.group(1) for q in prev.questions if (m := EDIT_NOTE_RE.search(q.text))), "")
    low = message.lower().replace("ё", "е")
    found, _ = extract(message, True)
    if EDIT_KEYS & set(found) or SHIFT_RE.search(low) or DATE_EDIT_RE.search(low):
        return f"{old}. {message}" if old else message
    return old


def _change_booking(prev: ChatReply, sid: str, use_llm: bool, origin: str | None) -> ChatReply:
    """#503: a new trip from the booked one: the same spec and plan, no cart, no voucher, plus
    the edit written before the press. The first booking stays: nothing is cancelled or rebooked."""
    edit = _booking_edit(prev, "")
    kept = (
        f"Бронь {prev.voucher.booking_id} остаётся оформленной: она не отменена и не переоформлена."
    )
    trip = prev.model_copy(
        update={"voucher": None, "cart": Cart(), "stage": "compare", "questions": []}
    )
    if edit:
        done = step(trip, sid, edit, use_llm, origin)
        head = "Новая поездка на тех же условиях с вашей правкой — отдельная покупка."
    else:
        done = trip.model_copy(update={"reply": _text_compare(trip.plan), "llm": False})
        head = "Новая поездка на тех же условиях — отдельная покупка. Правки пока нет."
    return done.model_copy(update={"reply": f"{head} {kept}\n{done.reply}"})


def step(
    prev: ChatReply | None, sid: str, message: str, use_llm: bool = False, origin: str | None = None
) -> ChatReply:
    """One user message -> the next ChatReply of the session.

    use_llm: ask the model (llm.py) when it is configured; this blocks on the network, so the
    caller runs it outside the store transaction.
    origin: the departure city a new session starts with (a restart keeps the named one, #436).

    «подтверждаю» at checkout raises _Confirm instead of a reply: the booking calls the
    provider and the store, so only chat() catches it and books (#382).
    """
    low = message.lower().replace("ё", "е")
    restart = RESTART_RE.search(message) and not NOT_RESTART_RE.search(low)
    if prev and restart and not REFUSE_RE.search(low):
        # The plan, the cart and the voucher go; what is left of the message starts the trip.
        rest = RESTART_RE.sub(" ", message).strip(" ,.!;:-")
        # #436: the departure city the user named stays; the default one is asked again.
        s = prev.plan.spec
        named = s.origin if not any(_label(a) == "откуда" for a in s.assumptions) else None
        fresh = step(None, sid, rest, use_llm, origin=named)
        kept = f" Бронь {prev.voucher.booking_id} остаётся оформленной." if prev.voucher else ""
        return fresh.model_copy(update={"reply": f"Начинаем заново.{kept} {fresh.reply}"})
    if prev and prev.voucher and CHANGE_RE.match(message):
        return _change_booking(prev, sid, use_llm, origin)
    if prev and prev.voucher:
        text = f"Бронь {prev.voucher.booking_id} уже оформлена."
        if GUEST_DATA_RE.search(message):  # #367: an e-mail or a phone after the booking
            text += " Контакт из сообщения в неё не добавлен."
            if prev.voucher.simulated:
                text += " Бронь имитирована."
        # #98: no dead end — another city or other dates mean a new trip; the booking stays
        text += f" {AFTER_BOOKING}"
        edit = _booking_edit(prev, message)
        ask = f"Что дальше? {EDIT_NOTE}{edit}»." if edit else "Что дальше?"
        chip = Question(text=ask, options=[CHANGE_CHIP, RESTART_CHIP])
        return prev.model_copy(update={"reply": text, "questions": [chip], "llm": False})
    old = prev or ChatReply(
        session_id=sid,
        stage="clarify",
        reply="",
        plan=Plan(spec=TripSpec(origin=origin)),
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
        text, used = _own_answer(_question_answer(old, low), message, use_llm)
        return old.model_copy(update={"reply": text, "questions": [], "llm": used})
    if (
        prev
        and old.stage in PLAN3
        and not rules_found  # an edit in the same message is a rebuild, as before
        and not (is_rebuild(low) or SHIFT_RE.search(low) or DATE_EDIT_RE.search(low))
        and (reply := _per_head_reply(old, low))
    ):
        return old.model_copy(update={"reply": reply, "questions": [], "llm": False})
    if (
        prev
        and old.stage in PLAN3
        and set(rules_found) <= {"destinations", "wishes", "modes"}  # «…чем 50 тысяч?» is a budget
        and not (is_rebuild(low) or SHIFT_RE.search(low) or DATE_EDIT_RE.search(low))
        and (reply := _price_versus_reply(old, low, rules_found))
    ):
        # #483: «дешевле, чем в Турции, с бассейном?» — the question changes nothing, the wish or
        # the one mode next to it is still an edit: the rest goes on without the comparison.
        if (
            set(rules_found.get("wishes", [])) - {"недорого"}
            or len(rules_found.get("modes", [])) == 1
        ) and (
            (rest := VERSUS_CLAUSE_RE.sub(" ", message).strip(" ,?"))
            and rest != message.strip(" ,?")  # a clause the regex does not cut would loop
        ):
            done = step(prev, sid, rest, use_llm, origin)
            head = reply.removesuffix(f" {HINTS[old.stage]}").split(" Чтобы")[0]
            head = head.removeprefix("Это вопрос: план не меняю. ")
            return done.model_copy(update={"reply": f"{head}\n{done.reply}"})
        return old.model_copy(update={"reply": reply, "questions": [], "llm": False})
    if no_transfer and not rules_found:
        return drop_transfers(old)
    # #186, #216: «какая погода в декабре?» at a December plan names no new field: still a
    # question. A question with a fact to give («погода?», «почему не Бульвар?», «почему не
    # поезд?») goes to no model, changes nothing, and is no transport pick.
    # #123: «какая погода в Анталии?» at a plan in Сочи is a question too: the place is its subject.
    fact = plan_fact(old.plan, low, message, rules_found) if prev and old.stage in PLAN3 else ""
    asked = without_plan_fields(rules_found, old.plan, low, message) if fact else rules_found
    if (
        fact
        and ASKING_NOTE not in rules_notes
        and (
            is_question(low, asked)
            or (is_hotel_compare(old.plan, low) and not EDIT_KEYS & set(asked))
        )
    ):
        text, used = _own_answer(_question_answer(old, low, message, rules_found), message, use_llm)
        return old.model_copy(update={"reply": text, "questions": [], "llm": used})
    pure = (
        set(rules_found) <= {"modes"}
        and not SHIFT_RE.search(low)
        and not (HYPOTHESIS_RE.search(low) and not CHOICE_RE.search(low))
    )
    if pure and prev and old.stage in ("compare", "assemble", "checkout"):
        row = hotel_row(message)
        plan, note = switch_transport(old.plan, message, row if old.stage == "compare" else None)
        if plan and row is not None and old.stage == "assemble":
            note = f"Отель уже выбран, сменить его можно на шаге сравнения. {note}"
        if no_transfer and old.stage == "assemble" and plan:  # «давай поездом, без трансфера»
            done = drop_transfers(old, plan)
            return done.model_copy(update={"reply": f"{note}\n{done.reply}"})
        if old.stage == "checkout" and (plan or note):
            note = "Корзина уже собрана, транспорт в ней не меняю."
            if row is not None:
                note = f"Отель уже выбран, сменить его можно на шаге сравнения. {note}"
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
        text, used = _own_answer(_question_answer(old, low, message), message, use_llm)
        return old.model_copy(update={"reply": text, "questions": [], "llm": used})
    if prev and old.stage in ("assemble", "checkout") and is_question(low, asked):
        # #81: «сколько ехать до моря?» is no wish: no model reads it, the DAG does not run.
        kids_only = asking and ASKING_NOTE in rules_notes and no_change(rules_found, old)
        text = _text_kids_question(old) if kids_only else _question_answer(old, low, message)
        text, used = _own_answer(text, message, use_llm)
        ask = [] if old.plan.spec.children_ages or not kids_only else kids_ask()  # #182
        return old.model_copy(update={"reply": text, "questions": ask, "llm": used})
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
        other = month_other(message, month_target) if month_verdict == "ask" else None
        month_note = month_reply(now, month_target, "", other)
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
    if asking and ASKING_NOTE in notes and no_change(found, old):
        ask = [] if old.plan.spec.children_ages else kids_ask()  # #182: no kids named: asked
        update = {"reply": _text_kids_question(old), "questions": ask, "llm": False}
        return old.model_copy(update=update)
    said_origin = _said_origin(old, message, found)  # #436, TRAP-3
    # #452: a country on «Откуда?» names where to go, not one place: the cards show its places
    # and the spec keeps the country (a note) until a place or another country is named.
    country_pool = None
    if (
        not said_origin
        and old.stage == "clarify"
        and any(q.text.startswith(ORIGIN_ASK) for q in old.questions)
        and (c := catalog.country_of(message))
        and len(dests := found.get("destinations") or []) == 1
        and (place := catalog.resolve(dests[0]))
        and place["country"] == c  # «Египет» extracts as Хургада: the one place of the country
        and catalog.by_country(c)
    ):
        found["destinations"], country_pool = [], c
    # #453: the place abroad named again on «Откуда?» is no origin either: Москва, said aloud
    origin_default = (
        not said_origin
        and old.stage == "clarify"
        and any(SAME_PLACE in q.text for q in old.questions)
        and not found.get("origin")
    )
    if said_origin and (again := same_place_question(old, said_origin, found)):
        # The other fields of the answer («Казань, трое взрослых») stay in the trip.
        rest = {k: v for k, v in found.items() if k != "origin" and k in TripSpec.model_fields}
        spec = TripSpec.model_validate({**old.plan.spec.model_dump(), **rest})
        plan = old.plan.model_copy(update={"spec": spec})
        update = {"plan": plan, "reply": again[0].text, "questions": again, "llm": False}
        return old.model_copy(update=update)
    if refused_month:
        found.pop("month", None)  # «не в январе», «отпуск до января»: no new month (#190)
        notes[:] = [n for n in notes if _label(n) != "когда"]
    # #184: «без перелёта», «на день дольше», «отель получше» at a built plan are edits, never
    # a consent. The spec edits go into `found`; the picks run on the plan after the DAG.
    # A question («сколько стоит ещё ночь?») is none of this: the question rules answer it.
    planned = (
        prev is not None
        and old.stage in PLAN3
        and bool(old.plan.offers)
        and (not asks_about(low) or (old.stage != "checkout" and asks_cheaper(low)))  # #310
    )
    cheaper = planned and asks_cheaper(low) and old.stage != "checkout"
    rebuild = planned and (is_rebuild(low) or cheaper)
    picks = pick_kinds(low) if planned else []
    lines: list[str] = []  # reasons there is nothing to change («уже поездом»)
    pinned = (
        rebuild_spec_edit(old.plan.spec, old.plan, low, found, notes, lines) if planned else None
    )
    if cheaper:
        lower_budget(old.plan, old.cart, found, notes)  # #278
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
    # #451: at inspire and select «ещё», «другие варианты» show the next places of the same pool.
    more = (
        prev is not None
        and old.stage in ("inspire", "select")
        and "destinations" not in found
        and bool(MORE_RE.match(low) or OTHER_RE.search(low) or OTHER_VARIANTS_RE.search(low))
    )
    other = more or (
        prev is not None
        and "destinations" not in found
        and bool(
            OTHER_RE.search(low)
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
    if llm_found and said_origin:
        _with_origin(llm_found, said_origin)  # «Питер» answered the origin question
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
            for k in ("nights", *DATE_KEYS):
                llm_found.pop(k, None)
    # Dates only the model read pass the check of the rules' dates (#164).
    past = llm_past_dates(llm_found) if llm_found else None
    full_dates = (
        bool(llm_found)
        and "date_from" in llm_found
        and bool({"date_to", "nights"} & llm_found.keys())
    )
    if past and DATE_KEYS & found.keys() and not full_dates:
        past = None  # the model's dates are dropped; the rules' dates stay, nothing to tell
    if past and past not in notes:
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
    if prev is None and children_uncounted(message) and NO_AGE_NOTE in notes:
        # #182: «с детьми» in the first message: no two children of 7, the question stands
        found.pop("children_ages", None)
        (llm_found or {}).pop("children_ages", None)
        notes[:] = [KIDS_UNKNOWN if n == NO_AGE_NOTE else n for n in notes]
    if prev is None and "adults" not in found and "adults" not in (llm_found or {}):
        notes.append(PARTY_UNKNOWN)
    if old.stage in ("inspire", "select") and idx is not None:
        # #458: a name beside the number wins: «третий: Александрия» after «ещё» is Александрия,
        # even if row 3 of the new batch is another place. A name that matches no shown place
        # and not the row asks which one (no guess).
        row = old.plan.destinations[idx].name if idx < len(old.plan.destinations) else None
        shown = [
            *(d.name for d in old.plan.destinations),
            *shown_before(old.plan.spec),
            *old.plan.spec.destinations,
        ]
        by_name = named_in(message, shown)
        said = found.get("destinations") or []
        if by_name:
            found["destinations"] = [by_name]
        elif said and (row is None or not named_in(row, said)):
            options = [d.name for d in old.plan.destinations]
            text = "Номер и название не совпадают. Какое направление берём?"
            q = Question(text="Какое направление?", options=options)
            return old.model_copy(update={"reply": text, "questions": [q], "llm": False})
        elif row:
            found["destinations"] = [row]
        (llm_found or {}).pop("destinations", None)  # the model does not see the numbered list
    # The departure city is no destination (#139): «Москва — Питер — Казань» starts in Москва.
    # It runs after the model too (#141): the model may return the departure city as a stay.
    start_default = not found.get("origin") and (
        not old.plan.spec.origin or any(_label(a) == "откуда" for a in old.plan.spec.assumptions)
    )

    told: list[str] = []

    def without_origin(f: dict) -> dict:
        f = drop_cheaper_wish(f, low) if prev is not None else f
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
    seen = (
        list(
            dict.fromkeys(
                [
                    *shown_before(spec),
                    *(d.name for d in old.plan.destinations),
                    *old.plan.spec.destinations,
                ]
            )
        )
        if more
        else []
    )
    spec = with_shown(spec, [])  # the note is set again below, by a «ещё» batch only
    country_pool = country_pool or country_before(old.plan.spec)  # #452: kept till a place is named
    if country_pool in refused:
        country_pool = None  # «не хочу в Турцию»: a refused country is no filter
    spec = with_country(spec, country_pool)  # a place named now drops the note
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
        if origin_default and plan.spec.origin == DEFAULT_ORIGIN:
            text = f"{ORIGIN_DEFAULT_LINE}\n{text}"
        text, kw["questions"] = with_month(plan.spec, text, kw.get("questions", []))
        return ChatReply(
            session_id=sid, stage=stage, reply=text, plan=plan, cart=cart, llm=used, **kw
        )

    if prev is None and (questions := blocking_questions(spec, message)):
        text = _text_clarify(questions)
        if use_llm and (own := llm.rewrite_questions(questions, deadline)):
            text, used = own, True  # checked against the question texts (TRAP-4)
        return reply("clarify", Plan(spec=spec), text, questions=questions)
    spec = assume(spec)
    if not spec.destinations:
        # 5: four are left without the origin. #451: «ещё» — the same spec, the pool without
        # every place shown in this session.
        # #452: every other country is excluded, as a refused one is (#113)
        skip = [c for c in catalog.snapshot()["countries"] if c != country_pool]
        skip = skip if country_pool else []
        dests = suggest_destinations(spec, 5, [*refused, *seen, *skip])
        no_country = None
        if country_pool and not dests and not more and not other:
            # Nothing of that country fits: say it, show the other places, drop the country.
            no_country, country_pool, skip = country_pool, None, []
            dests = suggest_destinations(spec, 5, [*refused, *seen])
        spec = with_country(spec, country_pool)
        if more:
            gone = old.plan.spec.destinations
            dests = [d for d in dests if d.name != spec.origin][:4]
            if not dests:
                # Nothing is left: the trip and the shown cards stay, «номер» picks from them.
                kept = with_shown(old.plan.spec, seen)
                plan = old.plan.model_copy(update={"spec": kept, "changes": []})
                options = [d.name for d in plan.destinations]
                question = Question(text="Куда поедем?", options=options)
                return reply("inspire", plan, relax_hint(spec), old.cart, questions=[question])
            spec = with_shown(spec, [*seen, *(d.name for d in dests)])
        elif other:
            gone = old.plan.spec.destinations
            ranked = suggest_destinations(spec, len(CATALOG), [*refused, *skip])
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
            sorry = (
                f"Под ваши условия в стране «{no_country}» мест нет. Показываю другие страны.\n"
                if no_country
                else ""
            )
            ask = children_question(spec) or party_question(spec)  # next to the options
            return reply("inspire", plan, sorry + _text_inspire(plan), questions=ask)
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
    said = found.get("origin")
    # C25 (#504): «из Кинешмы» maps to the same hub; the reply says so, not only the hint
    if (
        said
        and spec.origin == old.plan.spec.origin != said
        and (why := catalog.origin_hub(said)[1])
    ):
        lines.append(f"{why} План не меняю.")
    spec = end_agent_budget(old.plan.spec, spec, found)
    was = old.plan.spec
    party = (was.adults, was.children_ages) == (spec.adults, spec.children_ages)
    if party and agent_budget(was) and not agent_budget(spec) and "budget_rub" not in found:
        spec, line = refit_agent_budget(old.plan, old.cart, spec)  # #550: dates alone
        lines += [line] if line else []
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
            same = old.plan.spec.destinations == spec.destinations  # the country is of that place
            text, questions = _unknown_place(plan, message, old.questions if same else [])
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
        if cheaper and (line := cheaper_line(old.plan, plan, _other_edits(found))):
            lines.append(line)
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

    if cheaper and not plan_dirty:
        # The budget of the last step already holds: the plan is the cheapest one (#278).
        text = f"{cheaper_line(old.plan, old.plan)} {HINTS[old.stage]}"
        return old.model_copy(update={"reply": text, "questions": [], "llm": False})
    if (
        old.stage in ("compare", "assemble")
        and not (plan_dirty or picks or lines)
        and prev is not None
        and talks_cheaper(low)
    ):
        text = f"{cheaper_talk_line(old, low)} {HINTS[old.stage]}"  # #316
        return old.model_copy(update={"reply": text, "questions": [], "llm": False})
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
        if idx is not None and (by_title := named_in(message, [h.title for h in hotels])):
            idx = next(i for i, h in enumerate(hotels) if h.title == by_title)  # #458: name wins
        picks = idx is not None and idx < len(hotels)
        if not picks and not pets_possible(spec, plan.offers) and not _hotel_selected(plan):
            # «беру» is no consent (#131); #550: not the same refusal again, but what to do
            if not plan.selected:  # no leg takes the pet: the reason is the dead end itself
                stage, plan, text, questions = _pets_dead_end(plan)
                return reply(stage, plan, text, questions=questions)
            why = f"В корзину пока не кладу. Направление «{spec.destinations[0]}»: на эти даты "
            why += f"ни у одного отеля нет отметки о приёме животных. {PET_NO_HOTEL} {PET_TODO}"
            text, questions = _pet_exits(plan, why)
            return reply("compare", plan, text, questions=questions)
        if idx is not None and idx < len(hotels):
            # The hotel brings its own transfers for the selected legs (T7, #13).
            plan.selected = keep_refusal(
                plan, pick_hotel(plan.selected, plan.offers, hotels[idx].offer_id)
            )
            # «Почему» and the bundles name the picked hotel, not the ranked one (#58).
            plan.explanations = explain(plan.spec, plan.offers, plan.selected)
            plan.bundles = bundles(plan.spec, plan.offers, plan.selected)
        plan.timeline, plan.issues = build_timeline(plan)
        if any(i.severity == "error" for i in plan.issues):
            return reply("compare", plan, _text_blocked(plan))  # #276: no cart with an error
        if not _hotel_selected(plan):  # #532: a failed hotel source leaves nothing to stay in
            return reply("compare", plan, f"{NO_HOTEL} {HINTS['compare']}")
        return reply("assemble", plan, _with_tail(_text_assemble(plan), plan, low, message, found))
    if old.stage == "assemble" and yes:
        cart = fill_cart(plan)
        text = (
            f"В корзине {len(cart.items)} поз. на {_priced(cart.total_rub, cart.items)}, "
            f"оплата имитируется. {HINTS['checkout']}"
        )
        return reply("checkout", plan, _with_tail(text, plan, low, message, found), cart)
    if booking:  # chat() books outside the session lock; the user saw the stored cart total
        raise _Confirm(old.cart.total_rub)
    text = HINTS.get(old.stage, "Уточните запрос.")
    if old.stage == "select":
        if plan.offers and not pets_possible(spec, plan.offers):  # #550: near the pet's dead end
            text, questions = _pet_exits(plan, text)
        else:
            text, questions = _no_offers(plan, text, _pet_dead_cities(spec))
        return reply("select", plan, text, questions=questions)
    return reply(old.stage, plan, text)


def chat(req: ChatRequest, store: SessionStore) -> ChatReply:
    """POST /api/chat: an unknown session_id starts a new session with that id.

    The LLM runs before the store transaction: BEGIN IMMEDIATE locks the whole SQLite file,
    and a slow model must not hold the other sessions. The draft is written only if the
    session did not change meanwhile; otherwise the step runs again on the new state, rules
    only, inside the transaction.

    «подтверждаю» at checkout (step raises _Confirm) books through cart.checkout_reply, the
    path of POST /api/checkout: outside the store lock, with the attempt table (#382). The
    booking reply is a rule-based turn: llm is False.
    """
    sid = req.session_id or uuid4().hex
    started = time.monotonic()
    with ops.session(sid):
        prev = store.get(sid)
        try:
            with ops.collecting() as marks:  # #290: a failed provider is marked in the reply
                draft = ops.mark(step(prev, sid, req.message, use_llm=True), marks)

            def next_reply(cur: ChatReply | None) -> ChatReply:
                if cur == prev:
                    return draft
                with ops.collecting() as again:
                    return ops.mark(step(cur, sid, req.message), again)

            reply = store.update(sid, next_reply)
        except _Confirm as c:
            try:
                reply = checkout_reply(store, sid, c.total_rub).model_copy(update={"llm": False})
            except CartRefusal as e:  # the cart stays open; the reply says why
                why = {"reply": e.text, "questions": [], "llm": False}
                reply = store.update(sid, lambda cur: cur.model_copy(update=why))
        log.info(
            "chat stage=%s llm=%s marks=%d ms=%d",
            reply.stage,
            reply.llm,
            len(reply.plan.issues),
            (time.monotonic() - started) * 1000,
        )
        return reply


class _Confirm(Exception):  # a signal from step() to chat(), not an error
    """step() met «подтверждаю» at checkout; chat() books the cart of this total."""

    def __init__(self, total_rub: int):
        super().__init__(total_rub)
        self.total_rub = total_rub
