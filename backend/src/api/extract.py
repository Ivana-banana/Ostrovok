"""Rule extractor of TripSpec fields (T3, #7): no LLM, no network (TRAP-1).

extract(text) -> (found, notes): found holds only the TripSpec fields the message states;
notes are assumptions "<label>: text" made while reading it. model.merge drops a label
when a later message states its field. The LLM extractor (llm.extract_spec, T11 #15) returns
the same `found`; with_llm merges it: the model first, the rules keep what it did not say.
Invalid values (31 ноября, 0 ночей) are skipped with a note, never an exception.
"""

import functools
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from api import catalog
from api.fixtures import CATALOG, future_date
from api.guards import name_candidates
from api.offers import places, resolve_place

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}  # fmt: skip
MONTH = r"(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяе](?!\w)|июн\w*|июл\w*|август\w*" \
    r"|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"  # fmt: skip
MONTH_NAMES = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]  # fmt: skip
SEASONS = {"зимой": (12, 1, 2), "весной": (3, 4, 5), "летом": (6, 7, 8), "осенью": (9, 10, 11)}
NUMS = {
    "один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2, "двое": 2, "двух": 2, "двумя": 2,
    "три": 3, "трое": 3, "трех": 3, "тремя": 3, "четыре": 4, "четверо": 4, "четырьмя": 4,
    "пять": 5, "пятью": 5, "пятеро": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "двадцать": 20,
}  # fmt: skip
# #114: the English request (TRAP-3): month names, number words, the aliases of places.json.
MONTHS_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "sept": 9, "october": 10, "november": 11, "december": 12,
}  # fmt: skip
NUMS_EN = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10,
}  # fmt: skip
NUM_EN = rf"(\d{{1,2}}|{'|'.join(NUMS_EN)})"
# «may» is also a verb: it names a month only after a preposition.
MONTH_EN = (
    r"\b(?:(?:in|during|around|of|early|late|mid)\s+may"
    rf"|(?:{'|'.join(m for m in MONTHS_EN if m != 'may')}))\b"
)
NUM = rf"(\d{{1,2}}|{'|'.join(NUMS)})"
EDIT_VERB = (
    r"(?:перенес|перенос|сдвин|сдвиг|передвин|отлож|отодвин|подвин|продл|удлин|сократ|укороти)"
)
TOGETHER = {"вдвоем": 2, "втроем": 3, "вчетвером": 4, "впятером": 5}
# Places outside the fixture catalogue; T4 (#8) resolves them with the geocoder (TRAP-2).
PLACES = {
    "Москва": "москв", "Нижний Новгород": r"нижн\w*\s+новгород", "Тбилиси": "тбилис", "Батуми": "батум",
    "Грузия": "грузи", "Стамбул": "стамбул", "Екатеринбург": "екатеринбург",
    "Кисловодск": "кисловодск", "Пятигорск": "пятигорск", "Алтай": "алта[йяе]",
    "Байкал": "байкал", "Карелия": "карели", "Мурманск": "мурманск", "Абхазия": "абхази",
    "Анапа": "анап", "Геленджик": "геленджик", "Крым": "крым", "Ялта": "ялт",
    "Красная Поляна": r"красн\w*\s+полян", "Шерегеш": "шерегеш", "Ереван": "ереван",
    "Армения": "армени", "Минск": "минск", "Баку": "баку", "Ярославль": "ярославл",
    "Суздаль": "суздал", "Новосибирск": "новосибирск", "Владивосток": "владивосток",
}  # fmt: skip


# Nominative ending -> case endings. «Минеральная вода» is not «Минеральные Воды».
ADJECTIVE_FORMS = {
    ("ые", "ие"): "(?:ые|ие|ых|их|ым|им|ыми|ими)",
    ("ый", "ий", "ой"): "(?:ый|ий|ой|ого|его|ому|ему|ом|ем|ым|им)",
    ("ая", "яя"): "(?:ая|яя|ой|ей|ую|юю)",
}


def _word_re(word: str, first: str | None = None) -> str:
    """A word of a two-word name in any case: «Великого», «Воды» -> «воду».

    first: a pattern for its first letter («В»); None keeps the lower case letter."""
    w = word.lower().replace("ё", "е")
    rest = re.sub(r"[аяыиеийьюуо]+$", "", w) + r"\w*"
    for tails, forms in ADJECTIVE_FORMS.items():
        if w.endswith(tails):
            rest = w[:-2] + forms
    return (first or rest[0]) + rest[1:]


PLACE_RES = [
    (name, re.compile(rf"\b(?:{'|'.join(c['stems'])})\w*")) for name, c in CATALOG.items()
] + [(name, re.compile(rf"\b(?:{stem})\w*")) for name, stem in PLACES.items()]
# Checked on the original text: «Нижний» is a city, «на нижнем этаже» is not.
CAPITAL_PLACES = [("Нижний Новгород", re.compile(r"Нижн\w*\b(?!\s+этаж)"))]
# Two-word cities of places.json not listed above (TRAP-3): «Великий Новгород в мае». Their
# words are common («лечение минеральными водами»), so a name counts only with a capital first
# letter (original text) or right after «в/во/из/до» (lower text).
AFTER_PREPOSITION = r"(?:(?<=\bв )|(?<=\bво )|(?<=\bиз )|(?<=\bдо ))"


def _two_word_places() -> None:
    for p in places():
        if " " not in p["name"] or p["name"] in PLACES:
            continue
        words = p["name"].split()
        # the original text may have «ё»; the lower text has none
        cap = [w[0].upper() + _word_re(w)[1:].replace("е", "[её]") for w in words]
        cap[1:] = [f"[{w[0]}{w[0].lower()}]{w[1:]}" for w in cap[1:]]
        CAPITAL_PLACES.append((p["name"], re.compile(r"\b" + r"\s+".join(cap))))
        low = r"\s+".join(_word_re(w) for w in words)
        PLACE_RES.append((p["name"], re.compile(AFTER_PREPOSITION + low)))


_two_word_places()
# «Минводы», «Мин Воды» is the place «Минеральные Воды», not a second one (TRAP-3, #274).
PLACE_RES.append(("Минеральные Воды", re.compile(rf"\bминвод\w*|{AFTER_PREPOSITION}мин\s+вод\w*")))
NOT_PLACES = {"новый", "выходные", "майские", "праздники", "отель", "отпуск", "домой",
              "европа", "европе", "европу", "европы"}  # fmt: skip
# First word as a destination (TRAP-2). A city of places.json counts in any case and form.
# An unknown capitalized word counts only in main's narrow pattern: «Ухта, 3 ночи»,
# «Калуга в мае», «Тула на 3 дня». Even then the lists below veto ordinary first words.
FIRST_NEXT = re.compile(r"\s*,\s*\d|\s+(?:во?\s+" + MONTH + r"|[сн]а?\s+\d)")
NOT_CITY_START = re.compile(
    r"(?:хоч|хот|ищ|нуж|надо|подск|посов|подбер|подбир|помог|помощ|орган|спланир|планир|собир"
    r"|собер|давай|еду|едем|леч|лет[иеа]|поед|полет|мечт|привет|здравств|добр|отпуск|отел|билет"
    r"|тур\b|туры|турист|поезд|подум|интерес|сделай|создай|найд|покаж|расскаж|предлож|можн"
    r"|нельз|будем|будет|как\b|что\b|где\b|куд|когд|сколько|есть\b|если\b|для\b|мне\b|нам\b"
    r"|мы\b|ну\b|вот\b|так\b|или\b|тепл|хорош|нормал|красив|дешев|дорог|быстр|скоро|срочно"
    r"|лучш|любо|какой|какая|какое|зим|весн|осен|друз|компан|сем[ьъ]|одн|вмест"
    r"|нет\b|спасиб|отличн|ладно|понятно|ясно|дальше|далее|ок\b|окей|тогда|итого|ребят"
    r"|(?:другой|простой|большой|небольшой|выходной|мой|твой|свой|такой|мамой|папой|подругой"
    r"|другом|сыном|детьми|внуком|внучкой|коллегами|братом|родителями|подругами|племянником|дедушкой|отцом|дочкой|сестрой|бабушкой|мужем|женой|перенеси|подвинь|сдвинь)\b)"
)  # fmt: skip
# Not a city by its ending: verbs («Планируем», «Ищете»), adjectives («Тёплое»), infinitives.
# «ой» is not here: «Ханой» is a city. The ordinary words in «ой» that can stand before a
# number («Другой на 3 ночи», «Мамой на 3 дня») are a list of whole words in NOT_CITY_START;
# «Домой» is in NOT_PLACES.
NOT_CITY_END = re.compile(r"(?:ем|им|ете|ите|ешь|ишь|ют|ят|ться|ть|ый|ий|ая|яя|ое|ее|ые|ие)$")
CASES = "(?:а|у|е|ом|ем|ой|ы|и|ю|я|ь|ею|ам|ах|ами)"
CASES_SHORT = "(?:а|у|е|ы)"  # «Рим», «Вена»: a longer ending would make a common word
# Short stems that are ordinary words in these forms: «мала ли комната» is not «Мале».
NOT_SHORT_FORMS = {"мала", "малы", "бака"}
# A city that is also a person name (checked against all places.json; update it when the
# catalogue grows): «Владимир, хочу на море» is an address, not the city.
# Only a request verb after the comma makes it an address: «Владимир, 3 ночи» is the city.
PERSON_CITIES = {"Владимир"}
ADDRESS_NEXT = re.compile(
    r"\s*,\s*(?:хоч|хот|подскаж|помог|подбер|найд|посовет|скаж|слуш|мне\b|нам\b|мы\b|ищ|нуж"
    r"|добр|привет|здравств)"
)
# A length after a comma behind an unknown first word in a plan: «Ухта, 3 ночи». The form
# «<word> на 3 дня» is a rebuild command («Перенеси на 2 дня позже»), never a note.
LENGTH_NEXT = re.compile(r"\s*,\s*\d+\s*(?:ноч|дн|недел)")
# #337: an age adjective («годовалым ребёнком»), twins and triplets (a fixed count).
AGE_ADJ = {
    "полуторагодовал": 1, "годовал": 1, "двухлетн": 2, "двухгодовал": 2, "трехлетн": 3,
    "трёхлетн": 3, "четырехлетн": 4, "четырёхлетн": 4, "пятилетн": 5, "шестилетн": 6,
    "семилетн": 7, "восьмилетн": 8, "девятилетн": 9, "десятилетн": 10,
}  # fmt: skip
TWINS = {"близнец": 2, "близняшк": 2, "двойняшк": 2, "двойн": 2, "тройняшк": 3, "тройн": 3}
# Nouns only: «двойной номер», «тройная кровать» are adjectives, not children.
TWIN_RE = r"(?:близнец|близняшк|двойняшк|тройняшк)\w*|(?:двойн|тройн)(?:я|и|ей|ю|ями)(?!\w)"
CHILD_RE = re.compile(
    rf"\b(?:{NUM}\s+)?(?:(?P<adj>{'|'.join(AGE_ADJ)})\w*\s+)?"
    rf"(?:реб[её]н\w*|дет(?:и|ей|ьми|ям)\b|сын\w*|доч\w*|малыш\w*|внук\w*|внуч\w*"
    rf"|(?P<twin>{TWIN_RE}))"
)
# #195: «без детей», «без маленьких детей», «без мужа и детей», «без двоих детей» say there
# are no children. Between «без» and the child stand only «X и» or one word in -их/-ых
# («без пересадок двое детей 5 и 8 лет» has children). Only a genitive form can be refused.
NO_CHILD_BEFORE = re.compile(r"\bбез\s+(?:\w+\s+и\s+|\w+[иы]х\s+)?$")
# A number word or a number right after the child noun: the children are named. A negation
# later in the clause is a double negation («без детей не поедем»): the children stay.
NO_CHILD_AFTER = re.compile(r"\s*(?:\d|двое|трое|четверо|пятеро|шестеро)|[^,.;!?]*\b(?:не|ни\w*)\b")
NO_CHILD_FORM = ("детей", "ребенка", "сына", "дочери", "малыша")
# #112: the party. A number word names a person only in a few forms; «одна ночь», «один из
# вариантов», «один и тот же отель» are not a party. A solo word needs a stop, a preposition
# or a verb of the trip after it (or a verb of the trip before it).
SOLO_RE = re.compile(r"\b(?:один|одна|одной)\b")
SOLO_NEXT = re.compile(
    r"\s*(?:$|[,.;:!?)—–-]|(?:с|со|в|во|на|к|по|без|за|до|для|хочу|еду|поеду|лечу|полечу"
    r"|планирую|собираюсь|путешествую|отдыхаю)\b)"
)
SOLO_PREV = re.compile(
    r"\b(?:я|еду|поеду|лечу|полечу|ехать|лететь|поехать|слетать|съездить|отдохнуть|отдыхать"
    r"|путешествовать|воспитываю)\s+$"
)
SOLO_OTHER = re.compile(r"\bв\s+одиноч\w+|\b(?:на|для)\s+одного\b|\bсоло\b")
GROUP = {"двоих": 2, "троих": 3, "четверых": 4, "пятерых": 5, "шестерых": 6}
# «на троих» (not «на троих суток»), «нас 6 человек», «на 4 человека».
GROUP_RE = re.compile(
    rf"\b(?:на|для)\s+({'|'.join(GROUP)})\b(?!\s+(?:сут|ноч|дн|день|недел))"
    rf"|\b(?:на|для|из)\s+{NUM}\s+человек"
)
# With a child, these words say there are two adults at least («едем», «с мужем», «семьёй»).
PAIR_RE = re.compile(
    r"\bс\s+(?:мужем|женой|супруг\w*|парнем|мужчиной|мамой|папой|подругой|другом|сестрой"
    r"|братом|бабушкой|дедушкой)\b|\b(?:мы|нас|нам|наш\w*)\b"
    r"|\bсемь(?:я|е|и|ей|ю)\b|\bсемейн\w*|\b(?:едем|летим|поедем|полетим|хотим|планируем"
    r"|собираемся|отдыхаем)\b"
)
# #275: grown-up relatives and friends named in a list («я, муж, бабушка с дедушкой»): each is
# a traveller, «родители» are two. The speaker is one more.
ROLE_SRC = (
    r"(?:родител\w*|муж(?:ем|а|ья)?|жен(?:а|ой|ы)|супруг\w*|мам(?:а|ой|ы)|пап(?:а|ой|ы)"
    r"|бабушк\w*|дедушк\w*|сестр(?:а|ой|ы)|брат(?:ом|а)?|друг(?:ом|а)?|подруг(?:а|ой|и)"
    r"|коллег\w*|тещ\w*|тест(?:ь|ем)|свекр\w*)"
)
ADULT_ROLE_RE = re.compile(rf"\b{ROLE_SRC}\b")
# «for two», «for 3 adults»; not «for 3 nights».
EN_GROUP_RE = re.compile(
    rf"\bfor\s+{NUM_EN}\b(?!\s*-?(?:nights?|days?|weeks?|hours?|months?))|\bas a couple"
)
# #185: «трое друзей», «с двумя друзьями»: after «с» (or alone) the user and the friends travel,
# the friends are extra. «мы две подруги», «едем трое друзей», «трое друзей едем»: they are all.
FRIENDS_RE = re.compile(
    rf"\b{NUM}\s+(?:(?:мо(?:их|и|им|ими)|наш\w+)\s+)?"
    r"(?P<noun>друз(?:ей|ья|ьям|ьями|ьях)|подруг\w*|приятел\w+|коллег\w*)\b"
)
WITH_BEFORE = re.compile(r"\bсо?\s+$")
WHOLE_BEFORE = re.compile(
    r"\b(?:мы|едем|летим|поедем|полетим|отправимся|собираемся)\s+(?:\w+\s+)?$"
)
WHOLE_AFTER = re.compile(
    r"\s*,?\s*(?:едем|летим|поедем|полетим|хотим|собираемся|планируем|отдыхаем)\b"
)
FRIEND_NUMS = {1: "один", 2: "двое", 3: "трое", 4: "четверо", 5: "пятеро"}
FRIEND_NUMS_FEM = {1: "одна", 2: "две", 3: "три", 4: "четыре", 5: "пять"}
FRIEND_WORDS = {  # stem: (one, many)
    "друз": ("друг", "друзей"),
    "приятел": ("приятель", "приятелей"),
    "коллег": ("коллега", "коллег"),
}


