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
"""

import re
from datetime import date, timedelta
from uuid import uuid4

from api.contracts import Cart, ChatReply, ChatRequest, Plan, Question, Stage, TripSpec
from api.extract import MONTH_NAMES, extract
from api.fixtures import (
    book,
    build_timeline,
    fill_cart,
    find_offers,
    future_date,
    plan_changes,
    rank,
    suggest_destinations,
)
from api.store import SessionStore

DEFAULT_NIGHTS = 7
CONFIRM_RE = re.compile(
    r"\b(да|ок|окей|хорошо|подходит|давай\w*|бер[уиё]\w*|оформ\w*|собер\w*|корзин\w*)\b"
)
NEGATION_RE = re.compile(r"\b(нет|не)\b")
BOOK_RE = re.compile(r"\bподтверждаю\b")
NOT_BOOK_RE = re.compile(r"\bне\s+подтверждаю\b")
ORDINAL_RE = re.compile(
    r"\b(перв(?:ый|ая|ое|ую|ого)|втор(?:ой|ая|ое|ую|ого)|трет(?:ий|ья|ье|ью|ьего)"
    r"|четв[её]рт(?:ый|ая|ое|ую|ого))\b|^\s*([1-4])\s*$"
)
ORDINALS = {"перв": 0, "втор": 1, "трет": 2, "четв": 3}
# Assumption text starts with "<label>:"; a stated field removes its assumption.
LABELS = {
    "month": "когда",
    "date_from": "даты",
    "nights": "длительность",
    "origin": "откуда",
    "budget_rub": "бюджет",
    "children_ages": "возраст ребёнка",
    "adults": "взрослых",
    "max_travel_hours": "время в пути",
}
TRIP_FIELDS = {
    "destinations", "date_from", "date_to", "nights", "adults", "children_ages",
    "budget_rub", "meal", "origin",
}  # fmt: skip


def _label(assumption: str) -> str:
    return assumption.split(":", 1)[0]


def merge(spec: TripSpec, found: dict, notes: list[str]) -> TripSpec:
    """Stated fields override the spec; their old assumptions are dropped."""
    data = spec.model_dump()
    stated = {LABELS[k] for k in found if k in LABELS}
    if stated & {"когда", "даты", "длительность"}:
        data["date_to"] = None  # derived from date_from + nights again
        if "даты" not in stated and any(_label(a) == "даты" for a in data["assumptions"]):
            data["date_from"] = None
            stated.add("даты")
    kept = [a for a in data["assumptions"] if _label(a) not in stated]
    data["assumptions"] = kept + [n for n in notes if n not in kept]
    for k, v in found.items():
        data[k] = data[k] + [w for w in v if w not in data[k]] if k == "wishes" else v
    return TripSpec.model_validate(data)


def blocking_questions(spec: TripSpec) -> list[Question]:
    """At most 2 questions, only when the message names neither a place nor a wish.

    A place or a wish is enough to move on: the rest becomes assumptions named in the reply.
    """
    if spec.destinations or spec.wishes:
        return []
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
        data["origin"] = "Москва"
        note("откуда", "Москва (город вылета не назван)")
    if data["budget_rub"] is None:
        cheap = "недорого" in data["wishes"]
        note("бюджет", "не задан — сначала дешёвые" if cheap else "не задан — разные цены")
    return TripSpec.model_validate(data)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _rub(n: int) -> str:
    return f"{n:,} ₽".replace(",", " ")


def _ordinal(text: str) -> int | None:
    m = ORDINAL_RE.search(text.lower())
    if not m:
        return None
    return ORDINALS[m.group(1)[:4]] if m.group(1) else int(m.group(2)) - 1


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
    return "\n".join(lines + _tail(spec, "Напишите номер или название направления."))


def _selected_total(plan: Plan) -> int:
    return sum(o.price_rub for o in plan.offers if o.offer_id in plan.selected)


def _text_compare(plan: Plan) -> str:
    spec = plan.spec
    kids = f" + дети {', '.join(map(str, spec.children_ages))} лет" if spec.children_ages else ""
    lines = [
        (
            f"{spec.destinations[0]}, {spec.date_from:%d.%m}–{spec.date_to:%d.%m}, "
            f"{spec.nights} ноч., {spec.adults} взр.{kids}. Цены — синтетика."
        ),
        "Отели:",
    ]
    hotels = [o for o in plan.offers if o.kind == "hotel"]
    for i, h in enumerate(hotels, 1):
        cancel = "бесплатная отмена" if h.cancellation.refundable else "невозвратный"
        lines.append(
            f"{i}. {h.title} — {h.stars}★, рейтинг {h.rating}, {h.meal}, {cancel} — "
            f"{_rub(h.price_rub)}"
        )
    for o in plan.offers:
        if o.kind == "transport":
            lines.append(f"{o.title} — {_rub(o.price_rub)}")
    chosen = [o.title for o in plan.offers if o.offer_id in plan.selected]
    lines.append(f"Предлагаю: {', '.join(chosen)}. Итого {_rub(_selected_total(plan))}.")
    for e in plan.explanations[:1]:
        lines.append(f"Почему: {e.why}." + "".join(f" {_cap(x)}." for x in e.tradeoffs))
        lines += [f"Риск: {r}." for r in e.risks]
    for c in plan.changes:
        lines.append(f"Изменено {c.what}: {c.before} → {c.after}.")
    return "\n".join(lines + _tail(spec, "Напишите «беру» или номер отеля."))


def _text_assemble(plan: Plan) -> str:
    lines = ["План поездки:"]
    lines += [f"{e.at:%d.%m %H:%M} — {e.title}" for e in plan.timeline]
    lines += [f"Внимание: {i.text}." for i in plan.issues]
    lines.append(f"Итого {_rub(_selected_total(plan))} (синтетика). Оформляем? Напишите «да».")
    return "\n".join(lines)


HINTS: dict[Stage, str] = {
    "select": "Выберите другое направление.",
    "compare": "Напишите «беру» или номер отеля.",
    "assemble": "Напишите «да», чтобы положить план в корзину.",
    "checkout": "Чтобы оформить бронь, напишите «подтверждаю».",
}


def step(prev: ChatReply | None, sid: str, message: str) -> ChatReply:
    """One user message -> the next ChatReply of the session."""
    if prev and prev.voucher:
        text = f"Бронь {prev.voucher.booking_id} уже оформлена."
        return prev.model_copy(update={"reply": text, "questions": []})
    old = prev or ChatReply(
        session_id=sid,
        stage="clarify",
        reply="",
        plan=Plan(spec=TripSpec()),
        cart=Cart(),
        llm=False,
    )
    found, notes = extract(message)
    if prev is None and "adults" not in found:
        notes.append("взрослых: 2 (состав не назван)")
    idx = _ordinal(message)
    if old.stage == "inspire" and idx is not None and idx < len(old.plan.destinations):
        found["destinations"] = [old.plan.destinations[idx].name]
    spec = merge(old.plan.spec, found, notes)

    def reply(stage: Stage, plan: Plan, text: str, cart: Cart = old.cart, **kw) -> ChatReply:
        return ChatReply(
            session_id=sid, stage=stage, reply=text, plan=plan, cart=cart, llm=False, **kw
        )

    if prev is None and (questions := blocking_questions(spec)):
        return reply("clarify", Plan(spec=spec), _text_clarify(questions), questions=questions)
    spec = assume(spec)
    if not spec.destinations:
        plan = Plan(spec=spec, destinations=suggest_destinations(spec))
        return reply("inspire", plan, _text_inspire(plan))

    plan = old.plan.model_copy(update={"spec": spec})
    trip_changed = old.plan.spec.model_dump(include=TRIP_FIELDS) != spec.model_dump(
        include=TRIP_FIELDS
    )
    if trip_changed or not plan.offers:
        plan = Plan(spec=spec, destinations=old.plan.destinations)
        plan.offers = find_offers(spec, spec.destinations[0])
        plan.selected, plan.explanations = rank(spec, plan.offers)
        if old.plan.offers:
            plan.changes = plan_changes(old.plan.spec, spec)
        if not plan.offers:
            # TODO(T4, #8): the geocoder and synthetic offers cover any city.
            text = (
                f"Направления «{spec.destinations[0]}» пока нет в каталоге, предложений нет. "
                f"{HINTS['select']}"
            )
            return reply("select", plan, text, Cart())
        return reply("compare", plan, _text_compare(plan), Cart())

    t = message.lower()
    if old.stage == "compare" and (idx is not None or _confirmed(t)):
        hotels = [o for o in plan.offers if o.kind == "hotel"]
        if idx is not None and idx < len(hotels):
            ids = {h.offer_id for h in hotels}
            plan.selected = [hotels[idx].offer_id] + [i for i in plan.selected if i not in ids]
        plan.timeline, plan.issues = build_timeline(plan)
        return reply("assemble", plan, _text_assemble(plan))
    if old.stage == "assemble" and _confirmed(t):
        cart = fill_cart(plan)
        text = (
            f"В корзине {len(cart.items)} поз. на {_rub(cart.total_rub)} "
            f"(синтетика, оплата имитируется). {HINTS['checkout']}"
        )
        return reply("checkout", plan, text, cart)
    if old.stage == "checkout" and BOOK_RE.search(t) and not NOT_BOOK_RE.search(t):
        voucher = book(spec, old.cart)
        text = f"Бронь {voucher.booking_id} оформлена на {_rub(voucher.total_rub)}. Оплата имитирована."
        return reply("booked", plan, text, voucher=voucher)
    return reply(old.stage, plan, HINTS.get(old.stage, "Уточните запрос."))


def chat(req: ChatRequest, store: SessionStore) -> ChatReply:
    """POST /api/chat: an unknown session_id starts a new session with that id."""
    sid = req.session_id or uuid4().hex
    out = step(store.get(sid), sid, req.message)
    store.put(out)
    return out
