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
from api.offers import places

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
PLACE_RES = [
    (name, re.compile(rf"\b(?:{'|'.join(c['stems'])})\w*")) for name, c in CATALOG.items()
] + [(name, re.compile(rf"\b(?:{stem})\w*")) for name, stem in PLACES.items()]
# Checked on the original text: «Нижний» is a city, «на нижнем этаже» is not.
CAPITAL_PLACES = [("Нижний Новгород", re.compile(r"Нижн\w*\b(?!\s+этаж)"))]
NOT_PLACES = {"новый", "выходные", "майские", "праздники", "отель", "отпуск"}
# First word as a destination (TRAP-2). A city of places.json counts in any case and form.
# An unknown capitalized word counts only in main's narrow pattern: «Ухта, 3 ночи»,
# «Калуга в мае», «Тула на 3 дня». Even then the lists below veto ordinary first words.
FIRST_NEXT = re.compile(r"\s*,\s*\d|\s+(?:во?\s+" + MONTH + r"|[сн]а?\s+\d)")
# Replanning verbs (#73): a stem plus a verb ending, never a bare prefix («Уберландия» is a city).
# Forms: imperative sg/pl, «мы» form, infinitive, «-ка». «ё» is already «е» in the lowercased text.
_VERB_ENDINGS = {
    "сдвин": "ь ьте уть ем емте ете у", "сдвига": "й йте ть ем ю",
    "перенес": "и ите ти ем емте ете у", "перенос": "и ите ить им имте ишь ят",
    "поменя": "й йте ть ем емте ете ю", "замен": "и ите ить им имте ю", "заменя": "й йте ть ем ю",
    "добав": "ь ьте ить им имте лю", "добавля": "й йте ть ем ю",
    "убер": "и ите ем емте у", "убра": "ть ли", "убира": "й йте ть ем ю",
    "удал": "и ите ить им имте ю", "удаля": "й йте ть ем ю",
    "измен": "и ите ить им имте ю", "изменя": "й йте ть ем ю",
    "увелич": "ь ьте ить им имте у", "увеличива": "й йте ть ем ю",
    "уменьш": "и ите ить им имте у", "уменьша": "й йте ть ем ю",
    "продл": "и ите ить им имте ю", "продлева": "й йте ть ем ю",
    "сократ": "и ите ить им имте ю", "сокраща": "й йте ть ем ю",
    "исправ": "ь ьте ить им имте лю", "исправля": "й йте ть ем ю",
    "пересоб": "ери ерите ерем ерет еру рать ирай ирайте ирать",
}  # fmt: skip
REBUILD_VERBS = "|".join(
    rf"{stem}(?:{'|'.join(ends.split())})(?:-ка)?\b" for stem, ends in _VERB_ENDINGS.items()
)
NOT_CITY_START = re.compile(
    r"(?:хоч|хот|ищ|нуж|надо|подск|посов|подбер|подбир|помог|помощ|орган|спланир|планир|собир"
    r"|собер|давай|еду|едем|леч|лет[иеа]|поед|полет|мечт|привет|здравств|добр|отпуск|отел|билет"
    r"|тур\b|туры|турист|поезд|подум|интерес|сделай|создай|найд|покаж|расскаж|предлож|можн"
    r"|нельз|будем|будет|как\b|что\b|где\b|куд|когд|сколько|есть\b|если\b|для\b|мне\b|нам\b"
    r"|мы\b|ну\b|вот\b|так\b|или\b|тепл|хорош|нормал|красив|дешев|дорог|быстр|скоро|срочно"
    r"|лучш|любо|какой|какая|какое|зим|весн|осен|друз|компан|сем[ьъ]|одн|вмест"
    r"|нет\b|спасиб|отличн|ладно|понятно|ясно|дальше|далее|ок\b|окей|тогда|итого|ребят"
    # replanning verbs («Сдвинь на 3 дня») and common nouns («Отдых в ноябре»), #73
    r"|" + REBUILD_VERBS + r"|отдых(?:а|у|е|ом)?\b|отдохн\w*|каникул\w*"
    r"|путевк\w*|круиз\w*|пляж(?:а|у|е|и|ем)?\b|мор[еяюь]\b)"
)  # fmt: skip
# Not a city by its ending: verbs («Планируем», «Ищете»), adjectives («Тёплое»), infinitives.
NOT_CITY_END = re.compile(r"(?:ем|им|ете|ите|ешь|ишь|ют|ят|ться|ть|ый|ий|ой|ая|яя|ое|ее|ые|ие)$")
CASES = "(?:а|у|е|ом|ем|ой|ы|и|ю|я|ь|ею|ам|ах|ами)"
CASES_SHORT = "(?:а|у|е|ы)"  # «Рим», «Вена»: a longer ending would make a common word
# Short stems that are ordinary words in these forms: «мала ли комната» is not «Мале».
NOT_SHORT_FORMS = {"мала", "малы", "бака"}
# A city that is also a person name (checked against all places.json; update it when the
# catalogue grows): «Владимир, хочу на море» is an address, not the city.
# Only a request verb after the comma makes it an address: «Владимир, 3 ночи» is the city.
PERSON_CITIES = {"Владимир"}
ADDRESS_NEXT = re.compile(  # «Владимир, хочу…» and «Владимир! Хочу…» (#73)
    r"\s*[,!]\s*(?:хоч|хот|подскаж|помог|подбер|найд|посовет|скаж|слуш|мне\b|нам\b|мы\b|ищ|нуж"
    r"|добр|привет|здравств)"
)
# A length after a comma behind an unknown first word in a plan: «Ухта, 3 ночи». The form
# «<word> на 3 дня» is a rebuild command («Перенеси на 2 дня позже»), never a note.
LENGTH_NEXT = re.compile(r"\s*,\s*\d+\s*(?:ноч|дн|недел)")
CHILD_RE = re.compile(
    rf"\b(?:{NUM}\s+)?(?:реб[её]н\w*|дет(?:и|ей|ьми|ям)\b|сын\w*|доч\w*|малыш\w*)"
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


def _places(text: str, t: str, found: dict, notes: list[str], has_destination: bool) -> None:
    """Origin after «из/от», then destinations in text order: known places and capitalized names."""
    for m in re.finditer(r"\b(?:из|от)\s+(?=\w)", t):
        w = re.match(r"[а-яе-]+", t[m.end() :])
        if name := _place_at(text, t, m.end()) or _capital(text, m.end()) or (w and _known(w[0])):
            break
    else:
        m = None
    if m:
        found["origin"] = name
        end = t.find(" ", m.end())
        end = len(t) if end < 0 else end
        t = t[: m.start()] + " " * (end - m.start()) + t[end:]  # the origin is not a destination
    hits = [(m.start(), name) for name, rx in PLACE_RES for m in rx.finditer(t)]
    hits += [(m.start(), name) for name, rx in CAPITAL_PLACES for m in rx.finditer(text)]
    for m in re.finditer(r"(?:^|[\s,(])(?:[вВ]о?|[нН]а)\s+(?=[А-ЯЁ])", text):
        free = t[m.end()] != " " and not _place_at(text, t, m.end())  # " ": the origin span
        if free and (name := _capital(text, m.end())):
            hits.append((m.end(), name))
    for m in re.finditer(r"(?:^|[\s,(])(?:во?|на)\s+([а-яе-]+)", t):  # «в иркутск», «в Иркутске»
        if (
            t[m.start(1)] != " "
            and not _place_at(text, t, m.start(1))
            and (n := _known(m.group(1)))
        ):
            hits.append((m.start(1), n))
    # A real place in the text («Музеи в Петербурге») or in the plan makes the guess unneeded.
    if first := _first_city(text, t, not hits and not has_destination, None if hits else notes):
        hits.append(first)
    dests = []
    for _, name in sorted(hits):
        if name not in dests and name != found.get("origin"):
            dests.append(name)
    if dests:
        found["destinations"] = dests


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
    elif m := re.search(rf"\bнас\s+{NUM}\b|\b({'|'.join(TOGETHER)})\b", t):
        total = _num(m.group(1)) if m.group(1) else TOGETHER[m.group(2)]
        found["adults"] = max(total - len(ages), 1)


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


def with_llm(found: dict, llm_found: dict | None) -> dict:
    """Rule fields win; the LLM adds the fields rules missed and its wishes (T11, #15).

    Dates are one group: if rules read any date field, the LLM dates are not mixed in.
    """
    if not llm_found:
        return found
    out = dict(found)
    for k, v in llm_found.items():
        if k == "wishes":
            out["wishes"] = found.get("wishes", []) + [w for w in v if w not in found.get(k, [])]
        elif k not in found and not (k in DATE_KEYS and DATE_KEYS & found.keys()):
            out[k] = v
    return out
