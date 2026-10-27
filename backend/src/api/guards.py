"""Защитные ворота из craft-прохода планировщика (ловушки TRAP-1…7, CLAUDE.md).

Чистые функции без сети: внешние источники передаются параметрами, чтобы их
можно было подменить снапшотом или фейком в тестах.
"""

import itertools
import os
import re
import unicodedata
from collections.abc import Callable, Iterable
from datetime import datetime, time

# TRAP-3: варианты написания топонимов. Ключ — нормализованная форма.
TOPONYM_ALIASES = {
    "анталья": "Анталия",
    "antalya": "Анталия",
    "сочи": "Сочи",
    "sochi": "Сочи",
    "казань": "Казань",
    "kazan": "Казань",
}


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKC", name).strip().lower().replace("ё", "е")
    return re.sub(r"[\s\-]+", " ", s)


def normalize_toponym(name: str) -> str:
    """Каноническое имя направления; неизвестное — как ввёл пользователь, без пробелов по краям."""
    return TOPONYM_ALIASES.get(_norm(name), name.strip())


# TRAP-3: падежное окончание → замены именительного падежа, самая вероятная первой.
# None — слово как написано («Бали», «Сочи» не меняются). Существительное: «в Сиену» → Сиена,
# «в Бледе» → Блед, Бледа, Бледь; «в Италии» → Италия.
NOUN_ENDINGS = (
    ("ии", ("ия",)), ("ию", ("ия",)), ("ом", ("",)), ("ем", ("", "ь")), ("у", ("а", "")),
    ("ю", ("я", "ь")), ("е", ("", "а", "ь")), ("и", (None, "ь", "я", "а")),
)  # fmt: skip
# Прилагательное в многословном названии: «Старую» → Старая, «Чешском» → Чешский.
ADJ_ENDINGS = (
    ("ую", ("ая",)), ("юю", ("яя",)), ("ой", ("ая", "ой")), ("ей", ("яя",)),
    ("ого", ("ый", "ий", "ой")), ("ом", ("ый", "ий", "ой")), ("ем", ("ий", "ый")),
    ("ым", ("ый",)), ("им", ("ий",)),
)  # fmt: skip
HUSHING = "кгхжчшщ"  # after them «ий», never «ый»: Чешский, Великий
MAX_CANDIDATES = 6  # each one is a geocoder call with a timeout


def _forms(word: str, rules: tuple) -> list[str]:
    """Nominative guesses for a word by its case ending; the word itself if no rule fits."""
    low = word.lower()
    for ending, repls in rules:
        if low.endswith(ending) and len(low) - len(ending) >= 2:
            stem = word[: len(word) - len(ending)]
            out = [word if r is None else stem + r for r in repls]
            if ending in ("ого", "ом") and stem[-1].lower() in HUSHING:
                out = [w for w in out if not w.endswith("ый")]  # Чешском → Чешский, not «Чешскый»
            return out
    return [word]


def _gender(word: str) -> str:
    """m, f, n by the nominative ending; «?» when «ь» can be either."""
    low = word.lower()
    if low.endswith(("ая", "яя", "а", "я")):
        return "f"
    if low.endswith(("ое", "ее", "о", "е")):
        return "n"
    return "?" if low.endswith("ь") else "m"


def _agrees(adj: str, noun: str) -> bool:
    """An adjective of a place name has the gender of its noun (an adjective that is none passes)."""
    low = adj.lower()
    if low.endswith(("ая", "яя")):
        gender = "f"
    elif low.endswith(("ое", "ее")):
        gender = "n"
    elif low.endswith(("ый", "ий", "ой")):
        gender = "m"
    else:
        return True
    return _gender(noun) in (gender, "?")