def _friends(t: str, m: re.Match) -> tuple[int, str]:
    """(adults, note) for «трое друзей»: the user's word stays in the note."""
    n, noun = _num(m.group(1)), m.group("noun")
    before, after = t[: m.start()], t[m.end() : m.end() + 20]
    if noun.startswith("подруг"):
        said = f"{FRIEND_NUMS_FEM[n]} " if n in FRIEND_NUMS_FEM else f"{n} "
        said += "подруга" if n == 1 else "подруги" if n < 5 else "подруг"
    else:
        one, many = next(v for k, v in FRIEND_WORDS.items() if noun.startswith(k))
        said = f"один {one}" if n == 1 else f"{FRIEND_NUMS.get(n, n)} {many}"
    if (
        n >= 2
        and not WITH_BEFORE.search(before)
        and (WHOLE_BEFORE.search(before) or WHOLE_AFTER.match(after))
    ):
        return n, f"взрослых: {n} ({said} едут вместе)"
    return n + 1, f"взрослых: {n + 1} (вы и {said})"


# #322: «трое детей 3, 5 и 8» (no «лет»): a list of two or more numbers right after the child
# noun. A unit after it («5 ночей», «2 и 3 класса», «100 тысяч») says the numbers are not ages.
AGES_BARE_RE = re.compile(
    r"\s*\(?((?>(?:\d{1,2}\s*(?:,|и)\s*(?=\d))+)\d{1,2})\)?(?![\d.,]*\d)"
    r"(?![\s(]*(?:ноч|дн|сут|недел|тыс|к\b|₽|руб|р\b|класс|чел|взросл|%|:|-|числ|звезд|этаж|час"
    r"|мин|км\b|янв|фев|мар|апр|ма[йяе]\b|июн|июл|авг|сен|окт|ноя|дек))"
)
# #337: every number may carry its own unit: «1 год и 4 года», «6 месяцев и 3 года».
AGES_RE = re.compile(
    r"\D{0,12}?((?:\d{1,2}(?:\s*(?:лет|год|мес)\w*)?\s*(?:,|и)\s*)*\d{1,2})\s*(лет|год|мес)"
)
# «с детьми 5 лет и 8»: the first number has a unit, the last has none.
AGES_TAIL_RE = re.compile(
    r"\D{0,12}?(\d{1,2}\s*(лет|год|мес)\w*\s*(?:,|и)\s*\d{1,2})(?![\d.,]*\d)(?!\s*(?:лет|год|мес))(?=\s*(?:[.,;!?)]|$))"
    r"(?![\s(]*(?:ноч|дн|сут|недел|тыс|к\b|₽|руб|р\b|класс|чел|взросл|%|:|-|числ|звезд|этаж|час"
    r"|мин|км\b|янв|фев|мар|апр|ма[йяе]\b|июн|июл|авг|сен|окт|ноя|дек))"
)
# «сын 10.» / «дочь 3,»: one bare number closing the clause is the age of a single child.
AGE_ONE_RE = re.compile(
    r"\s*(\d{1,2})(?![\d.,]*\d)(?=\s*(?:[.,;!?)]|$))"
    r"(?![\s(]*(?:ноч|дн|сут|недел|тыс|к\b|₽|руб|р\b|класс|чел|взросл|%|:|-|числ|звезд|этаж|час"
    r"|мин|км\b|янв|фев|мар|апр|ма[йяе]\b|июн|июл|авг|сен|окт|ноя|дек))"
)
AGE_ITEM_RE = re.compile(r"(\d{1,2})(?:\s*(лет|год|мес))?")


def _ages_of(a: re.Match, one_child: bool = False) -> list[int]:
    """Ages of a matched list; a number without its own unit takes the last one.

    one_child: «1 год и 8 месяцев» after a single child word is one age in years."""
    ages = []
    prev = ""
    for n, unit in AGE_ITEM_RE.findall(a.group(1)):
        unit = unit or a.group(2)
        if one_child and prev in ("лет", "год") and unit == "мес":
            continue
        prev = unit
        if unit == "мес":
            if int(n) <= 23:
                ages.append(0)
        elif int(n) <= 17:
            ages.append(int(n))
    return ages


# #193: «дети с нами — двое, 5 и 8 лет»: a dash or a colon, an optional count word, the ages.
AGES_LIST_RE = re.compile(
    r"\s*(?:с\s+нами\s*)?[—–:-]\s*(?:[а-я]+\s*,\s*)?((?:\d{1,2}\s*(?:,|и)\s*)*\d{1,2})\s*(лет|год|мес)"
)
BUDGET_RES = [  # (pattern, multiplier); «к» counts only right after the number: «150к»
    (r"\b(\d+(?:[.,]\d+)?)\s*(?:млн|миллион)", 1_000_000),
    (r"\b(\d+(?:[.,]\d+)?)\s*(?:тыс|тр\b|т\.р)", 1000),
    (r"\b(\d+)[кk]\b", 1000),
    (r"\bтыс\w*\.?\s+(\d+)\b", 1000),
    (r"\b(\d{1,3}(?:\s\d{3})+|\d{4,7})\s*(?:₽|руб|р\b)", 1),
    (r"\bбюджет\w*\s*(?:до\s+|около\s+)?(\d{4,7})\b", 1),
]
BARE_HOTEL_RE = (  # «отель до 5000 за ночь»: no unit after the number, but a night after it
    r"\bдо\s+(\d{4,7})\b(?=\s*(?:за|в|/)\s*(?:ноч|сут))"
)
# #158: a budget in another currency is never read as rubles: (label, symbol, words).
CURRENCIES = [
    ("евро", "€", r"евро|eur"),
    ("долларах", "$", r"долл\w*|usd|бакс\w*"),
    ("фунтах", "£", r"фунт\w*|gbp"),
    ("юанях", "¥", r"юан\w*|cny"),
    ("лирах", "₺", r"лир(?:а|ы|ах|ам|у)?"),
    ("тенге", "₸", r"тенге|kzt"),
    ("дирхамах", "", r"дирхам\w*|aed"),
    ("йенах", "", r"йен\w*|jpy"),
]
AMOUNT_UNIT = r"\d(?:[.,]\d+)?\s?(?:[кk]|тыс\w*\.?|млн|миллион\w*)?\s*"
# #158: «полмиллиона», «потолок миллион»: a word sum. «Миллион» needs a budget word before it.
MILLION_CUE = (
    r"\b(?:бюджет\w*|потол\w+|лимит\w*|максимум\w*|до|около|порядка|не\s+(?:больше|более|дороже))"
)
MILLION_RES = [
    (r"\bпол(?:у)?миллиона\b", 500_000),
    (r"\bполтора\s+миллиона\b", 1_500_000),
    (MILLION_CUE + r"\s+(?:в\s+)?миллион\w*", 1_000_000),
]
# «50 тысяч шагов», «1000 км»: the number counts something else than money.
NOT_MONEY_RE = re.compile(
    r"^\w*\s+(?:шаг|км\b|километр|метр|человек|люд|жител|турист|раз\b|раза\b|отзыв|балл"
    r"|звезд|подписчик|лайк|просмотр|страниц|впечатл)"
)
# After the sum: «на каждого», «на человека» (not «на каждого ребёнка»); «в сутки», «за ночь».
PER_PERSON = (
    r"(?:(?:на|с|за|для)\s+(?:1\s+)?(?:кажд\w+\b(?!\s+(?:ребен|дет))|челов\w*|чел\b|персон\w+|душу"
    r"|взросл\w+|одного|нос\b|голов\w+)|кажд\w+\b(?!\s+(?:ребен|дет)))"
)
PER_NIGHT = (
    r"(?:(?:в|за)\s+(?:сутки|сут\b|ночь|день\b(?!\s+(?:рожд|отъезд|прилет|вылет|заезд)))"
    r"|на\s+ночь|/\s*(?:сут\w*|день|ночь)|посуточно)"
)
MEALS = [
    (r"вс[её]\s+включ|all\s*inclusive|олл\s*инклюзив", "AI"),
    (r"полупансион", "HB"),
    (r"полн\w*\s+пансион", "FB"),
    (r"без\s+(?:питания|завтрак)", "RO"),
    (r"завтрак", "BB"),
]
MODES = [
    ("train", r"\bпоезд(?!к)|\bж/?д\b|сапсан|ласточк"),
    ("car", r"\bна\s+(?:сво\w+\s+)?(?:машин|авто)"),
    ("flight", r"\b(?:самол[её]т|летим|лететь|полетим|рейс)"),
]
# «без самолётов», «не хочу лететь»: the flight is banned (the spec keeps the other modes)
NO_FLIGHT = re.compile(
    r"\b(?:без|не)\s+(?:на\s+)?(?!долг|длинн)(?:перелет\w*|самолет\w*|авиа(?!билет|компани|линии|перевоз)\w*)"
    r"|\bне\s+(?:хочу\s+|будем\s+|надо\s+)?(?:лететь|летим|полетим|летать)\b"
    r"(?!\s+(?:долг|дольше|больше|более|\d|с\s+пересад))"  # a long flight, not every flight
)
# #302: a meal or a mode refused after its name: «all inclusive не надо», «самолёт ни за что».
# «не надо» refuses only at the end of its clause: «поезд, не надо пересадок» keeps the train.
REFUSED_BEFORE_RE = re.compile(r"\b(?:не|без|никак\w*)\s+(?:\w+\s+)?$")
REFUSED_AFTER_RE = re.compile(
    r"\w*\s*(?:[—–:-]\s*)?(?:(?:мне|нам|точно|совсем|вообще)\s+)?"
    r"(?:ни\s+за\s+что|исключ\w*|отпада\w*|не\s+вариант|не\s+рассматрива\w*"
    r"|не\s+(?:надо|нужн\w*|нужен|хочу|хотим|хочется|подходит|интересн\w*|поеду|поедем|едем)"
    r"(?=\s*(?:[.,;!?)]|$)|\s+(?:и|а|но)\b))"
)
# #351: the fear of flying bans the flight only as a whole: «не боюсь летать», «аэрофобии нет»
# and «боюсь летать ночью» (a condition, not every flight) ban nothing.
FLIGHT_FEAR_RE = re.compile(
    r"аэрофоб\w*|бо(?:юсь|имся|ится|ятся)\s+(?:летать|лететь|самол[её]т\w*|перел[её]т\w*)"
)
FEAR_DENIED_BEFORE_RE = re.compile(r"\b(?:не|нет)\s+(?:\w+\s+)?$")
FEAR_DENIED_AFTER_RE = re.compile(r"\s+(?:нет|не\s+\w+)\b")
# «боюсь летать, но ради Бали готов»: a concession after the fear lifts the ban (#351).
FEAR_CONCEDED_RE = re.compile(
    r"[^.!?]*?\bно\b[^.!?]*?\b(?:готов\w*|можно|ладно|потерплю|соглас\w*|полечу|полетим|переживу)"
)
CLAUSE_END_RE = re.compile(r"\s*(?:[.,;!?)—–-]|$)|\s+(?:и|а|но|поэтому|так\s+что)\b")
# #114: «где тепло», «тёплые края», «жарко». «Тёплое море» is the sea wish (its water is checked),
# «не тепло», «не люблю жару» and «тёплая куртка» are no wish for the warm.
WARM = (
    r"\b(?:тепл(?!оход|\w*\s+(?:мор|одежд|куртк|вещ|одет|носк|шапк|плед))\w*"
    r"|жарк\w*|жар[аеуы]\b|погре(?:ться|тся|ем\w*+)(?!\s+(?:в\s+(?:бан|саун)|у\s+(?:камин|костр|печ)))|\bwarm\b|\bhot\b)"
)
# The warm is refused or only asked about: «не люблю жару», «жару не люблю», «жарко не надо»,
# «будет ли тепло?».
WARM_REFUSED_BEFORE = re.compile(r"\b(?:не|без|ли)\s+(?:\w+\s+)?$")
WARM_REFUSED_AFTER = re.compile(
    r"\s*(?:[,—–-]\s*)?(?:\w+\s+)?не\s+(?:люблю|любим|надо|нужн\w*|хочу|хотим|хочется|выношу"
    r"|переношу|перевариваю|подходит|нравится|нравятся|годится)"
)
WISHES = [
    ("море", r"\bмор[еяю]\b|\bморск|пляж|купат|\bsea\b|\bbeach"),
    ("недорого", r"недорог|деш[её]в|бюджетн|эконом|\bcheap"),
    ("горы", r"\bгор(?:ы|ах|ам|ами|у)\b|\bгорн|\bmountains?\b"),
    ("лыжи", r"лыж|сноуборд|\bski"),
    # #277: quiet and green words are the nature tag of the catalogue (api/inspire.py)
    (
        "природа",
        r"\bлес(?:а|у|ом|е|ах|ов)?\b|\bлесн|природ|\bозер(?:о|а|у|ом|е|ах)?\b",
    ),
    ("тепло", WARM),
    ("санаторий", r"санатор"),
    # #361: wishes the catalogue has no field for; the reply names them as not checked
    ("северное сияние", r"северн\w*\s+сияни|\bсияни\w*|\baurora\b|northern\s+lights"),
    ("киты", r"\bкит(?:ы|ов|ам|ами|ах|а)\b|\bwhales?\b"),
    ("термальные источники", r"термальн|\bтерм(?:ы|ах|ам)\b|горяч\w+\s+источник"),
    ("фестиваль", r"фестивал|карнавал"),
    ("дайвинг", r"дайвинг|дайвер|снорк"),
    ("серфинг", r"серфинг|серфить|кайтсерф|кайтинг"),
    ("рыбалка", r"рыбалк|рыбач"),
    ("сафари", r"сафари"),
    ("шопинг", r"шопинг|аутлет"),
    ("музеи", r"\bмузе[йяеи]\w*|выставк|\bтеатр"),
    ("йога", r"\bйог(?:а|и|е|у|ой)\b|\bйог-|ретрит"),
    (
        "тяжело ходить",
        r"(?:тяжело|трудно|сложно)\s+ход|без\s+лестниц|маломобил|на\s+коляске|инвалидн\w*\s+коляск|колясочник|кресл\w*[\s-]+коляск",
    ),
    # #338: hard conditions of a stay; no hotel field checks them, the reply says so
    ("детская коляска", r"детск\w*\s+коляск|\bс\s+коляск\w*"),
    ("веганское питание", r"веган"),
    ("вегетарианское питание", r"вегетариан"),
    (
        "поздний заезд",
        r"засел\w*(?:\s+\w+)?\s+(?:ночью|поздно|сразу)|засел\w*\s+после\s+(?:2[23]|0?[0-5])(?::\d\d|\s*(?:ч\b|час\w*|ночи|утра))|поздн\w*\s+(?:заезд|заселен)|ночн\w*\s+заезд",
    ),
    ("детская кроватка", r"кроватк|колыбел"),
    ("вино и еда", r"\bвин[оа]\b|\bвинн|гастро|\bеда\b|\bкухн"),
    ("с удобствами", r"удобств|палатк"),
    ("красиво", r"красив"),
    ("романтика", r"романт"),
    ("командировка", r"командиров"),
    # #20: region words narrow the inspiration (api/inspire.py); «Европа» is no destination
    ("Европа", r"\bевроп[аеуы]\b"),
    ("юг", r"\bна\s+юг\b"),
]
PRIORITY_RE = re.compile(r"\b(?:важн\w*|главн\w*|приоритет\w*|в первую очередь|прежде всего)")
PRIORITIES = [
    ("cancellation", r"отмен|возврат"),
    ("price", r"\bцен(?!тр)|деш[её]в|недорог|стоимост"),
    ("meal", r"питани|завтрак|вс[её]\s+включ"),
    ("location", r"центр|располож|\bрядом|\bблиз"),
    ("travel_time", r"в\s+пути|перел[её]т|переезд|\bдорог[аеу]\b"),
    ("rating", r"рейтинг|отзыв|зв[её]зд"),
]

