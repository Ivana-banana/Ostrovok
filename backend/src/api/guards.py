"""Защитные ворота из craft-прохода планировщика (ловушки TRAP-1…7, CLAUDE.md).

Чистые функции без сети: внешние источники передаются параметрами, чтобы их
можно было подменить снапшотом или фейком в тестах.
"""

import os
import re
import unicodedata
from collections.abc import Callable
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


def resolve_destination(
    name: str,
    catalog: dict[str, dict],
    geocode: Callable[[str], dict | None],
    snapshot: dict[str, dict] | None = None,
) -> dict | None:
    """Направление из каталога, иначе — геокодер (TRAP-2), при сбое сети — снапшот (TRAP-6).

    Результат помечен источником: catalog | geocoder | snapshot.
    """
    canon = normalize_toponym(name)
    if canon in catalog:
        return {**catalog[canon], "name": canon, "source": "catalog"}
    try:
        found = geocode(canon)
    except OSError:
        found = None
        if snapshot and canon in snapshot:
            return {**snapshot[canon], "name": canon, "source": "snapshot"}
    if found:
        return {**found, "name": canon, "source": "geocoder"}
    return None


def llm_available(env: dict[str, str] | None = None) -> bool:
    """TRAP-1: без ключа LLM агент работает в режиме правил, а не падает."""
    env = os.environ if env is None else env
    return bool(env.get("LLM_API_KEY", "").strip())


def rule_extract(text: str) -> dict:
    """Разбор запроса без LLM (TRAP-1): месяц, бюджет, дети, животные, море — через api.extract."""
    from api.extract import extract  # extract -> fixtures -> guards: импорт здесь, не наверху

    found, _ = extract(text)
    wishes = found.get("wishes", [])
    return {
        "month": found.get("month"),
        "budget_rub": found.get("budget_rub"),
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


PRICE_RE = re.compile(r"(\d[\d\s]{2,})\s*(₽|руб)")


def unknown_prices(text: str, offers: list[dict]) -> list[int]:
    """TRAP-4: цены в ответе, которых нет ни в одном предложении инструментов."""
    known = {int(o["price_rub"]) for o in offers if "price_rub" in o}
    seen = [int(m.group(1).replace(" ", "")) for m in PRICE_RE.finditer(text)]
    return [p for p in seen if p not in known]


def label_synthetic(offer: dict) -> dict:
    """TRAP-7: у каждого синтетического предложения — явная пометка для UI."""
    if offer.get("synthetic", True):
        return {**offer, "synthetic": True, "label": "синтетика"}
    return {**offer, "synthetic": False}