def name_candidates(name: str) -> list[str]:
    """Nominative forms of a place name for the geocoder, likeliest first, the name as written last.

    «Старую Ладогу» → Старая Ладога; «Бледе» → Блед, Бледа, Бледь. A many-word name stays
    whole: the words before the last one are adjectives that must agree with the noun (TRAP-3).
    """
    words = name.split()
    if not words or len(words) > 3:
        return [name.strip()]
    nouns = _forms(words[-1], NOUN_ENDINGS)
    leads = [_forms(w, ADJ_ENDINGS) for w in words[:-1]]
    forms = [
        " ".join([*lead, noun])
        for lead in itertools.product(*leads)
        for noun in nouns
        if all(_agrees(a, noun) for a in lead)
    ]
    return list(dict.fromkeys([*forms, name.strip()]))[:MAX_CANDIDATES]


def resolve_destination(
    name: str,
    catalog: dict[str, dict],
    geocode: Callable[[str], dict | None],
    snapshot: dict[str, dict] | None = None,
) -> dict | None:
    """Направление из каталога, иначе — геокодер (TRAP-2), при сбое или пустом ответе — снапшот (TRAP-6).

    Результат помечен источником: catalog | geocoder | snapshot.
    """
    canon = normalize_toponym(name)
    if canon in catalog:
        return {**catalog[canon], "name": canon, "source": "catalog"}
    try:
        found = geocode(canon)
    except Exception:  # noqa: BLE001  любой сбой геокодера: таймаут, HTTP, разбор ответа
        found = None
    if found:
        return {**found, "name": canon, "source": "geocoder"}
    if snapshot and canon in snapshot:
        return {**snapshot[canon], "name": canon, "source": "snapshot"}
    return None


LLM_VARS = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")


def llm_available(env: dict[str, str] | None = None) -> bool:
    """TRAP-1: LLM включена, только если заданы ключ, адрес и модель; иначе — режим правил."""
    env = os.environ if env is None else env
    return all(env.get(k, "").strip() for k in LLM_VARS)


def rule_extract(text: str) -> dict:
    """Разбор запроса без LLM (TRAP-1): месяц, бюджет, дети, животные, море — через api.extract."""
    from api.extract import extract  # extract -> fixtures -> guards: импорт здесь, не наверху

    found, _ = extract(text)
    wishes = found.get("wishes", [])
    return {
        "month": found.get("month"),
        "budget_rub": found.get("budget_rub"),
        "hotel_budget_rub": found.get("hotel_budget_rub"),
        "cheap": "недорого" in wishes,
        "kids": bool(found.get("children_ages")),
        "pets": found.get("pets", False),
        "sea": "море" in wishes,
    }


def check_arrival_vs_checkin(arrival: datetime, check_in: time = time(14, 0)) -> list[str]:
    """TRAP-5: прибытие раньше заезда — предупреждение и варианты. Время — местное, с tzinfo."""
    if arrival.tzinfo is None:
        raise ValueError("arrival без часового пояса: сравнение с заездом недостоверно")
    if arrival.timetz().replace(tzinfo=None) < check_in:
        return [
            f"прибытие {arrival:%H:%M} раньше заезда {check_in:%H:%M}",
            "варианты: ранний заезд, камера хранения, другой транспорт, ночь накануне",
        ]
    return []


PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[ \u00a0\u202f]\d{3}(?!\d))+|\d+)\s*(₽|руб)")


def unknown_prices(
    text: str, offers: list[dict], totals: Iterable[int] = (), nights: int | None = None
) -> list[int]:
    """TRAP-4: суммы в ответе, которых нет у инструментов и которые нельзя из них вывести.

    Известны: цена каждого предложения, итоги (корзина, пакет, разницы — их пишет код),
    цена отеля × ночи и цена за ночь (цена ÷ ночи), если ночей больше одной.
    """
    prices = {int(o["price_rub"]) for o in offers if "price_rub" in o}
    known = prices | {int(t) for t in totals}
    if nights and nights > 1:
        known |= {p * nights for p in prices} | {p // nights for p in prices if p % nights == 0}
    seen = [int(re.sub(r"\D", "", m.group(1))) for m in PRICE_RE.finditer(text)]
    return [p for p in seen if p not in known]


def label_synthetic(offer: dict) -> dict:
    """TRAP-7: у каждого синтетического предложения — явная пометка для UI."""
    if offer.get("synthetic", True):
        return {**offer, "synthetic": True, "label": "синтетика"}
    return {**offer, "synthetic": False}