# The exact message of the web priority chips (web/src/format.ts, priorityMessage).
CHIP_NAMES = ("цена", "отмена", "питание", "расположение", "время в пути", "рейтинг")
CHIP_RE = re.compile(
    rf"\s*мои приоритеты по важности:\s*(?:{'|'.join(CHIP_NAMES)})(?:,\s*(?:{'|'.join(CHIP_NAMES)}))*\.?\s*"
)


def is_chip_message(text: str) -> bool:
    """The chip phrase only: rules read all of it, the model has nothing to add (S9)."""
    return CHIP_RE.fullmatch(text.lower()) is not None


def _month(word: str) -> int:
    return next(n for s, n in MONTHS.items() if word.startswith(s))  # «март» before «ма»


# #338: hard conditions of a stay; no hotel field checks them, the reply says so
HARD_WISHES = (
    "детская коляска", "веганское питание", "вегетарианское питание", "поздний заезд",
    "детская кроватка",
)  # fmt: skip
NO_DATA_WISHES = (
    "северное сияние", "киты", "термальные источники", "фестиваль", "дайвинг", "серфинг",
    "рыбалка", "сафари", "шопинг", "музеи", "йога", *HARD_WISHES,
)  # fmt: skip
# «отель у Технопарка», «возле Технопарка»: the landmark is the next capitalized word. The lookahead
# needs \b: without it the word backtracks («у Чёрного моря» -> «Чёрног»).
LANDMARK_RE = re.compile(
    r"(?i:\b(?:(?:отел\w*|гостиниц\w*|жиль[её]|жить|остановиться)\s+(?:\w+\s+)?у|возле|около|рядом\s+с))"
    r"\s+([А-ЯЁ][\w-]+)\b(?!\s+(?:мор|озер))"
)
BREAKFAST_FROM_RE = re.compile(
    r"завтрак\w*\s+с\s+(\d{1,2})(?::(\d\d)|\s*(?:утра|ч\b|час(?:а|ов)?\b))"
)


def is_unchecked(wish: str) -> bool:
    """A wish of #338 that no data checks: the reply and the model must not call it met."""
    return wish in HARD_WISHES or wish.startswith(("рядом: ", "завтрак с "))


def _wished(wish: str, t: str, x: re.Match, negated: re.Pattern) -> bool:
    """The match is a wish: «не люблю жару» and «без лесов» are not."""
    if wish == "тепло":
        return not _warm_refused(t, x)
    if wish == "природа":
        return not negated.search(t[: x.start()])
    if wish in NO_DATA_WISHES:  # #361: «без музеев», «шопинг не нужен»
        return not _refused(t, x)
    return True


def _refused(t: str, m: re.Match) -> bool:
    """«без all inclusive», «самолёт ни за что»: the named thing is refused, not wanted (#302).

    Two refusals want it (#351): «без all inclusive не хочу», «без всё включено не поеду»."""
    before = bool(REFUSED_BEFORE_RE.search(t[: m.start()]))
    return before != bool(REFUSED_AFTER_RE.match(t, m.end()))


def _fears_flight(t: str) -> bool:
    """«боюсь летать», «аэрофобия»: no flight at all (#302); not denied, with no condition."""
    return any(
        not FEAR_DENIED_BEFORE_RE.search(t[: m.start()])
        and not FEAR_DENIED_AFTER_RE.match(t, m.end())
        and CLAUSE_END_RE.match(t, m.end())
        and not FEAR_CONCEDED_RE.match(t, m.end())
        for m in FLIGHT_FEAR_RE.finditer(t)
    )


def _meal(t: str) -> str | None:
    """The first wanted meal; a refused breakfast («завтрак не нужен») is room only."""
    refused = []
    for p, v in MEALS:
        xs = list(re.finditer(p, t))
        if v == "RO" and xs:
            # «без завтрака» names the refusal itself; «без завтрака не хочу» wants it (#351)
            return "BB" if all(REFUSED_AFTER_RE.match(t, x.end()) for x in xs) else v
        if any(not _refused(t, x) for x in xs):
            return v
        refused += [v] if xs else []
    return "RO" if "BB" in refused else None


def _warm_refused(t: str, m: re.Match) -> bool:
    return bool(WARM_REFUSED_BEFORE.search(t[: m.start()]) or WARM_REFUSED_AFTER.match(t, m.end()))


def _num(word: str) -> int:
    return int(word) if word.isdigit() else NUMS[word]


def _num_en(word: str) -> int:
    return int(word) if word.isdigit() else NUMS_EN[word]


def _place_at(text: str, t: str, pos: int) -> str | None:
    return next((name for name, rx in PLACE_RES if rx.match(t, pos)), None) or next(
        (name for name, rx in CAPITAL_PLACES if rx.match(text, pos)), None
    )


@functools.cache
def _stem_re(stem: str, short: bool) -> re.Pattern:
    # 445 places overflow the re module cache (512): compile each stem once (#232)
    return re.compile(re.escape(stem) + (CASES_SHORT if short else CASES))


def _known(word: str, exact_short: bool = False) -> str | None:
    """«Иркутске», «иркутска» -> «Иркутск» (TRAP-3): one-word names of places.json, any case.

    exact_short: a short name counts only as written, «Дела» is not «Дели» (#102)."""
    w = word.lower().replace("ё", "е")
    for p in places():
        n = p["name"].lower().replace("ё", "е")
        if " " in n:
            continue
        stem = n[:-1] if n[-1] in "аяьйеиыуо" else n
        short = len(stem) <= 3
        if w == n or _stem_re(stem, short).fullmatch(w):
            if short and w != n and (exact_short or w in NOT_SHORT_FORMS):
                continue
            return p["name"]
    return None


def _first_city(
    text: str, t: str, guess: bool, notes: list[str] | None = None
) -> tuple[int, str] | None:
    """The first word of the message as the destination: «Иркутск на неделю…», «Мурманск, …».

    A known city (places.json) is always taken. An unknown word is taken only if guess is on.
    With guess off (the plan has a city) «Ухта, 3 ночи» is no destination, but it is not silent:
    a note says the city is not in the catalogue."""
    m = re.match(r"\s*([А-ЯЁа-яё][а-яё]+(?:-[А-ЯЁа-яё]+)*)", text)
    if not m:
        return None
    word, low = m.group(1), t[m.start(1) : m.end(1)]
    if known := _known(word, exact_short=True):  # no preposition: «Дела такие» is no «Дели»
        if known in PERSON_CITIES and ADDRESS_NEXT.match(t, m.end()):
            return None
        return m.start(1), known  # a known city may be lower case: «иркутск на неделю»
    if guess and FIRST_NEXT.match(t, m.end()):
        return _unknown_first_city(text, t, m, word, low)
    # «Отдых на море», «Нет»: no guess, no note
    if (
        notes is not None
        and LENGTH_NEXT.match(t, m.end())
        and (city := _unknown_first_city(text, t, m, word, low))
    ):
        notes.append(f"направление: «{city[1]}» нет в каталоге, направление прежнее")
    return None


def _unknown_first_city(
    text: str, t: str, m: re.Match, word: str, low: str
) -> tuple[int, str] | None:
    """An unknown capitalized first word that no list of ordinary first words vetoes."""
    words_not_city = [low in NOT_PLACES, len(low) < 3, not word[0].isupper(), re.match(MONTH, low)]
    words_not_city.append(low in NUMS)
    if any(words_not_city) or NOT_CITY_START.match(low) or NOT_CITY_END.search(low):
        return None
    if any(re.search(p, low) for _, p in WISHES) or _place_at(text, t, m.start(1)):
        return None  # a wish word, or a place the other rules find anyway
    return m.start(1), _nominative(word)


# S5: «тогда Барселона», «лучше Сочи»: a lead word and a bare city, no preposition.
SWITCH_LEAD = re.compile(
    r"\s*(?:ну\s+|а\s+)?(?:тогда|лучше|ладно|пусть|давай(?:те)?|может(?:\s+быть)?|а\s+если)\W+"
    r"(?:(?:в|во|на)\s+)?",
    re.IGNORECASE,
)


def _switch_city(text: str) -> tuple[int, str] | None:
    """The bare city right after a lead word: «тогда Барселона», «Лучше Анталию».

    Only a city of places.json counts: «Лучше завтра», «Тогда Дети» name no place. A name
    outside the catalogue needs a preposition («тогда в Ухту») and is read by the rules below.
    """
    lead = SWITCH_LEAD.match(text)
    m = re.compile(r"[А-ЯЁа-яё]+(?:-[А-ЯЁа-яё]+)*\b").match(text, lead.end()) if lead else None
    known = _known(m.group(0)) if m else None
    return (m.start(), known) if m and known else None


def _vetoed_first_word(text: str, t: str, notes: list[str]) -> None:
    """#109: a first word before «на 3 дня» / «в мае» that the veto rejects leaves a neutral line.

    The word is not quoted: by form «Хотим» and «Иерусалим» look the same (TRAP-2)."""
    m = re.match(r"\s*([А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*)", text)
    if m and FIRST_NEXT.match(t, m.end()):
        low = t[m.start(1) : m.end(1)]
        if len(low) >= 3 and (NOT_CITY_START.match(low) or NOT_CITY_END.search(low)):
            notes.append("направление: не названо — если имели в виду город, напишите «в <город>»")


# #113: a place after a negation is refused, not wanted: «кроме Турции», «в Турции уже были».
# Countries are not destinations by themselves (the alias of places.json takes «Турция» to
# a city), so the refusal must know them: «Турцию и Египет муж отвергает» names two countries.
COUNTRY_STEMS = {"Египет": "египе?т", "Таиланд": "т(?:аи|ай)ланд"}


def _country_res() -> list[tuple[str, re.Pattern]]:
    names = {p["country"] for p in places()}
    out = []
    for name in sorted(names):
        if name in COUNTRY_STEMS:
            rx = COUNTRY_STEMS[name] + r"\w{0,3}"
        else:
            words = name.lower().replace("ё", "е").split()
            rx = r"\s+".join(re.sub(r"[аяйьыи]$", "", w) + r"\w{0,3}" for w in words)
        out.append((name, re.compile(rf"\b{rx}\b")))
    return out


COUNTRY_RES = _country_res()
COUNTRIES = {c for c, _ in COUNTRY_RES}
NEG_VERBS = (
    r"(?:хочу|хотим|хотел\w*|хочется|хотелось|надо|нужн\w*|нужен|нужна|поеду|поедем|еду|едем"
    r"|ехать|лететь|летим|лечу|полетим|полечу|полететь|поехать|отдыхать|люблю|любим|нравится"
    r"|интересует|рассматрива\w*|предлагай\w*|показывай\w*|выбира\w*|давай\w*|планируем|думаем)"
)
# Right before a place; «не дорого, в Турцию» is no match: only these words may stand between.
NEG_BEFORE = re.compile(
    rf"(?:\b(?:без|ни)\s+|\bне\s+(?:{NEG_VERBS}\s+)*"
    r"|\b(?:надоел\w*|отверга\w+|отвергн\w+|исключ\w+|вычеркн\w+|избега\w+|устал\w*\s+от)\s+)"
    r"(?:(?:в|во|на)\s+)?$"
)
# «кроме X» refuses X, but «кроме Сочи хотим ещё в Казань» adds Казань to Сочи.
EXCEPT_BEFORE = re.compile(r"\bкроме\s+(?:(?:в|во|на)\s+)?$")
ADDITION = re.compile(r"\b(?:еще|тоже|также|заодно)\b")
WANT_AFTER = re.compile(r"\b(?:хотим|хочу|хотел\w*|заедем|заехать|поедем|поехать|съездить)\b")
# «уже были» alone is no refusal («понравилось, хотим снова»): see _negated.
WEAK_BEFORE = re.compile(
    r"\bуже\s+(?:были|бывали|ездили|летали|отдыхали)\s+(?:там\s+)?(?:(?:в|во|на)\s+)?$"
)
WEAK_WORD = r"уже\s+(?:был\w*|бывал\w*|ездил\w*|летал\w*|отдыхал\w*|посетил\w*)"
WISH_AGAIN = re.compile(
    r"\b(?:снова|опять|еще\s+раз|понравил\w*|вернуться|вернемся|возвращ\w+|повторить)\b"
)
DISLIKE = re.compile(
    r"\bне\s+(?:хочу|хотим|хочется|хотелось|надо|нужн\w*|вариант|подходит|подойд\w*|нравит\w*"
    r"|интересует|едем|поедем|поеду|летим|полетим)"
    r"|\b(?:надоел\w*|надоест|хватит|отверга\w+|отвергн\w+|ненавид\w+|скучно|табу)\b"
    r"|\b(?:куда|что)-(?:то|нибудь|либо)\s+(?:нов\w+|друг\w+)"
)
# Right after a place, only a few light words in between and no comma: «Турция надоела».
AFTER_LEAD = (
    r"\s*(?:[—–-]\s*)?(?:(?:мы|я|они|там|уже|вс[её]|давно|тоже|муж|жена|мама|папа|нам|мне|нас|ему|ей"
    r"|совсем|категорически|точно|никак|вот)\s+){0,3}"
)
WEAK_AFTER = re.compile(AFTER_LEAD + WEAK_WORD)
NEG_AFTER = re.compile(
    AFTER_LEAD + r"(?:надоел\w*|надоест"
    r"|отверга\w+|отвергн\w+|исключ\w+|вычеркн\w+|ненавид\w+|табу|отпада\w+"
    r"|не\s+(?:хочу|хотим|хочется|хотелось|надо|нужн\w*|подходит|подойд\w*|нравится|нравятся"
    r"|интересует|вариант|едем|поедем|поеду|летим|полетим|рассматрива\w*|любим|люблю))"
)
# Places in one list share the refusal: «кроме Турции и Египта», «Турцию и Египет отвергает».
LIST_FORWARD = re.compile(r"\s*(?:,\s*(?:ни\s+)?|\s+и\s+|\s+или\s+|\s+ни\s+)(?:(?:в|во|на)\s+)?")
LIST_BACK = re.compile(r"\s+и\s+(?:(?:в|во|на)\s+)?")
SENTENCE_END = re.compile(r"[.!?;\n]")
# #127: «Турцию не хочу, Египет тоже»: the verb is dropped, the refusal goes on.
ELLIPSIS_GAP = re.compile(r"\s*(?:,\s*(?:(?:а|и)\s+)?|(?:а|и)\s+)(?:(?:в|во|на)\s+)?")
ELLIPSIS_TAIL = re.compile(r"\s*(?:тоже|также)(?=\s*(?:[,.!?;\n]|$))")


