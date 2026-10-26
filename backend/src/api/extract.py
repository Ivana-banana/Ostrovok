"""Rule extractor of TripSpec fields (T3, #7): no LLM, no network (TRAP-1).

extract(text) -> (found, notes): found holds only the TripSpec fields the message states;
notes are assumptions "<label>: text" made while reading it. model.merge drops a label
when a later message states its field. The LLM extractor (llm.extract_spec, T11 #15) returns
the same `found`; with_llm merges it: rules first, the LLM fills what rules missed.
Invalid values (31 ноября, 0 ночей) are skipped with a note, never an exception.
"""

import re
from datetime import date, timedelta

from api.fixtures import CATALOG, future_date
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
    "три": 3, "трое": 3, "трех": 3, "тремя": 3, "четыре": 4, "четверо": 4, "пять": 5,
    "пятеро": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
}  # fmt: skip
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
NOT_PLACES = {"новый", "выходные", "майские", "праздники", "отель", "отпуск", "домой"}
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
CHILD_RE = re.compile(
    rf"\b(?:{NUM}\s+)?(?:реб[её]н\w*|дет(?:и|ей|ьми|ям)\b|сын\w*|доч\w*|малыш\w*)"
)
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
AGES_RE = re.compile(r"\D{0,12}?((?:\d{1,2}\s*(?:,|и)\s*)*\d{1,2})\s*(лет|год|мес)")
BUDGET_RES = [  # (pattern, multiplier); «к» counts only right after the number: «150к»
    (r"\b(\d+(?:[.,]\d+)?)\s*(?:млн|миллион)", 1_000_000),
    (r"\b(\d+(?:[.,]\d+)?)\s*(?:тыс|тр\b|т\.р)", 1000),
    (r"\b(\d+)[кk]\b", 1000),
    (r"\bтыс\w*\.?\s+(\d+)\b", 1000),
    (r"\b(\d{1,3}(?:\s\d{3})+|\d{4,7})\s*(?:₽|руб|р\b)", 1),
    (r"\bбюджет\w*\s*(?:до\s+|около\s+)?(\d{4,7})\b", 1),
]
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
WISHES = [
    ("море", r"\bмор[еяю]\b|\bморск|пляж|купат"),
    ("недорого", r"недорог|деш[её]в|бюджетн|эконом"),
    ("горы", r"\bгор(?:ы|ах|ам|ами|у)\b|\bгорн"),
    ("лыжи", r"лыж|сноуборд"),
    ("санаторий", r"санатор"),
    ("тяжело ходить", r"(?:тяжело|трудно|сложно)\s+ход|без\s+лестниц|маломобил"),
    ("вино и еда", r"\bвин[оа]\b|\bвинн|гастро|\bеда\b|\bкухн"),
    ("с удобствами", r"удобств|палатк"),
    ("красиво", r"красив"),
    ("романтика", r"романт"),
    ("командировка", r"командиров"),
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


def _num(word: str) -> int:
    return int(word) if word.isdigit() else NUMS[word]


def _place_at(text: str, t: str, pos: int) -> str | None:
    return next((name for name, rx in PLACE_RES if rx.match(t, pos)), None) or next(
        (name for name, rx in CAPITAL_PLACES if rx.match(text, pos)), None
    )


def _known(word: str) -> str | None:
    """«Иркутске», «иркутска» -> «Иркутск» (TRAP-3): one-word names of places.json, any case."""
    w = word.lower().replace("ё", "е")
    for p in places():
        n = p["name"].lower().replace("ё", "е")
        if " " in n:
            continue
        stem = n[:-1] if n[-1] in "аяьйеиыуо" else n
        short = len(stem) <= 3
        if w == n or re.fullmatch(re.escape(stem) + (CASES_SHORT if short else CASES), w):
            if short and w != n and w in NOT_SHORT_FORMS:
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
    if known := _known(word):
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
    names = {p["country"] for p in places()} | {c["country"] for c in CATALOG.values()}
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


def _word_end(t: str, pos: int) -> int:
    m = re.compile(r"[\w-]+").match(t, pos)
    return m.end() if m else pos


def _sentence(t: str, start: int, end: int) -> str:
    """The sentence (up to «.!?;») around the mention."""
    before = [m.end() for m in SENTENCE_END.finditer(t, 0, start)]
    stop = SENTENCE_END.search(t, end)
    return t[before[-1] if before else 0 : stop.start() if stop else len(t)]


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


def _negated(t: str, spans: list[tuple[int, int]]) -> list[bool]:
    """Which of the place mentions (start, end; sorted) a negation refuses.

    «кроме X» refuses X unless the sentence adds a place («ещё в Казань»). «уже были в X» refuses
    X only with a dislike in the sentence («не хочется», «надоело») or another place wanted
    next to it, and never with «снова», «ещё раз», «понравилось»."""
    n = len(spans)
    strong = [bool(NEG_BEFORE.search(t[max(0, s - 60) : s])) for s, _ in spans]
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
                else:
                    weak[i] = True
    neg = [False] * n
    for i in range(n):
        in_list = i > 0 and bool(LIST_FORWARD.fullmatch(t[spans[i - 1][1] : spans[i][0]]))
        neg[i] = (
            strong[i]
            or (exc[i] and not _adds_place(t, spans, i, strong))
            or (in_list and neg[i - 1])
        )
        weak[i] = weak[i] or (in_list and weak[i - 1])
    for i, (s, e) in enumerate(spans):
        if weak[i] and not neg[i]:
            sentence = _sentence(t, s, e)
            wanted = any(j != i and not weak[j] and not neg[j] for j in range(n))
            neg[i] = not WISH_AGAIN.search(sentence) and (bool(DISLIKE.search(sentence)) or wanted)
    # A list shares the refusal: «кроме Турции и Египта», «В Турции и Египте уже были».
    for i in reversed(range(n - 1)):
        if neg[i + 1] and LIST_BACK.fullmatch(t[spans[i][1] : spans[i + 1][0]]):
            neg[i] = True
    return neg


def is_excluded(name: str, excluded: list[str]) -> bool:
    """The place, or the country of the place, is refused («Турция» refuses Анталия)."""
    place = resolve_place(name)
    country = place["country"] if place else CATALOG.get(name, {}).get("country")
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
        found["origin"] = name
        end = t.find(" ", m.end())
        end = len(t) if end < 0 else end
        t = t[: m.start()] + " " * (end - m.start()) + t[end:]  # the origin is not a destination
    hits = [(m.start(), name, m.end()) for name, rx in PLACE_RES for m in rx.finditer(t)]
    hits += [(m.start(), name, m.end()) for name, rx in CAPITAL_PLACES for m in rx.finditer(text)]
    for m in re.finditer(r"(?:^|[\s,(])(?:[вВ]о?|[нН]а)\s+(?=[А-ЯЁ])", text):
        free = t[m.end()] != " " and not _place_at(text, t, m.end())  # " ": the origin span
        if free and (name := _capital(text, m.end())):
            hits.append((m.end(), name, _word_end(t, m.end())))
    for m in re.finditer(r"(?:^|[\s,(])(?:во?|на)\s+([а-яе-]+)", t):  # «в иркутск», «в Иркутске»
        if (
            t[m.start(1)] != " "
            and not _place_at(text, t, m.start(1))
            and (n := _known(m.group(1)))
        ):
            hits.append((m.start(1), n, m.end(1)))
    if switch := _switch_city(text):
        hits.append((*switch, _word_end(t, switch[0])))
    # A real place in the text («Музеи в Петербурге») or in the plan makes the guess unneeded.
    if first := _first_city(text, t, not hits and not has_destination, None if hits else notes):
        hits.append((*first, _word_end(t, first[0])))
    elif not hits and not has_destination:
        _vetoed_first_word(text, t, notes)
    # Mentions for the negation: the places above and the bare countries («Турцию и Египет…»).
    # The refused name is the country when the word is one: «египет» is a hit of Хургада.
    mentions = {s: (e, name, name) for s, name, e in hits}
    for country, rx in COUNTRY_RES:
        for m in rx.finditer(t):
            e, name, _ = mentions.get(m.start(), (m.end(), None, None))
            mentions[m.start()] = (max(e, m.end()), name, country)
    starts = sorted(mentions)
    neg = _negated(t, [(s, mentions[s][0]) for s in starts])
    refused_at = {s for s, n in zip(starts, neg, strict=True) if n}
    refused = list(dict.fromkeys(mentions[s][2] for s in starts if s in refused_at))
    dests = []
    for s, name, _ in sorted(hits):
        if s in refused_at or name == found.get("origin"):
            continue
        place = resolve_place(name)
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


def _capital(text: str, pos: int) -> str | None:
    """A capitalized word that is not a month: kept as written, «Грузию» -> «Грузия»."""
    m = re.match(r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*", text[pos:])
    if not m or m.group(0).lower() in NOT_PLACES or re.match(MONTH, m.group(0).lower()):
        return None
    return _known(m.group(0)) or _nominative(m.group(0))


def _nominative(word: str) -> str:
    """Unknown names: «Грузию» -> «Грузия», «Иркутске» -> «Иркутск», «Аляске» -> «Аляска»."""
    word = re.sub(r"и[ию]$", "ия", word)
    if re.search(r"[аеёиоуыэюя]ск[еу]$", word):
        return word[:-1] + "а"  # «Аляске» -> «Аляска»; «Иркутске» has a consonant before «ск»
    return re.sub(r"(ск|цк|зк|град|бург|город)(?:е|а|у|ом)$", r"\1", word)


def _dates(t: str, found: dict, notes: list[str]) -> None:
    """«11–13 декабря», «с 30 ноября по 3 декабря», «с 20 ноября»; month without a day."""
    d = r"(\d{1,2})(?:-?го)?"
    if m := re.search(rf"\b{MONTH}", t):
        found["month"] = _month(m.group(1))
    elif m := re.search(r"\bмайск", t):
        found["month"] = 5
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
        elif re.search(r"\bследующ\w*\s+недел", t):
            found["date_from"] = today + timedelta(days=7 - today.weekday())  # Monday
            notes.append(f"даты: с понедельника {found['date_from']:%d.%m} («на следующей неделе»)")
        if "date_from" in found:
            found["month"] = found["date_from"].month
        return
    said = (two or one or m).group(0)
    found["month"] = _month(m1)
    try:
        start = future_date(_month(m1), int(d1))
    except ValueError:
        notes.append(f"даты: «{d1} {m1}» нет в календаре, даты подобраны")
        return
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
    found["date_to"], found["nights"] = end, (end - start).days


def _nights(t: str, found: dict, notes: list[str]) -> None:
    """«3 ночи», «на 3 дня» (2 ночи), «на неделю», «на выходные» (пятница–воскресенье)."""
    if "nights" in found:
        return
    starts = rf"\bчерез\s+(?:{NUM}\s*)?(?:дн\w*|день|недел\w*)|\bследующ\w*\s+недел\w*"
    # «Сдвинь на 3 дня», «Продли на 2 дня»: an edit of the current trip, not a new length (#36)
    # «Сократить расходы на 3 дня» names a cost, not the trip: the object word is no edit
    starts += rf"|\b{EDIT_VERB}\w*(?:\s+(?!расход|бюджет|затрат|цен)\w+)?\s+на\s+(?:(?:{NUM}|пар[уы])\s*)?(?:дн\w*|день|сут\w*|недел\w*|ноч\w*)"
    t = re.sub(starts, lambda m: " " * len(m.group(0)), t)  # a start, not a duration
    if m := re.search(rf"\b{NUM}\s*ноч\w*", t):
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
    elif m := re.search(rf"\b(?:{NUM}\s+)?недел", t):
        found["nights"] = 7 * (_num(m.group(1)) if m.group(1) else 1)
    elif re.search(r"\bвыходн", t):
        found["nights"] = 2
        if "date_from" not in found:
            month, tomorrow = found.get("month"), date.today() + timedelta(days=1)
            base = future_date(month, 1) if month and month != tomorrow.month else tomorrow
            start = base + timedelta(days=(4 - base.weekday()) % 7)  # Friday
            end = start + timedelta(days=2)
            found["date_from"], found["date_to"], found["month"] = start, end, start.month
            notes.append(f"даты: выходные {start:%d.%m}–{end:%d.%m} (ближайшие)")


def _people(t: str, found: dict, notes: list[str]) -> None:
    """Adults, children with ages; «нас четверо» counts children out."""
    ages: list[int] = []
    for m in CHILD_RE.finditer(t):
        a = AGES_RE.match(t, m.end())
        said = [int(x) for x in re.findall(r"\d+", a.group(1))] if a else []
        said = [0 if a.group(2) == "мес" else x for x in said if x <= 17]
        plural = m.group(0).split()[-1].startswith("дет")
        count = _num(m.group(1)) if m.group(1) else len(said) or (2 if plural else 1)
        if len(said) < count:
            notes.append("возраст ребёнка: 7 лет (возраст не назван)")
        ages += (said + [7] * count)[:count]
    if ages:
        found["children_ages"] = ages
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
    elif not ages and (m := GROUP_RE.search(t)):  # «на двоих с ребёнком»: the child may count
        total = GROUP[m.group(1)] if m.group(1) else _num(m.group(2))
        found["adults"] = max(total - len(ages), 1)
    elif SOLO_OTHER.search(t) or any(
        SOLO_NEXT.match(t, m.end()) or SOLO_PREV.search(t[: m.start()]) for m in SOLO_RE.finditer(t)
    ):
        found["adults"] = 1
    elif ages and (m := PAIR_RE.search(t)):
        found["adults"] = 2
        notes.append(f"взрослых: 2 (по слову «{m.group(0)}»)")


def _budget(t: str, found: dict, notes: list[str]) -> None:
    """The first sum in the message; said about the hotel, it still counts for the whole trip."""
    for pattern, k in BUDGET_RES:
        if m := re.search(pattern, t):
            found["budget_rub"] = int(float(m.group(1).replace(" ", "").replace(",", ".")) * k)
            if re.search(r"отел\w*\s+(?:\S+\s+){0,2}$", t[: m.start()]):
                # TODO(T7, #13): a hotel-only budget; TripSpec has one budget for the trip.
                rub = f"{found['budget_rub']:,}".replace(",", " ")
                notes.append(f"бюджет: {rub} ₽ на всю поездку (сказано про отель)")
            return


def extract(text: str, has_destination: bool = False) -> tuple[dict, list[str]]:
    """TripSpec fields stated in one message, plus assumptions made while reading it.

    has_destination: the plan has a city already; an unknown first word is then never a city."""
    t = text.lower().replace("ё", "е")  # same length: positions match the original text
    found: dict = {}
    notes: list[str] = []
    _places(text, t, found, notes, has_destination)
    _dates(t, found, notes)
    _nights(t, found, notes)
    _people(t, found, notes)
    _budget(t, found, notes)
    if re.search(r"собак|кошк|\bкот\b|\bпес\b|питом", t):
        found["pets"] = True
    if meal := next((v for p, v in MEALS if re.search(p, t)), None):
        found["meal"] = meal
    negated = re.compile(r"\b(?:не|без)\s+(?:\w+\s+)?$")  # «не хочу лететь», «не на машине»
    modes = [
        m for m, p in MODES if any(not negated.search(t[: x.start()]) for x in re.finditer(p, t))
    ]
    if modes:
        found["modes"] = modes
    wishes = [w for w, p in WISHES if re.search(p, t)]
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
    """Rule fields win; the LLM adds the fields rules missed and its wishes (T11, #15).

    Dates are one group: if rules read any date field, the LLM dates are not mixed in.
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
    for k, v in llm_found.items():
        if k == "wishes":
            out["wishes"] = found.get("wishes", []) + [w for w in v if w not in found.get(k, [])]
        elif k not in found and not (k in DATE_KEYS and DATE_KEYS & found.keys()):
            out[k] = v
    return out