def _word_end(t: str, pos: int) -> int:
    m = re.compile(r"[\w-]+").match(t, pos)
    return m.end() if m else pos


def _sentence_span(t: str, start: int, end: int) -> tuple[int, int]:
    before = [m.end() for m in SENTENCE_END.finditer(t, 0, start)]
    stop = SENTENCE_END.search(t, end)
    return (before[-1] if before else 0), (stop.start() if stop else len(t))


def _sentence(t: str, start: int, end: int) -> str:
    """The sentence (up to «.!?;») around the mention."""
    a, b = _sentence_span(t, start, end)
    return t[a:b]


def _adds_place(t: str, spans: list[tuple[int, int]], i: int, refused: list[bool]) -> bool:
    """«Кроме Сочи хотим ещё в Казань»: the exception adds a place, it refuses nothing.

    It needs another place after X in the sentence (not in X's list, not refused itself)
    and a wish word or «ещё/тоже» in the sentence."""
    s, e = spans[i]
    j = i + 1
    while j < len(spans) and LIST_FORWARD.fullmatch(t[spans[j - 1][1] : spans[j][0]]):
        j += 1
    stop = SENTENCE_END.search(t, e)
    end = stop.start() if stop else len(t)
    later = [k for k in range(j, len(spans)) if spans[k][0] < end and not refused[k]]
    return bool(later) and bool(
        ADDITION.search(_sentence(t, s, e)) or WANT_AFTER.search(t, e, spans[later[0]][0])
    )


def _wanted_pair(t: str, spans: list[tuple[int, int]], i: int) -> bool:
    """#151: «Не хочу в Египет, Турция или Кипр»: a nominative after the comma names the wish.

    «в Египет» has the case of «в»; «Турция» (not «Турцию») cannot be in the refused list.
    The places of the «или» group after the comma are checked; «а/я» is the nominative sign."""
    if not re.fullmatch(r"\s*,\s*", t[spans[i - 1][1] : spans[i][0]]):
        return False
    if not re.search(r"\b(?:в|во|на)\s+$", t[: spans[i - 1][0]]):
        return False  # «кроме Египта, Кипра или Турции»: «Кипра» is no nominative
    group = [i]
    while group[-1] + 1 < len(spans) and re.fullmatch(
        r"\s+(?:или|либо)\s+", t[spans[group[-1]][1] : spans[group[-1] + 1][0]]
    ):
        group.append(group[-1] + 1)
    stop = SENTENCE_END.search(t, spans[group[-1]][1])
    rest = t[spans[group[-1]][1] : stop.start() if stop else len(t)]
    if len(group) < 2 or re.search(r"\b(?:тоже|также|нет|не)\b", rest):
        return False  # «Турция, Кипр», «Турция или Кипр тоже»: the refused list goes on
    return any(re.match(r"[\w-]*[ая]\b", t[spans[j][0] :]) for j in group)


def _mask(sentence: str, base: int, spans: list[tuple[int, int]]) -> str:
    """The sentence without the given absolute spans (blanked, the length stays)."""
    for a, b in spans:
        if base <= a < base + len(sentence):
            sentence = sentence[: a - base] + " " * (b - a) + sentence[b - base :]
    return sentence


def _negated(t: str, spans: list[tuple[int, int]], keys: list[str] | None = None) -> list[bool]:
    """Which of the place mentions (start, end; sorted) a negation refuses.

    «кроме X» refuses X unless the sentence adds a place («ещё в Казань»). «уже были в X» refuses
    X only with a dislike in the sentence («не хочется», «надоело») or another place wanted
    next to it, and never with «снова», «ещё раз», «понравилось». A marker stands for its own
    mention (#127): the dislike «в Анапу не хотим» and the wish «опять в Сочи» are not meant for
    the other places of the sentence. keys[i] is the name of the mention (the same place twice)."""
    n = len(spans)
    keys = keys or [t[s:e] for s, e in spans]
    strong = [False] * n
    own: list[list[tuple[int, int]]] = [[] for _ in range(n)]  # marker spans of the mention
    anchor: list[int | None] = [None] * n  # where the marker of the mention ends
    for i, (s, _) in enumerate(spans):
        w0 = max(0, s - 60)
        before = NEG_BEFORE.search(t[w0:s])
        strong[i] = bool(before)
        if before:
            own[i].append((w0 + before.start(), w0 + before.end()))
            anchor[i] = spans[i][1]
    exc = [bool(EXCEPT_BEFORE.search(t[max(0, s - 60) : s])) for s, _ in spans]
    weak = [bool(WEAK_BEFORE.search(t[max(0, s - 60) : s])) for s, _ in spans]
    for i in reversed(range(n)):
        e = spans[i][1]
        stop = SENTENCE_END.search(t, e)
        limit = min([stop.start() if stop else len(t)] + [x for x, _ in spans[i + 1 :] if x >= e])
        area = t[e:limit]
        for rx in (NEG_AFTER, WEAK_AFTER):
            after = rx.match(area)
            # «уже были в Турции»: the preposition right after the marker belongs to the next place
            if after and not re.fullmatch(r"\s+(?:в|во|на)\s*", area[after.end() :]):
                if rx is NEG_AFTER:
                    strong[i] = True
                    anchor[i] = e + after.end()
                    own[i].append((e, e + after.end()))
                else:
                    weak[i] = True
    # «Турцию не хочу, Египет тоже»: «тоже» with no verb goes on the refusal of the mention before.
    for i in range(1, n):
        gap = ELLIPSIS_GAP.fullmatch(t[anchor[i - 1] : spans[i][0]]) if anchor[i - 1] else None
        tail = ELLIPSIS_TAIL.match(t, spans[i][1]) if gap else None
        if tail and not strong[i]:
            strong[i] = True
            anchor[i] = tail.end()
            own[i].append((spans[i][1], tail.end()))
    neg = [False] * n
    for i in range(n):
        in_list = i > 0 and bool(LIST_FORWARD.fullmatch(t[spans[i - 1][1] : spans[i][0]]))
        in_list = in_list and not _wanted_pair(t, spans, i)
        neg[i] = (
            strong[i]
            or (exc[i] and not _adds_place(t, spans, i, strong))
            or (in_list and neg[i - 1])
        )
        weak[i] = weak[i] or (in_list and weak[i - 1])
        if in_list and neg[i - 1] and not own[i]:
            own[i] = own[i - 1]
    for i, (s, e) in enumerate(spans):
        if weak[i] and not neg[i]:
            base, stop = _sentence_span(t, s, e)
            sentence = t[base:stop]
            # The markers of the other refused places are not meant for this one.
            other = [
                x for j in range(n) if j != i and neg[j] and keys[j] != keys[i] for x in own[j]
            ]
            sentence = _mask(sentence, base, other)
            # A wish right before another place («опять в Сочи») is for that place only.
            wish = [
                m
                for m in WISH_AGAIN.finditer(sentence)
                if not any(
                    keys[j] != keys[i]
                    and re.fullmatch(r"\s*(?:(?:в|во|на)\s+)?", t[base + m.end() : spans[j][0]])
                    for j in range(n)
                    if j != i and spans[j][0] >= base + m.end()
                )
            ]
            wanted = any(
                j != i and not weak[j] and not neg[j] and keys[j] != keys[i] for j in range(n)
            )
            neg[i] = not wish and (bool(DISLIKE.search(sentence)) or wanted)
    # A list shares the refusal: «кроме Турции и Египта», «В Турции и Египте уже были».
    # Not when the same place is wanted again in the sentence: «были в Сочи и в Анапе, хотим в Сочи».
    for i in reversed(range(n - 1)):
        wanted_again = any(j != i and keys[j] == keys[i] and not neg[j] for j in range(n))
        if (
            neg[i + 1]
            and not wanted_again
            and LIST_BACK.fullmatch(t[spans[i][1] : spans[i + 1][0]])
        ):
            neg[i] = True
    return neg


def is_excluded(name: str, excluded: list[str]) -> bool:
    """The place, or the country of the place, is refused («Турция» refuses Анталия)."""
    place = resolve_place(name)
    country = place["country"] if place else None
    return name in excluded or country in excluded or bool(place and place["name"] in excluded)


def _other_city(place: dict, excluded: list[str]) -> str | None:
    """A city of the same country, with the sea or without it as the refused one."""
    return next(
        (
            p["name"]
            for p in places()
            if p["country"] == place["country"]
            and p["sea"] == place["sea"]
            and p["name"] != place["name"]
            and not is_excluded(p["name"], excluded)
        ),
        None,
    )


# #142: two names joined by «или»/«либо», each may stand after «в»: «Египет или Турция».
OR_PAIR = re.compile(
    r"([А-ЯЁа-яё]+(?:-[А-ЯЁа-яё]+)*)\s+(?:или|либо)\s+(?:(?:в|во|на)\s+)?([А-ЯЁа-яё]+(?:-[А-ЯЁа-яё]+)*)"
)


# #151: nouns of a request that are no place, also with a capital: «Экскурсии или Казань»,
# «Сочи или Дети». The capital of the first word of a sentence tells nothing.
NOT_PAIR_PLACE = re.compile(
    r"(?:экскурс|музе|прогулк|шопинг|театр|ресторан|развлеч|отдых|поход|рыбалк|дайвинг|серфинг"
    r"|аквапарк|зоопарк|достопримеч|концерт|фестивал|природ|взросл|реб[её]н|дет[иейяь]|малыш"
    r"|сын|доч|внук|семь)"
)
NAME_WORD = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*"
# The first words of two-word names outside the catalogue (lower, «е» for «ё»): «Новый Афон».
# A list, not adjective endings: «Летний Ханой» is no name.
NAME_LEADS = {
    "новый", "новая", "новое", "старый", "старая", "роза", "красная", "красный", "красное",
    "большой", "большая", "малый", "малая", "верхний", "великий", "белый", "белая", "черный",
    "золотой", "горный", "южный", "северный", "санта", "сан", "нью", "лас", "лос", "абу", "форт",
}  # fmt: skip


def _ordinary(word: str, low: str) -> bool:
    """A word that is no name of a place outside the catalogue."""
    ordinary = [low in NOT_PLACES, len(low) < 3, not word[0].isupper(), low in NUMS]
    ordinary += [bool(re.match(MONTH, low)), bool(NOT_CITY_START.match(low))]
    ordinary += [bool(NOT_PAIR_PLACE.match(low)), any(re.search(p, low) for _, p in WISHES)]
    return any(ordinary)


def _pair_name(word: str, low: str) -> str | None:
    """The place a word of a pair names: a known city, a country, or a capitalized unknown name."""
    if known := _known(word):
        return known
    if country := next((c for c, rx in COUNTRY_RES if rx.fullmatch(low)), None):
        return country
    return None if _ordinary(word, low) or NOT_CITY_END.search(low) else _nominative(word)


def _two_word_name(text: str, t: str, s: int, e: int, side: int) -> tuple[int, str, int] | None:
    """«Роза Хутор или Сочи», «Сочи или Новый Афон»: two capitalized words are one name.

    The first word may be an adjective («Новый»), the second one is checked as a pair word."""
    if side == 0:
        m = re.search(rf"(?:^|[\s,(])({NAME_WORD}) $", text[:s])
    else:
        m = re.compile(rf" ({NAME_WORD})\b").match(text, e)
    if not m or not text[s].isupper():
        return None
    (a, b), (hs, he) = ((m.start(1), m.end(1)), (s, e)) if side == 0 else ((s, e), m.span(1))
    first, head = text[a:b], t[hs:he]
    if t[a:b] not in NAME_LEADS or any(rx.fullmatch(head) for _, rx in COUNTRY_RES):
        return None  # «Отпуск Ханой», «Ханой Пожалуйста», «Ханой Вьетнам»
    if (name := _pair_name(text[hs:he], head)) is None:
        return None
    return a, f"{first} {name}", he


def _or_pairs(text: str, t: str, hits: list) -> set[int]:
    """Add the names of «X или Y» that the other rules miss; one side must be a place for sure.

    «Египет или Турция»: Египет is a place of the catalogue, Турция has no stem of its own.
    «Ханой или Сочи»: Ханой is outside the catalogue and has no preposition. «Привет или
    Здравствуйте» names no place. Returns the starts of the pair words (see _places)."""
    sides = set()
    for m in OR_PAIR.finditer(text):
        words = [(m.start(i), m.end(i), m.group(i)) for i in (1, 2)]
        if any(t[s] == " " for s, _, _ in words):
            continue  # a word of the origin span: «из Казани или Самары» is no pair of places

        def taken(s: int) -> bool:
            return any(hs <= s < he for hs, _, he in hits)  # inside «Нижний Новгород» too

        sure = [
            taken(s) or _known(w) or any(rx.fullmatch(t[s:e]) for _, rx in COUNTRY_RES)
            for s, e, w in words
        ]
        if not any(sure):
            continue
        for side, (s, e, w) in enumerate(words):
            sides.add(s)
            if taken(s):
                continue
            two = _two_word_name(text, t, s, e, side)
            if two and not taken(two[0]) and not taken(two[2] - 1):
                sides.add(two[0])
                hits.append(two)
            elif name := _pair_name(w, t[s:e]):
                hits.append((s, name, e))
    return sides


TRANSFER = re.compile(r"с\s+пересадкой\s+(?:в|во|на)\s+$")
VIA = re.compile(r"через\s+$")


def _transit_dropped(t: str, hits: list) -> list:
    """Hits without transit points (#274): the place after «с пересадкой в», or after «через» when
    it has its own airport that serves another named place («через Минводы» to Кисловодск, MRV).
    «через Нижний в Сочи» is a route stop and stays. The last place is never dropped."""

    def iata(name: str) -> str | None:
        p = resolve_place(name)
        return (p or {}).get("airport", {}).get("iata")

    out = []
    for h in hits:
        before = t[: h[0]]
        own = ((resolve_place(h[1]) or {}).get("airport") or {}).get("km", 99) <= 25  # own airport
        hub = (
            VIA.search(before)
            and own
            and iata(h[1])
            and any(
                o[1] != h[1] and not VIA.search(t[: o[0]]) and iata(o[1]) == iata(h[1])
                for o in hits
            )
        )
        if TRANSFER.search(before) or hub:
            continue
        out.append(h)
    return out or hits


def _places(text: str, t: str, found: dict, notes: list[str], has_destination: bool) -> list[str]:
    """Origin after «из/от», then destinations in text order: known places and capitalized names.

    A place after a negation is no destination (#113). Returns the refused places and countries."""
    for m in re.finditer(r"\b(?:из|от)\s+(?=\w)", t):
        w = re.match(r"[а-яе-]+", t[m.end() :])
        if re.search(r"устал\w*\s+$", t[: m.start()]):
            continue  # «устали от Турции» is a refusal, not an origin
        if name := _place_at(text, t, m.end()) or _capital(text, m.end()) or (w and _known(w[0])):
            break
    else:
        m = None
    if m:
        if home := catalog.respelled(name):
            name = home["name"]  # «Ебург» -> Екатеринбург (#274)
        found["origin"] = name
        end = t.find(" ", m.end())
        end = len(t) if end < 0 else end
        t = t[: m.start()] + " " * (end - m.start()) + t[end:]  # the origin is not a destination
    hits = [(m.start(), name, m.end()) for name, rx in PLACE_RES for m in rx.finditer(t)]
    hits += [(m.start(), name, m.end()) for name, rx in CAPITAL_PLACES for m in rx.finditer(text)]
    spans = []
    for m in re.finditer(r"(?:^|[\s,(])(?:[вВ]о?|[нН]а)\s+(?=[А-ЯЁ])", text):
        free = t[m.end()] != " " and not _place_at(text, t, m.end())  # " ": the origin span
        if free and (span := _capital_span(text, m.end())):
            hits.append((m.end(), *span))
            spans.append((m.end(), span[1]))
    # «Горный Алтай» is one name: the place «Алтай» inside its span is not a second destination.
    hits = [h for h in hits if not any(a < h[0] < e for a, e in spans)]
    for m in re.finditer(r"(?:^|[\s,(])(?:во?|на)\s+([а-яе-]+)", t):  # «в иркутск», «в Иркутске»
        if (
            t[m.start(1)] != " "
            and not _place_at(text, t, m.start(1))
            and (n := _known(m.group(1)))
        ):
            hits.append((m.start(1), n, m.end(1)))
    pair_sides = _or_pairs(text, t, hits)
    if switch := _switch_city(text):
        hits.append((*switch, _word_end(t, switch[0])))
    # A real place in the text («Музеи в Петербурге») or in the plan makes the guess unneeded.
    if first := _first_city(text, t, not hits and not has_destination, None if hits else notes):
        hits.append((*first, _word_end(t, first[0])))
    elif not hits and not has_destination:
        _vetoed_first_word(text, t, notes)
    # A transit point is no destination (#274): «через Минводы», «с пересадкой в Москве».
    hits = _transit_dropped(t, hits)
    # Mentions for the negation: the places above and the bare countries («Турцию и Египет…»).
    # The refused name is the country when the word is one: «египет» is a hit of Хургада.
    mentions = {s: (e, name, name) for s, name, e in hits}
    for country, rx in COUNTRY_RES:
        for m in rx.finditer(t):
            e, name, _ = mentions.get(m.start(), (m.end(), None, None))
            mentions[m.start()] = (max(e, m.end()), name, country)
    starts = sorted(mentions)
    neg = _negated(t, [(s, mentions[s][0]) for s in starts], [mentions[s][2] for s in starts])
    refused_at = {s for s, n in zip(starts, neg, strict=True) if n}
    refused = list(dict.fromkeys(mentions[s][2] for s in starts if s in refused_at))
    dests = []
    for s, name, _ in sorted(hits):
        if s in refused_at or name == found.get("origin"):
            continue
        if s in pair_sides and name in COUNTRIES and (city := resolve_place(name)):
            name = city["name"]  # «Египет или Турция»: the city of the catalogue for the country
        place = resolve_place(name)
        if place and place.get("source") == "online":
            name = place["name"]  # the geocoder's name: «Бледе» is the place «Блед» (TRAP-3)
        elif fixed := catalog.respelled(name):
            name = fixed["name"]  # «Ебург», «Кисловоцк» are the places of the catalogue (#274)
        if s in pair_sides and place and place["country"] in refused:
            continue  # «Турция или Египет, в Египте уже были»: no Хургада (#151)
        if place and mentions[s][2] in COUNTRIES and place["name"] in refused:
            # The country is named, its city is refused: another city of the country.
            alt = _other_city(place, refused)
            if alt is None:
                continue
            notes.append(f"направление: «{place['name']}» исключена, вместо неё {alt}")
            name = alt
        if name not in dests:
            dests.append(name)
    if dests:
        found["destinations"] = dests
    return refused


def excluded_places(text: str) -> list[str]:
    """Places and countries the message refuses («кроме Турции», «в Египте уже были»), #113."""
    return _places(text, text.lower().replace("ё", "е"), {}, [], True)


# The first word of a name outside the catalogue that is an adjective in a case: «Старую
# Ладогу», «Чешском Крумлове», «Новой Ухты». NAME_LEADS adds the nominative («Новый Афон»).
ADJ_CASE = re.compile(r"(?:ую|юю|ой|ей|ого|его|ом|ем|ым|им|ая|яя|ый|ий|ое|ее)$")


def _capital_span(text: str, pos: int) -> tuple[str, int] | None:
    """A capitalized name: kept as written, «Грузию» -> «Грузия»; and where it ends in the text.

    Two capitalized words are one name when the first is an adjective: «Старую Ладогу» ->
    «Старая Ладога» (TRAP-3). The geocoder gets the whole name, not «Старую»."""
    m = re.match(r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*", text[pos:])
    if not m or m.group(0).lower() in NOT_PLACES or re.match(MONTH, m.group(0).lower()):
        return None
    word = m.group(0)
    end = pos + m.end()
    low = word.lower().replace("ё", "е")
    country = next((c for c, rx in COUNTRY_RES if rx.fullmatch(low)), None)
    if known := _known(word) or country:  # «Египте» is Египет, not a new city
        return known, end
    second = re.compile(rf" ({NAME_WORD})(?![а-яё-])").match(text, end)
    if second and (ADJ_CASE.search(low) and len(low) > 3 or low in NAME_LEADS):
        tail, tail_low = second.group(1), second.group(1).lower().replace("ё", "е")
        if not (_ordinary(tail, tail_low) or NOT_CITY_END.search(tail_low) or _known(tail)):
            return name_candidates(f"{word} {tail}")[0], second.end()
    return _nominative(word), end


def _capital(text: str, pos: int) -> str | None:
    span = _capital_span(text, pos)
    return span[0] if span else None


def _nominative(word: str) -> str:
    """Unknown names: «Грузию» -> «Грузия», «Иркутске» -> «Иркутск», «Аляске» -> «Аляска».

    Other endings stay: «Малибу», «Сан-Паулу» are no accusative. The geocoder asks the name as
    written first and then the nominative guesses (catalog.lookup_online, TRAP-3)."""
    word = re.sub(r"и[ию]$", "ия", word)
    if re.search(r"[аеёиоуыэюя]ск[еу]$", word):
        return word[:-1] + "а"  # «Аляске» -> «Аляска»; «Иркутске» has a consonant before «ск»
    return re.sub(r"(ск|цк|зк|град|бург|город)(?:е|а|у|ом)$", r"\1", word)


# The English names of places.json (its ASCII aliases) and the common spellings of the capital.
EN_EXTRA = {"st. petersburg": "Санкт-Петербург", "st petersburg": "Санкт-Петербург",
            "petersburg": "Санкт-Петербург", "saint-petersburg": "Санкт-Петербург"}  # fmt: skip


def _english_places(t: str, found: dict) -> None:
    """«from Moscow» is the origin, any other known name is a destination (#114, TRAP-3).

    Only what the Russian rules did not find; only the names of places.json."""
    names = {a: p["name"] for p in places() for a in p["aliases"] if a.isascii()} | EN_EXTRA
    rx = re.compile(
        r"\b(from\s+)?(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\b"
    )
    dests = []
    for m in rx.finditer(t):
        name = names[m.group(2)]
        if m.group(1):
            found.setdefault("origin", name)
        elif name not in dests:
            dests.append(name)
    if dests and "destinations" not in found:
        found["destinations"] = [d for d in dests if d != found.get("origin")]
        if not found["destinations"]:
            del found["destinations"]


WEEKDAYS = ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье")


PAST_MARK = "уже прошло"  # in the note about a named day moved to next year
PAST_GONE_MARK = "уже прошли"  # in the note about model dates too far back to take
PAST_WINDOW = timedelta(days=31)  # only a day this recent counts as a slip


def is_past_note(assumption: str) -> bool:
    return PAST_MARK in assumption or PAST_GONE_MARK in assumption


def past_year_note(day: date, year: int) -> str:
    return f"даты: {day:%d.%m} {PAST_MARK} — взят {year} год"


def llm_past_dates(llm_found: dict, today: date | None = None) -> str | None:
    """Model dates go through the same check as the rules' dates (#164); returns the note.

    A day in the last PAST_WINDOW moves to next year, the length stays (#159).
    Dates further back are not taken: the model's dates leave `llm_found`.
    """
    start = llm_found.get("date_from")
    today = today or date.today()
    if start is None or start >= today:
        return None
    if start >= today - PAST_WINDOW:
        try:
            moved = start.replace(year=start.year + 1)
        except ValueError:  # 29 February
            moved = start.replace(year=start.year + 1, day=28)
        end = llm_found.get("date_to")
        llm_found["date_from"] = moved
        if end is not None:
            llm_found["date_to"] = end + (moved - start)
        if "month" in llm_found:
            llm_found["month"] = moved.month
        return past_year_note(start, moved.year)
    for k in DATE_KEYS:
        llm_found.pop(k, None)
    return f"даты: от модели {start:%d.%m.%Y} {PAST_GONE_MARK} — не применены"


# #362: «в конце января» is the end of the month. The day is inside the named part (check-in may
# run past the month end): start 5th, middle 15th, end 25th.
MONTH_PART = re.compile(
    rf"\b(?:(?:ближе\s+к|под)\s+)?(конец|конц\w*|начал\w*|середин\w*|перв\w+\s+числ\w*"
    rf"|последн\w+\s+числ\w*)\s+{MONTH}"
)
MONTH_PART_DAY = (("нач", 5, 10), ("перв", 5, 10), ("сер", 15, 20))  # (stem, day, last day)


def _month_part(t: str, today: date) -> tuple[date, str] | None:
    """Check-in date and the said words for «в конце января»; None if no part is named."""
    if re.search(r"\bвыходн|\bуик[-\s]?[эе]нд", t) or not (m := MONTH_PART.search(t)):
        return None
    month = _month(m.group(2))
    day, last = next(((d, e) for s, d, e in MONTH_PART_DAY if m.group(1).startswith(s)), (25, 31))
    start = future_date(month, day, today)
    if month == today.month and start.year > today.year and today.day < last:
        start = today + timedelta(days=1)  # the part is under way: tomorrow is still inside it
    return start, m.group(0)


# The day of the return after the start: «улетаем 18-го», «обратно 3 декабря»; not «в 18:30».
BACK_DAY_RE = re.compile(
    rf"\b(?:улета\w*|уезжа\w*|возвраща\w*|обратно)\s+(\d{{1,2}})(?![\d:.]|\s*(?:ноч|дн|сут|недел|час|чел))(?:-?(?:го|е)\b)?"
    rf"(?:\s+{MONTH})?"
)


def _dates(t: str, found: dict, notes: list[str], kept: int = 7) -> None:
    """«11–13 декабря», «с 30 ноября по 3 декабря», «с 20 ноября»; month without a day.

    kept: the length of the plan now; a range over MAX_NIGHTS is named and not taken (#168)."""
    d = r"(\d{1,2})(?:-?го)?"
    if m := re.search(rf"\b{MONTH}", t):
        found["month"] = _month(m.group(1))
    elif m := re.search(r"\bмайск", t):
        found["month"] = 5
    elif m := re.search(MONTH_EN, t):
        found["month"] = MONTHS_EN[m.group(0).split()[-1]]
    elif m := re.search(rf"\b({'|'.join(SEASONS)})\b", t):
        months = SEASONS[m.group(1)]
        found["month"] = date.today().month if date.today().month in months else months[0]
        notes.append(f"когда: {MONTH_NAMES[found['month'] - 1]} («{m.group(1)}»)")
    two = re.search(rf"\b{d}\s+{MONTH}\s*(?:[-–—]|по|до)\s*{d}\s+{MONTH}", t)
    one = re.search(rf"\b{d}\s*(?:[-–—]|по|до)\s*{d}\s+{MONTH}", t)
    if two:
        d1, m1, d2, m2 = two.groups()
    elif one:
        d1, d2, m1 = one.groups()
        m2 = m1
    elif m := re.search(rf"\b{d}\s+{MONTH}", t):
        (d1, m1), d2, m2 = m.groups(), None, None
        # C5 (#363): «прилетаем 14 ноября, улетаем 18-го» — the return day ends the stay.
        # A day alone at or before the start is in the next month: «с 28 ноября, улетаем 3-го».
        if back := BACK_DAY_RE.search(t, m.end()):
            d2, m2 = back.group(1), back.group(2) or m1
            if not back.group(2) and int(d2) <= int(d1):
                m2 = MONTH_NAMES[_month(m1) % 12]
    else:
        today = date.today()
        if m := re.search(r"\b(сегодня|завтра|послезавтра)\b", t):
            days = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[m.group(1)]
            found["date_from"] = today + timedelta(days=days)
        elif m := re.search(rf"\bчерез\s+(?:{NUM}\s*)?(дн\w*|день|недел\w*)", t):
            n = _num(m.group(1)) if m.group(1) else 1
            found["date_from"] = today + timedelta(
                days=n * (7 if m.group(2).startswith("нед") else 1)
            )
        elif m := re.search(
            rf"\b(?:в|во|на)\s+(?:(эт[уо]т?|ближайш\w+|следующ\w+)\s+)?({'|'.join(WEEKDAYS)})\b",
            t,
        ):
            which, wd, month = m.group(1) or "", WEEKDAYS.index(m.group(2)), found.get("month")
            ahead = (wd - today.weekday()) % 7
            if month and month != (today + timedelta(days=1)).month:
                base = future_date(month, 1)  # first such weekday of the named month
                found["date_from"] = base + timedelta(days=(wd - base.weekday()) % 7)
            elif which.startswith("эт"):  # #351: «на эту пятницу» on a Friday is today
                found["date_from"] = today + timedelta(days=ahead)
            elif which.startswith("следующ"):  # the same weekday of the next week
                found["date_from"] = today + timedelta(days=ahead + 7)
            else:
                found["date_from"] = today + timedelta(days=ahead or 7)
            notes.append(f"даты: {found['date_from']:%d.%m} («{m.group(0)}», ближайший)")
        elif re.search(r"\bследующ\w*\s+недел", t):
            found["date_from"] = today + timedelta(days=7 - today.weekday())  # Monday
            notes.append(f"даты: с понедельника {found['date_from']:%d.%m} («на следующей неделе»)")
        elif part := _month_part(t, today):
            found["date_from"], said = part
            notes.append(f"даты: с {part[0]:%d.%m} («{said}», допущение: день внутри части месяца)")
        if "date_from" in found:
            found["month"] = found["date_from"].month
        return
    first = two or one or m
    said = first.group(0)
    if lead := re.search(r"\bс\s+$", t[: first.start()]):
        said = lead.group(0) + said  # the quote starts where the user's range starts (#173)
    found["month"] = _month(m1)
    today = date.today()
    try:
        start = future_date(_month(m1), int(d1), today)
    except ValueError:
        notes.append(f"даты: «{d1} {m1}» нет в календаре, даты подобраны")
        return
    # A day passed in the last month may be a slip: say so (#159). «8 марта» in October is next year.
    if start.year > today.year and date(today.year, start.month, start.day) >= today - PAST_WINDOW:
        notes.append(past_year_note(start, start.year))
    found["date_from"] = start
    if d2 is None:
        return
    try:
        end = date(start.year + (_month(m2) < start.month), _month(m2), int(d2))
    except ValueError:
        end = start
    if end <= start:
        notes.append(f"даты: «{said}» — такого периода нет, конец по длительности")
        return
    if (end - start).days > MAX_NIGHTS:
        notes.append(too_long_note(said, kept))  # the start stays, the end goes
        return
    found["date_to"], found["nights"] = end, (end - start).days


# «дней 10 августа», «дней 5 тысяч»: the number after «дней» is a date or a sum, not a length.
NOT_LENGTH_AFTER = rf"(?!\s*(?:{MONTH}|[-–—:.]\d|тыс|к\b|₽|руб|час|ч\b|чел|%))"


DEFAULT_NIGHTS = 7  # the length when none is named; model.assume() adds the note
MAX_NIGHTS = 60  # a longer stay is named and not taken (#160)


def too_long_note(said: str, nights: int) -> str:
    """A length over MAX_NIGHTS is not taken; `nights` is the length that stays in the plan."""
    return f"длительность: срок «{said}» не принял — взято {nights} {_night_word(nights)}"


def is_too_long_note(assumption: str) -> bool:
    return assumption.startswith("длительность: срок «")


# A ruble sum right before «на месяц»: «бюджет 100 тысяч», «100 000 ₽», «100к».
BUDGET_SUM_BEFORE = re.compile(
    r"(?:бюджет\w*\W*(?:до\s+|около\s+)?\d[\d\s]*"
    r"|\d[\d\s.,]*\s*(?:(?:тыс\w*|к|k|₽|руб\w*|р|млн|миллион\w*)\.?\s*)+)\s*$"
)
# «позже», «вперёд», «назад» after a period: the dates move, the length stays (#173).
NOT_A_MOVE = r"(?!\s*(?:позже|раньше|вперед|вперёд|назад))"
MONTH_STAY = re.compile(rf"\bна\s+(?:{NUM}\s+|пар[уы]\s+)?месяц\w*(?!\w){NOT_A_MOVE}")


def _month_stay(t: str) -> re.Match | None:
    """The first «на месяц» that is a length: one after a budget sum is a budget period (#170, #173)."""
    stays = (m for m in MONTH_STAY.finditer(t) if not BUDGET_SUM_BEFORE.search(t[: m.start()]))
    return next(stays, None)


# #185: holidays instead of dates. The Russian New Year break is a known period; other school
# holidays have no calendar here, so they name a season or the nearest month, in a note.
NEW_YEAR_RE = re.compile(
    r"(?:\b(?:на|к|под)\s+)?(?:\bнов(?:ый|ого|ому|ым)\s+год\w*"
    r"|\bновогодн\w*(?:\s+(?:праздник|каникул|выходн)\w*)?)"
)
WINTER_HOLIDAY_RE = re.compile(r"(?:\bна\s+)?\bзимн\w*\s+каникул\w*")
SEASON_HOLIDAY_RE = re.compile(r"\b(летн|осенн|весенн)\w*\s+каникул\w*")
HOLIDAY_SEASON = {"летн": "летом", "осенн": "осенью", "весенн": "весной"}
ANY_HOLIDAY_RE = re.compile(r"\bканикул\w*")
NEW_YEAR_START, NEW_YEAR_END = (12, 30), (1, 8)
# After «после», «до», «кроме», «без», «перед», «не» the break is not the trip; «к новому году
# вернуться домой» is a deadline, not a trip.
NOT_NEW_YEAR_BEFORE = re.compile(r"\b(?:после|до|кроме|без|перед|не)\s+$")
AT_HOME_AFTER = re.compile(r"\bверн[уё]\w*|\bдом(?:а|ой|е)?\b|\bуспе\w*")


def _new_year(t: str) -> re.Match | None:
    for m in NEW_YEAR_RE.finditer(t):
        if NOT_NEW_YEAR_BEFORE.search(t[: m.start()]):
            continue
        if m.group(0).startswith("к ") and AT_HOME_AFTER.search(t[m.end() : m.end() + 40]):
            continue
        return m
    return None


def _new_year_window(today: date) -> tuple[date, date]:
    """30.12–08.01 after today. The break goes on until 7.01 (a night is left): from tomorrow."""
    if today.month == 1 and today.day < NEW_YEAR_END[1] - 1:
        return today + timedelta(days=1), date(today.year, *NEW_YEAR_END)
    if (today.month, today.day) == (12, 31):
        return today + timedelta(days=1), date(today.year + 1, *NEW_YEAR_END)
    start = future_date(*NEW_YEAR_START, today)
    return start, date(start.year + 1, *NEW_YEAR_END)


def _holiday(t: str, found: dict, notes: list[str]) -> str | None:
    """«на новый год», «новогодние праздники», «каникулы» without a date.

    The New Year break sets the start date; returns the said words, the end is set by
    _holiday_end after the length is read. A season holiday sets the month, like «летом»."""
    if "date_from" in found:
        return None
    month, today = found.get("month"), date.today()
    new_year = _new_year(t) if month in (None, 12) else None
    if new_year or (month is None and (winter := WINTER_HOLIDAY_RE.search(t))):
        said = (new_year or winter).group(0)
        found["date_from"] = _new_year_window(today)[0]
        found["month"] = found["date_from"].month
        notes[:] = [n for n in notes if not n.startswith("когда:")]  # «зимой на новый год»
        return said
    if month is not None:
        return None
    if m := SEASON_HOLIDAY_RE.search(t):
        months = SEASONS[HOLIDAY_SEASON[m.group(1)]]
        found["month"] = today.month if today.month in months else months[0]
        why = "текущий месяц сезона" if today.month in months else "первый месяц сезона"
    elif m := ANY_HOLIDAY_RE.search(t):
        found["month"] = today.month % 12 + 1
        why = "сезон не назван, взят ближайший месяц"
    else:
        return None
    notes.append(f"когда: {MONTH_NAMES[found['month'] - 1]} («{m.group(0)}»: {why})")
    return None


def _holiday_end(said: str, found: dict, notes: list[str]) -> None:
    """The New Year break lasts to 08.01 unless the user named the length."""
    start = found["date_from"]
    end = _new_year_window(date.today())[1] if "nights" not in found else None
    if end is not None:
        found["nights"] = (end - start).days
    found["date_to"] = start + timedelta(days=found["nights"])
    notes.append(
        f"даты: {start:%d.%m}–{found['date_to']:%d.%m} («{said}»: праздники 1–8 января, "
        f"заезд {start:%d.%m} — допущение)"
    )


# C5 (#408): «билеты у нас уже куплены», «свои билеты», «уже купили билеты».
OWN_TICKETS_RE = re.compile(
    r"\bбилет\w*\s+(?:у\s+(?:нас|меня)\s+)?(?:уже\s+)?(?:есть|куплен\w*|взят\w*)"
    r"|\bсво\w+\s+билет\w*|\bуже\s+(?:купил\w*|взял\w*)\s+билет\w*"
)
# The clock time of the own arrival and departure in one clause: «прилетаем 14 ноября в 01:30».
CLOCK = r"[^.,;!?]*?\bв\s+(\d{1,2})[:.](\d{2})\b"
ARRIVE_RE = re.compile(rf"\b(?:прилет|приезж|прибыв|приезд|прибыт)\w*{CLOCK}")
LEAVE_RE = re.compile(rf"\b(?:улет|уезжа|отъезд|обратн)\w*{CLOCK}")


def _own_tickets(t: str, found: dict) -> None:
    """Own tickets and their times at the destination of this message (offers.own_legs)."""
    if not OWN_TICKETS_RE.search(t):
        return
    found["own_transport"] = True
    place = resolve_place(found["destinations"][0]) if found.get("destinations") else None
    if place is None:
        return  # no zone for the clock time
    tz = ZoneInfo(place["tz"])
    for key, day, rx in (("arrive_at", "date_from", ARRIVE_RE), ("leave_at", "date_to", LEAVE_RE)):
        m = rx.search(t)
        if m and found.get(day) and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            at = time(int(m.group(1)), int(m.group(2)))
            found[key] = datetime.combine(found[day], at, tz)


def _nights(t: str, found: dict, notes: list[str], kept: int) -> None:
    """«3 ночи», «на 3 дня» (2 ночи), «на неделю», «на месяц» (30), «на выходные» (пятница–воскресенье).

    kept: the length of the plan now (an edit keeps it); DEFAULT_NIGHTS in a new session."""
    if "nights" in found:
        return
    starts = rf"\bчерез\s+(?:{NUM}\s*)?(?:дн\w*|день|недел\w*)|\bследующ\w*\s+недел\w*"
    # «Сдвинь на 3 дня», «Продли на 2 дня»: an edit of the current trip, not a new length (#36)
    # «Сократить расходы на 3 дня» names a cost, not the trip: the object word is no edit
    starts += rf"|\b{EDIT_VERB}\w*(?:\s+(?!расход|бюджет|затрат|цен)\w+)?\s+на\s+(?:(?:{NUM}|пар[уы])\s*)?(?:дн\w*|день|сут\w*|недел\w*|ноч\w*|месяц\w*)"
    t = re.sub(starts, lambda m: " " * len(m.group(0)), t)  # a start, not a duration
    if m := re.search(r"\bна\s+(\d{3,})\s*(?:ноч\w*|дн(?:я|ей)|день|сут\w*)", t):
        found["nights"] = MAX_NIGHTS + 1  # too long, the 3 digits are no number the rules read
    elif m := re.search(rf"(?<![:.])\b{NUM}\s*ноч\w*", t):  # «в 01:30 ночи» is a time (C5)
        n = _num(m.group(1))
        if n >= 1:
            found["nights"] = n
        else:
            notes.append(f"длительность: «{m.group(0)}» не бывает, взята длительность по умолчанию")
    elif m := re.search(rf"\b{NUM}\s*(?:дн(?:я|ей)|день)\b(?!\s*(?:позже|раньше|спустя))", t):
        n = _num(m.group(1))
        found["nights"] = max(n - 1, 1)
        notes.append(f"длительность: {found['nights']} ноч. («{m.group(0)}»)")
    elif m := re.search(rf"\b{NUM}\s*сут\w*", t):
        found["nights"] = _num(m.group(1))
        notes.append(f"длительность: {found['nights']} ноч. («{m.group(0)}»)")
    elif m := re.search(r"\bна\s+сутки\b", t):
        found["nights"] = 1
        notes.append(f"длительность: 1 ноч. («{m.group(0)}»)")
    elif m := re.search(r"\bна\s+пар[уы]\s+(?:дн\w*|день|суток)", t):
        found["nights"] = 2
        notes.append(f"длительность: 2 ноч. («{m.group(0)}»)")
    elif m := re.search(rf"\bна\s+пол\s?года\b{NOT_A_MOVE}", t):
        found["nights"] = 180  # half a year: over the limit, named
    elif m := re.search(rf"\bна\s+(?:полтора|1[.,]5)\s+месяц\w*(?!\w){NOT_A_MOVE}", t):
        found["nights"] = 45
        notes.append(f"длительность: «{m.group(0)}» — 45 ночей")
    elif m := _month_stay(t):
        # «на пару месяцев» is 2 months; «бюджет 100 тысяч на месяц» is a budget period (#170)
        found["nights"] = 30 * (2 if "пар" in m.group(0) else _num(m.group(1)) if m.group(1) else 1)
        if found["nights"] <= MAX_NIGHTS:
            notes.append(f"длительность: «{m.group(0)}» — {found['nights']} ночей")
    elif m := re.search(rf"\b(?:{NUM}\s+)?недел\w*", t):
        found["nights"] = 7 * (_num(m.group(1)) if m.group(1) else 1)
    elif m := re.search(rf"\bдн(?:я|ей)\s+(\d{{1,2}})\b{NOT_LENGTH_AFTER}", t):
        # «дней 10» (#114): after every named length; «день 3» is a day of a programme
        found["nights"] = max(int(m.group(1)) - 1, 1)
        notes.append(f"длительность: {found['nights']} ноч. («{m.group(0)}»)")
    elif m := re.search(rf"(?<!\bin )\b{NUM_EN}[\s-]*nights?\b", t):
        found["nights"] = _num_en(m.group(1))
    elif m := re.search(rf"(?<!\bin )\b{NUM_EN}[\s-]*days?\b", t):
        found["nights"] = max(_num_en(m.group(1)) - 1, 1)
        notes.append(f"длительность: {found['nights']} ноч. («{m.group(0)}»)")
    elif m := re.search(rf"(?<!\bin )\b{NUM_EN}[\s-]*weeks?\b", t):
        found["nights"] = 7 * _num_en(m.group(1))
    elif re.search(r"\bвыходн|\bweekend\b|\bуик[-\s]?[эе]нд", t):
        found["nights"] = 2
        if "date_from" not in found:
            month, tomorrow = found.get("month"), date.today() + timedelta(days=1)
            base = future_date(month, 1) if month and month != tomorrow.month else tomorrow
            start = base + timedelta(days=(4 - base.weekday()) % 7)  # Friday
            end = start + timedelta(days=2)
            found["date_from"], found["date_to"], found["month"] = start, end, start.month
            notes.append(f"даты: выходные {start:%d.%m}–{end:%d.%m} (ближайшие)")
    if found.get("nights", 0) > MAX_NIGHTS:
        found.pop("nights")
        notes.append(too_long_note(m.group(0), kept))


# #179: a question about children at a built plan («А дети бесплатно?», «а с детьми можно?»)
# asks, it does not state a party. The words are the signs of a question without «?».
CHILD_QUESTION_RE = re.compile(
    r"\?|\bли\b|\bможно\b|\bбесплатн|\bскидк|\bльгот|\bа\s+(?:с\s+)?(?:дет|реб[её]н)"
)
THRESHOLD_RE = re.compile(r"\b(?:до|от|младше|старше|моложе|менее|более|меньше|больше)\b")
ASKING_NOTE = "дети: вопрос, состав не менял — чтобы добавить, назовите возраст"


def is_child_question(text: str) -> bool:
    return bool(CHILD_QUESTION_RE.search(text.lower().replace("ё", "е")))


# #302: a stay is asked for («гостиницу на пятницу», «нужен отель»): it needs a city.
STAY_ASK_RE = re.compile(
    r"\b(?:отел(?:ь|я|ю|е|и|ей|ям|ях|ями)\b|гостиниц\w*|хостел\w*|апартамент\w*|жиль[еяю]\w*"
    r"|переночев\w*)"
)
# #351: a booking or tickets without a stay word («забронируй билеты на поезд»): the trip's
# destination is asked, not the city of a hotel.
# A verb asks to book: «вместе с билетами» in a budget is no request (F09).
BOOK_ASK_RE = re.compile(r"\b(?:заброниру\w*|забронир\w*|купи(?:те)?|оформи(?:те)?)\b")


def asks_stay(text: str) -> bool:
    return bool(STAY_ASK_RE.search(text.lower().replace("ё", "е")))


def asks_booking(text: str) -> bool:
    return bool(BOOK_ASK_RE.search(text.lower().replace("ё", "е")))


def children_uncounted(text: str) -> bool:
    """«с детьми», «дети»: children without a number and without ages (#302, F25).

    The rules guess two children of 7 then; the number and the ages set the price, so they are
    asked. «двое детей», «дети 4 и 7 лет», «без детей» are counted."""
    t = text.lower().replace("ё", "е")
    for m in CHILD_RE.finditer(t):
        if m.group(1) or not m.group(0).split()[-1].startswith("дет"):
            continue
        if NO_CHILD_BEFORE.search(t[: m.start()]) or re.match(r"\s*(?:нет|не)\b", t[m.end() :]):
            continue  # «без детей», «детей нет»
        if AGES_RE.match(t, m.end()) or AGES_LIST_RE.match(t, m.end()):
            continue
        if AGES_BARE_RE.match(t, m.end()):
            continue
        return True
    return False


# #275: only the roles in a travel chain are companions: after «с/со» («я с мамой»), right after
# «я/мы» («я, муж, бабушка с дедушкой»), or a bare list that holds a child («бабушка, дедушка и
# внук 5 лет»). Roles after «без», «к», «навестить», «рядом с» or as a subject are not.
# A child phrase becomes «§» so that the chain goes through it («двое детей 6 и 9 лет»).
CHILD_PHRASE_RE = re.compile(
    CHILD_RE.pattern
    + r"(?:\s+\d+(?:\s*(?:и|,)\s*\d+)*\s*(?:лет|года|год|мес\w*)|\s*\(\d+(?:\s*(?:и|,)\s*\d+)*\))?"
)
_ITEM = rf"(?:(?:мо\w+|наш\w+|свои\w*|ег?о|её|ее|их)\s+)?(?:\b{ROLE_SRC}\b|§)"
CHAIN_RE = re.compile(
    rf"(?P<s>\b(?:я|мы)\b[\s,]*(?:и\s+)?(?:со?\s+)?|\bсо?\s+)?"
    rf"(?P<c>{_ITEM}(?:[\s,]*(?:и\s+)?(?:со?\s+)?{_ITEM})*)"
)
NEAR_BEFORE_RE = re.compile(
    r"\b(?:рядом|около|возле|близко|недалеко|у|без|к|маму|папу|бабушку|дедушку|жену|мужа"
    r"|родителей|сестру|брата|друга|подругу)\s+$"
)


def _family_adults(t: str) -> int:
    """Adults named by family words in a travel chain; 0 when the words do not make a party."""
    # a grown-up son («сын с женой») is a travelling adult: a role word stands for him
    t = CHILD_PHRASE_RE.sub(lambda m: "брат" if ADULT_KIN_RE.match(t, m.end()) else "§", t)
    adults, me = 0, False
    for m in CHAIN_RE.finditer(t):
        chain = m.group("c")
        roles = [r.group(0) for r in ADULT_ROLE_RE.finditer(chain)]
        start = m.group("s")
        if start and NEAR_BEFORE_RE.search(t[: m.start()]) and not start.startswith(("я", "мы")):
            continue
        if not roles or not (start or ("§" in chain and len(roles) >= 2)):
            continue
        adults += sum(2 if r.startswith("родител") else 1 for r in roles)
        me = me or bool(start)
    return adults + me


ADULT_KIN_RE = re.compile(
    r"\s+(?:и\s+)?с\s+(?:ег?о\s+|её\s+|ее\s+)?(?:жен\w*|мужем|невестк\w*|зят\w*)"
)


# #341: «с друзьями, 4 человека», «4 чел.», «на 4 персоны», «4 гостя»: a head count. Not a limit
# of a room («4 человека в номере максимум», «до 4 человек») and not a score («4 человека из 5»).
HEADS_RE = re.compile(
    rf"\b{NUM}\s*(?:человек\w*|чел\b\.?|персон\w*|гост(?:я|ей|ь|ям))(?!\w)"
    r"(?!\s*(?:из\s+\d|в\s+номер\w*|в\s+комнат\w*|максимум|макс\b|в\s+день|в\s+час))"
)
HEADS_NOT_BEFORE = re.compile(r"(?:\bдо|\bиз|макс\w*|не\s+более|вмещ\w*|рейтинг\w*|оценк\w*)\s+$")


# Children are inside the head count only when the text says so (#341).
HEADS_INSIDE_RE = re.compile(
    rf"\s*[,(]?\s*(?:из\s+них|включая|в\s+том\s+числе)|\s*:"
    rf"|\s+с\s+(?:{NUM}\s+)?(?:реб[её]н|дет|малыш)"
)
HEADS_PLUS_RE = re.compile(r"\s*(?:и|\+|плюс)\s+")


def _people_count(t: str, kids: int) -> tuple[int, str] | None:
    """Adults from a head count and a note; None when the children make it unclear."""
    for m in HEADS_RE.finditer(t):
        n = _num(m.group(1))
        if HEADS_NOT_BEFORE.search(t[: m.start()]) or not 1 <= n <= 20:
            continue
        tail = t[m.end() :]
        if not kids or HEADS_PLUS_RE.match(tail):
            return n, f"взрослых: {n} (по «{m.group(0)}»)"
        if HEADS_INSIDE_RE.match(tail):
            adults = max(n - kids, 1)
            return adults, f"взрослых: {adults} (из «{m.group(0)}», дети в счёте)"
    return None


# #369: «по 50 тысяч на 1 человека», «сколько на человека?»: the price per head, no party.
PER_PERSON_RE = re.compile(r"\bна\s+(?:(?:1|одного|каждого)\s+)?(?:человека|душу|персону)\b")
PRICE_CUE_RE = re.compile(r"сколько|\bцен[ауы]\b|стоимост|стоит|покаж\w*\s+цен")
MONEY_PER_PERSON_RE = re.compile(
    r"((?:тыс\w*|руб\w*|₽|\d\s*к|\bр)\.?)\s+на\s+(?:(?:1|одного|каждого)\s+)?(?:человека|душу|персону)\b"
)


def _people(
    t: str, found: dict, notes: list[str], asking: bool = False, built: bool = False
) -> None:
    """Adults, children with ages; «нас четверо» counts children out.

    built: the plan exists; «на 1 человека» near a price word is then a unit, not a party (#369).

    asking: the message is a question at a built plan; a child without a number or an age in
    it adds nobody (#179)."""
    ages: list[int] = []
    no_kids = False
    if built and (PRICE_CUE_RE.search(t) or asking):  # only at a built plan
        t = PER_PERSON_RE.sub(" ", t)  # #369: «на 1 человека» is a unit of price, not a party
    t = MONEY_PER_PERSON_RE.sub(r"\1", t)
    for m in CHILD_RE.finditer(t):
        if ADULT_KIN_RE.match(t, m.end()):
            continue  # «сын с женой»: a grown-up son is no child (#275)
        if (
            m.group(0).endswith(NO_CHILD_FORM)
            and NO_CHILD_BEFORE.search(t[: m.start()])
            and not NO_CHILD_AFTER.match(t, m.end())
        ):
            no_kids = True  # «без детей»: nobody is added (#195)
            continue
        plural_word = m.group(0).split()[-1].startswith("дет")
        a = (
            (m.group(0).split()[-1].startswith("дет") and AGES_TAIL_RE.match(t, m.end()))
            or AGES_RE.match(t, m.end())
            or AGES_LIST_RE.match(t, m.end())
        )
        if asking and a and THRESHOLD_RE.search(t[m.end() : a.start(1)]):
            a = None  # «детям до 12 лет скидка?»: 12 is a limit of a fare, not an age
        said = _ages_of(a, not plural_word) if a else []
        if not a and not asking and (b := AGES_BARE_RE.match(t, m.end())):
            bare = [int(x) for x in re.findall(r"\d+", b.group(1))]
            if max(bare) <= 17 and (not m.group(1) or _num(m.group(1)) == len(bare)):
                said = bare
        single = (
            not m.group(1) and not m.group("twin") and not m.group(0).split()[-1].startswith("дет")
        )
        if not said and not asking and single and (o := AGE_ONE_RE.match(t, m.end())):
            said = [int(o.group(1))] if int(o.group(1)) <= 17 else []
        if not said and m.group("adj"):
            said = [AGE_ADJ[m.group("adj")]]
        plural = m.group(0).split()[-1].startswith("дет")
        count = _num(m.group(1)) if m.group(1) else len(said) or (2 if plural else 1)
        if m.group("twin"):  # «близнецы по 2 года»: one age for every child of the set
            count = next(v for k, v in TWINS.items() if m.group("twin").startswith(k))
        if len(said) == 1 and count > 1 and (m.group("twin") or m.group("adj")):
            said = said * count
        if (
            plural
            and not m.group(1)
            and re.match(r"\s*:", t[m.end() :])
            and CHILD_RE.search(t, m.end())
            and (not a or CHILD_RE.search(t, m.end(), a.start(1)))
        ):
            continue  # «наши дети: у меня близнецы…»: the list that follows has them
        if asking and len(said) < count:  # no age for every counted child: nobody is added
            if ASKING_NOTE not in notes:
                notes.append(ASKING_NOTE)
            continue
        if len(said) < count:
            notes.append("возраст ребёнка: 7 лет (возраст не назван)")
        ages += (said + [7] * count)[:count]
    if ages:
        found["children_ages"] = ages
    elif no_kids and asking:  # a question «а без детей можно?» changes nothing (#179)
        if ASKING_NOTE not in notes:
            notes.append(ASKING_NOTE)
    elif no_kids:
        found["children_ages"] = []
    if m := re.search(rf"\b{NUM}\s*взросл\w*", t):
        if _num(m.group(1)) < 1:
            notes.append(f"взрослых: «{m.group(0)}» не бывает, состав прежний")
        else:
            found["adults"] = _num(m.group(1))
    elif (m := GROUP_RE.search(t)) and m.group(1) and re.match(r"\s*взросл", t[m.end() :]):
        found["adults"] = GROUP[m.group(1)]  # «на троих взрослых»: all three are adults
    elif m := re.search(rf"\bнас\s+(?:будет\s+)?{NUM}\b|\b({'|'.join(TOGETHER)})\b", t):
        total = _num(m.group(1)) if m.group(1) else TOGETHER[m.group(2)]
        found["adults"] = max(total - len(ages), 1)
    elif not asking and not _family_adults(t) and (head := _people_count(t, len(ages))):
        found["adults"] = head[0]
        notes.append(head[1])
    elif (m := FRIENDS_RE.search(t)) and _num(m.group(1)) >= 1:
        found["adults"], note = _friends(t, m)
        notes.append(note)
    elif (
        not ages and not asking and (m := GROUP_RE.search(t))
    ):  # a question changes no party  # «на двоих с ребёнком»: the child may count
        total = GROUP[m.group(1)] if m.group(1) else _num(m.group(2))
        found["adults"] = max(total - len(ages), 1)
    elif SOLO_OTHER.search(t) or any(
        SOLO_NEXT.match(t, m.end()) or SOLO_PREV.search(t[: m.start()]) for m in SOLO_RE.finditer(t)
    ):
        found["adults"] = 1
    elif not asking and (n := _family_adults(t)):
        found["adults"] = n
        notes.append(f"взрослых: {n} (по составу семьи в тексте)")
    elif m := EN_GROUP_RE.search(t):
        found["adults"] = 2 if m.group(0).endswith("couple") else _num_en(m.group(1))
    elif ages and (m := PAIR_RE.search(t)):
        found["adults"] = 2
        notes.append(f"взрослых: 2 (по слову «{m.group(0)}»)")


HOTEL_NOTE = "бюджет отеля"  # the label of the assumption; model.LABELS uses it too
# «отель до 40 тысяч»: up to 3 words between, none of them a comma or a conjunction
HOTEL_BEFORE_RE = re.compile(
    r"(?:отел|гостиниц|проживани)\w*\s+(?:(?!(?:и|а|но|да|\+|плюс)\s)[^\s,;.!?]+\s+){0,3}$"
)
HOTEL_AFTER_RE = re.compile(
    r"[\w.]*\s*(?:₽|руб\w*|р\b)?\s*(?:на|за|для)\s+(?:отел|гостиниц|проживани)"
)
# A fragment ends at , ; ! ? a full stop (not in a number or «тыс.») and the conjunctions а, но.
FRAGMENT_END_RE = re.compile(
    r"[,;!?]|(?<!тыс)(?<!руб)(?<!млн)(?<!\bр)(?<!\bт)\.(?!\d)|\b(?:а|но)\b"
)
# Another expense item in the fragment: the sum is for the trip, not for the hotel (#41).
EXPENSE_RE = re.compile(
    r"\b(?:перел|авиа|билет|рейс|транспорт|трансфер|поезд|самол|пол[её]т|такси|автобус|тур\b|тура\b|туре\b|туром\b"
    r"|итого|общ|вместе)\w*|\bдорог(?:а|и|у|е|ой|ою)\b"
)
AND_HOTEL_RE = re.compile(r"(?:\bи|\+|плюс)\s*(?:отел|гостиниц|проживани)")
TOTAL_BEFORE_RE = re.compile(r"\b(?:всего|итого|общ\w+|вместе)\s+(?:\S+\s+){0,2}$")
# «бюджет отеля: 10 000 ₽ за ночь…»: the price of a night, kept in the assumption (see model)
PER_NIGHT_NOTE_RE = re.compile(rf"^{HOTEL_NOTE}: (\d[\d ]*) ₽ за ночь")


def money(n: int) -> str:
    return f"{n:,} ₽".replace(",", " ")


def _night_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "ночь"
    return "ночи" if 2 <= n % 10 <= 4 and n % 100 not in (12, 13, 14) else "ночей"


def _rub(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _currency_of(before: str, after: str) -> str | None:
    """The foreign currency written right after a sum («1000 евро») or before it («$1500»)."""
    for label, symbol, words in CURRENCIES:
        sym = re.escape(symbol) if symbol else "(?!)"
        if re.match(rf"\s*(?:{sym}|(?:{words})\b)", after) or (
            symbol and before.rstrip().endswith(symbol)
        ):
            return label
    return None


def _standalone_currency(t: str) -> str | None:
    """A foreign sum no ruble pattern took: «$1500», «1500$», «бюджет 3 евро», «1000 евро» first."""
    cue = r"(?:бюджет\w*|до|потол\w+|лимит\w*|максимум\w*|около|порядка|не\s+(?:больше|более))"
    for label, symbol, words in CURRENCIES:
        sym = re.escape(symbol) if symbol else "(?!)"
        by_symbol = rf"{sym}\s*\d|\d\s*{sym}"
        by_word = rf"(?:\A\s*|{cue}\s+(?:\w+\s+)?){AMOUNT_UNIT}(?:{words})\b"
        if re.search(by_symbol, t) or re.search(by_word, t):
            return label
    return None


def _fragment(t: str, start: int, end: int) -> tuple[str, str]:
    """The text before and after the sum inside its fragment (see FRAGMENT_END_RE)."""
    ends = [(m.start(), m.end()) for m in FRAGMENT_END_RE.finditer(t)]
    left = max((e for s_, e in ends if e <= start), default=0)
    right = min((s_ for s_, e in ends if s_ >= end), default=len(t))
    return t[left:start], t[end:right]


def _hotel_sum(t: str, m: re.Match) -> bool:
    """The sum is the hotel budget: «отель» is in its fragment and no other expense item is."""
    before, after = _fragment(t, m.start(), m.end())
    if AND_HOTEL_RE.search(before):  # «X и отель до N»: X is an expense item, known or not
        return False
    if EXPENSE_RE.search(before) or EXPENSE_RE.search(after) or TOTAL_BEFORE_RE.search(before):
        return False
    return bool(HOTEL_BEFORE_RE.search(before) or HOTEL_AFTER_RE.match(after))


def _budget(t: str, found: dict, notes: list[str], nights: int, adults: int) -> None:
    """The sums of the message: the first for the hotel (#41) and the first for the trip.

    A sum is the hotel budget when «отель» is in its fragment and no other expense item is
    (перелёт, билеты, тур, «итого»…); else it is the trip budget. A hotel price «за ночь»
    becomes the total of the nights named in the message; else model.resolve_hotel_budget
    does it from the assumption when the nights are known.
    #158: a foreign currency is no rubles (budget stays unset, the note says so); «на каждого»
    multiplies by the adults, «в сутки» by the nights (the note names the factors).
    nights, adults: the plan's values when the message does not state them."""
    foreign = None
    hits = [(m, v, True) for p, v in MILLION_RES for m in re.finditer(p, t)]
    hits += [(m, k, False) for p, k in BUDGET_RES for m in re.finditer(p, t)]
    hits += [(m, 1, False) for m in re.finditer(BARE_HOTEL_RE, t)]  # only for a hotel, see below
    hits.sort(key=lambda h: h[0].start())
    taken: list[tuple[int, int]] = []
    for m, k, is_word in hits:
        if "budget_rub" in found and "hotel_budget_rub" in found:
            return
        if any(m.start() < e and s_ < m.end() for s_, e in taken) or NOT_MONEY_RE.match(
            t[m.end() :]
        ):
            continue
        if m.re.pattern == BARE_HOTEL_RE and not _hotel_sum(t, m):
            continue  # «до 5000» is no sum unless it is said about the hotel
        if is_word:
            value, tail = k, t[m.end() :]
        else:
            value = int(float(m.group(1).replace(" ", "").replace(",", ".")) * k)
            tail = re.sub(r"^[а-яa-z₽.]*", "", t[m.end() :])  # the rest of «тысяч», «рублей»
        if label := _currency_of(t[: m.start()], tail):
            foreign = foreign or label  # not this sum: a ruble sum may follow
            continue
        tail = re.sub(r"^\s*(?:(?:₽|руб[а-я]*\.?|р\b\.?)\s*)?", "", tail)
        per_person = per_night = False
        for _ in range(2):  # «на человека в сутки»
            if not per_person and (r := re.match(PER_PERSON, tail)):
                per_person = True
            elif not per_night and (r := re.match(PER_NIGHT, tail)):
                per_night = True
            else:
                break
            tail = tail[r.end() :].lstrip()
        taken.append((m.start(), m.end()))
        hotel = _hotel_sum(t, m)
        if (hotel and "hotel_budget_rub" in found) or (not hotel and "budget_rub" in found):
            continue  # the first sum of each kind counts
        total, factors = value, []
        people = found.get("adults", adults)
        if per_person and people > 1:
            total *= people
            kids = " (дети не считаются)" if found.get("children_ages") else ""
            factors.append(f"{people} взрослых{kids}")
        if hotel:
            if not per_night:
                found["hotel_budget_rub"] = total
                notes.append(f"{HOTEL_NOTE}: {money(total)} за все ночи")
            else:
                line = f"{HOTEL_NOTE}: {money(total)} за ночь"
                if stay := found.get("nights"):
                    found["hotel_budget_rub"] = total * stay
                    line += f", {money(total * stay)} за {stay} ноч."
                notes.append(line)  # nights unknown: the total comes when they are
            continue
        stay = found.get("nights", nights)
        if per_night and stay > 1:
            total *= stay
            plan = "" if "nights" in found else " (длительность из плана)"
            factors.append(f"{stay} {_night_word(stay)}{plan}")
        if factors:
            notes.append(f"бюджет: {_rub(value)} ₽ × " + " × ".join(factors))
        found["budget_rub"] = total
    # No ruble sum: «бюджет 1000 евро», «$1500». None, not absent: the model must not add rubles.
    unset = "budget_rub" not in found and "hotel_budget_rub" not in found
    if unset and (label := foreign or _standalone_currency(t)):
        found["budget_rub"] = None
        notes.append(f"бюджет: в {label} — не пересчитываю, уточните в рублях")


PET_RE = re.compile(
    r"собак|кошк|\bкот(?:а|у|ом|е|ы|ов)?\b|\bпес\b|питом"
    r"|\bпс(?:а|у|ом|е|ы|ов|ам|ами|ах)\b"
    r"|\bщен(?:ок|ка|ку|ком|ке|ки|ков|кам|ками|ках)\b|\bщенят(?:а|ам|ами|ах)?\b"
    r"|\bкот[её]н(?:ок|ка|ку|ком|ке)\b|\bкотят(?:а|ам|ами|ах)?\b"
    r"|\bживотн"  # only a refusal counts: «с животными» is the model's, a bare «животные» is a zoo
)
# #294: «без питомцев», «без детей и собаки», «питомцев нет», «собаку не берём». Between «без» and
# the pet only a possessive or a list with «и»: «без нашей собаки». Any other word keeps the pet:
# «без доплаты за собаку», «без детей с собакой», «без пересадок кошка плохо переносит перелёты».
_PET_BEFORE = re.compile(
    r"\b(?:без|нет|не\s+(?:берем|возьмем|везем|берут|возьмут))\s+(?:у\s+нас\s+)?"
    r"(?:(?:наш|ваш|мо[ейи]|сво|тво)\w*\s+|\w+\s+и\s+(?:без\s+)?){0,2}$"
)
_PET_AFTER = re.compile(
    r"\w*\s+(?:у\s+нас\s+)?(?:нет\b|не\s+(?:берем|возьмем|везем|берут|возьмут)"
    r"|не\s+(?:будет|будут)\s*(?:$|[.,;!?]))"  # «собака не будет проблемой» is no refusal
)
# «без собаки не поедем», «без собаки никуда не едем», «без питомца никак»: no pet, no trip
_DOUBLE_NO = re.compile(
    r"\w*(?:\s+\w+){0,2}?\s+(?:никуда|никак|ни\s+за\s+что"
    r"|не\s+(?:(?:по)?(?:ед|ех|лет)\w*|путеш\w*|(?:с)?можем|хотим|будем|отправ\w*))"
)


def _pets(t: str) -> bool | None:
    """True: a pet goes. False: the message refuses one. None: pets are not mentioned.
    A pet that is not refused wins: «с собакой, без кошек»."""
    refused = False
    for m in PET_RE.finditer(t):
        no = _PET_BEFORE.search(t[: m.start()]) or _PET_AFTER.match(t, m.end())
        if no and not _DOUBLE_NO.match(t, m.end()):
            refused = True
        elif m.group() != "животн":
            return True
    return False if refused else None


def extract(
    text: str,
    has_destination: bool = False,
    nights: int = 7,
    adults: int = 2,
    asking: bool = False,
) -> tuple[dict, list[str]]:
    """TripSpec fields stated in one message, plus assumptions made while reading it.

    has_destination: the plan has a city already; an unknown first word is then never a city.
    nights, adults: the plan's values (TripSpec defaults); only a «в сутки» or «на каждого»
    budget reads them (#158).
    asking: a question at a built plan; a child without a number or an age adds nobody (#179)."""
    t = text.lower().replace("ё", "е")  # same length: positions match the original text
    found: dict = {}
    notes: list[str] = []
    _places(text, t, found, notes, has_destination)
    _english_places(t, found)
    _dates(t, found, notes, nights)
    holiday = _holiday(t, found, notes)
    range_notes = [i for i, n in enumerate(notes) if is_too_long_note(n)]
    _nights(t, found, notes, nights)
    for i in range_notes:  # «… на 10 ночей»: the note names the length the plan has (#173)
        kept = found.get("nights", nights)
        notes[i] = re.sub(r"\d+ \w+$", f"{kept} {_night_word(kept)}", notes[i])
    if holiday:
        _holiday_end(holiday, found, notes)
    _own_tickets(t, found)
    _people(t, found, notes, asking, has_destination)
    _budget(t, found, notes, nights, adults)
    if (pets := _pets(t)) is not None:
        found["pets"] = pets
    if meal := _meal(t):
        found["meal"] = meal
    negated = re.compile(r"\b(?:не|без)\s+(?:\w+\s+)?$")  # «не хочу лететь», «не на машине»
    hits = {m: list(re.finditer(p, t)) for m, p in MODES}
    modes = [m for m, xs in hits.items() if any(not _refused(t, x) for x in xs)]
    # A refusal after the word bans the flight; a refusal before it is NO_FLIGHT's: «не хочу
    # лететь долго» bans a long flight only (#277).
    refused_after = any(REFUSED_AFTER_RE.match(t, x.end()) for x in hits["flight"])
    # «без перелёта не поеду»: two refusals want the flight (#351)
    no_flight = [bool(REFUSED_AFTER_RE.match(t, x.end())) for x in NO_FLIGHT.finditer(t)]
    if any(no_flight) and "flight" not in modes:
        modes.append("flight")
    fear = _fears_flight(t)
    if fear:  # «боимся самолётов»: the word «самолёт» is no wish for the flight (#351)
        modes = [m for m in modes if m != "flight"]
    if not modes and (refused_after or not all(no_flight) or fear):
        # #277, #302: «без самолётов», «самолёт ни за что» ban the flight, the rest stay.
        # «не хочу поезд» bans nothing: it is no pick at a built plan (test_transport_choice).
        modes = ["train", "car"]
    if modes:
        found["modes"] = modes
    wishes = [w for w, p in WISHES if any(_wished(w, t, x, negated) for x in re.finditer(p, t))]
    for m in LANDMARK_RE.finditer(text):  # #338: «отель у Технопарка»; cities are no landmark
        if not _known(m.group(1)):
            wishes.append(f"рядом: {m.group(1)}")
    if m := BREAKFAST_FROM_RE.search(t):
        wishes.append(f"завтрак с {int(m.group(1)):02d}:{m.group(2) or '00'}")
    hours = r"\s*(?:ч\b|час\w*)\s+(?:в\s+пути|лет\w*|перел\w*|езд\w*|дорог\w*)"
    if m := re.search(rf"\b(?:до|не\s+(?:больше|дольше|более))\s+(\d{{1,2}}){hours}", t):
        found["max_travel_hours"] = float(m.group(1))
    elif re.search(r"без\s+(?:долг|длинн)\w*\s+перел|недолг\w*\s+перел", t):
        found["max_travel_hours"] = 5.0
        wishes.append("без долгих перелётов")
    elif re.search(r"\bнедалеко\b", t):
        found["max_travel_hours"] = 3.0
        notes.append("время в пути: до 3 ч («недалеко»)")
    if PRIORITY_RE.search(t):
        hits = sorted((m.start(), p) for p, rx in PRIORITIES if (m := re.search(rx, t)))
        if hits:
            found["priorities"] = [p for _, p in hits]
    if "cancellation" not in found.get("priorities", []):
        if re.search(r"невозвратн", t):
            found["refundable"] = False
        elif re.search(r"(?:бесплатн\w*|возможност\w*|можно)\s+отмен|отмен\w*\s+бесплатн", t):
            found["refundable"] = True
    if wishes:
        found["wishes"] = wishes
    return found, notes


DATE_KEYS = {"date_from", "date_to", "month", "nights"}


def with_llm(found: dict, llm_found: dict | None, excluded: list[str] | None = None) -> dict:
    """The model's fields win over the rules' fields; the rules keep what it did not say (#258).

    Dates are one group: a full model group (date_from and date_to or nights) replaces the rules'
    group; a part of a group never replaces it, and fills only when the rules read no dates.
    excluded: a refused place (#113) never comes back as a model destination.
    """
    if not llm_found:
        return found
    out = dict(found)
    if excluded and llm_found.get("destinations"):
        kept = [d for d in llm_found["destinations"] if not is_excluded(d, excluded)]
        llm_found = {**llm_found, "destinations": kept}
        if not kept:
            del llm_found["destinations"]
    full_dates = "date_from" in llm_found and bool({"date_to", "nights"} & llm_found.keys())
    if full_dates:
        for d in DATE_KEYS:
            out.pop(d, None)
    for k, v in llm_found.items():
        if k in DATE_KEYS and not full_dates and DATE_KEYS & found.keys():
            continue
        if k == "wishes":
            out["wishes"] = found.get("wishes", []) + [w for w in v if w not in found.get(k, [])]
        elif k == "budget_rub" and v == found.get("hotel_budget_rub"):
            continue  # the model took the hotel sum for the trip budget (#41)
        else:
            out[k] = v
    return out
