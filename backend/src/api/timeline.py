"""Timeline checker (T8, #14): arrival -> transfer -> check-in -> check-out -> transfer -> return.

Deterministic, no network, no LLM. Reads only plan.selected and plan.offers, and of the spec
only the own tickets (C5, #408: offers.own_legs); an edit of them re-runs the offers node of
api/dag.py, so the timeline still follows its inputs. Every time is computed in UTC and shown in the local zone of the
event place (TRAP-5): the same ZoneInfo on both sides would subtract wall clocks and lose or
gain an hour at a DST change. Texts are Russian; numbers come from offers or from the
constants below. `fixes` hold offer_id from plan.offers only (TRAP-4): add-ons of the hotel
(early check-in, night before, late check-out) and other legs on the same route (change of
flight). Luggage storage is not an offer: the text only advises to ask the hotel.

A transfer counts only when it is selected (#200). Without it the guest goes on their own:
the timeline says so and adds the road to the arrival (or before the departure). The road is
the time of the pair's transfer offer (offers.py derives it from the distance to the hub, with
the way out after an arrival), never shorter than OWN_WAY; never the time of a transfer that
is not taken.

Extra kind is ExtraOffer.tag, the time of an add-on is ExtraOffer.at (ADR-0002, #307).
Without them: the offer_id suffix (-early, -late, -prev) and the time in the title
(«с 06:00», «до 18:00»). The «≈N км» of a transfer title is text only: no km, no km text.

All limits below are team estimates without a source.
"""

import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from api.contracts import (
    ExtraOffer,
    HotelOffer,
    Offer,
    Plan,
    TimelineEvent,
    TimelineIssue,
    TransferOffer,
    TransportOffer,
)
from api.offers import MAX_DRIVE, drive_time, own_legs, resolve_place, road_nights, transfer_id

EARLY_WAIT = timedelta(hours=3)  # arrival earlier than this before check-in is an issue
LATE_WAIT = timedelta(hours=3)  # time between check-out and leaving for the station or airport
NIGHT_FROM, NIGHT_TO = time(23, 0), time(5, 0)  # reception and transfers may not work
BUFFER = {"flight": timedelta(minutes=30), "train": timedelta(minutes=10), "car": timedelta()}
# Arrive at the airport or station this long before the departure.
MARGIN = {"flight": timedelta(hours=2), "train": timedelta(minutes=40), "car": timedelta()}
# BUFFER, MARGIN and OWN_WAY are team estimates without a source, as the limits above.
# Without a selected transfer the guest goes between the hub and the hotel on their own: the
# way out of the airport or the station and a taxi. The minimum; a far hub takes longer
# (_Trip.own_way). The car drives to the hotel itself.
OWN_WAY = {
    "flight": timedelta(hours=1, minutes=30),
    "train": timedelta(minutes=45),
    "car": timedelta(),
}
MAX_ALTERNATIVES = 3
VERBS = {
    "flight": ("Вылет", "Прилёт"),
    "train": ("Отправление", "Прибытие"),
    "car": ("Старт на машине", "Приезд на машине"),
}
HUBS = {"flight": "в аэропорт", "train": "на вокзал"}  # with the preposition: «на вокзал»
DEPARTURE_OF = {"flight": "вылета", "train": "отправления", "car": "старта"}
TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
KM_RE = re.compile(r"≈\s*(\d+(?:[.,]\d+)?)\s*км")  # the distance in a transfer title (offers.py)


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def _local(day, at: time, tz: ZoneInfo) -> datetime:
    """Wall-clock time of a day in a zone as a real instant; a time inside a DST gap moves on."""
    return _utc(datetime.combine(day, at, tz)).astimezone(tz)


def _hm(dt: datetime) -> str:
    return f"{dt:%H:%M}"


def _span(delta: timedelta) -> str:
    hours, minutes = divmod(max(0, int(delta.total_seconds() // 60)), 60)
    if hours and minutes:
        return f"{hours} ч {minutes:02d} мин"
    return f"{hours} ч" if hours else f"{minutes} мин"


def _night(dt: datetime) -> bool:
    t = dt.timetz().replace(tzinfo=None)
    return t >= NIGHT_FROM or t < NIGHT_TO


def _extra_time(extra: ExtraOffer | None) -> time | None:
    """ExtraOffer.at; without it the HH:MM in the title (the old rule, ADR-0002)."""
    if extra and extra.at:
        return extra.at
    m = TIME_RE.search(extra.title) if extra else None
    return time(int(m.group(1)), int(m.group(2))) if m else None


def _tag(extra: ExtraOffer) -> str | None:
    """ExtraOffer.tag; without it the offer_id suffix "-early", "-late", "-prev" (old rule)."""
    if extra.tag:
        return extra.tag
    return next((t for t in ("early", "late", "prev") if extra.offer_id.endswith(f"-{t}")), None)


def _at_place(dt: datetime, city: str) -> datetime:
    """The instant in the local zone of a city (TRAP-5); unknown city: as it came."""
    place = resolve_place(city)
    return dt.astimezone(ZoneInfo(place["tz"])) if place else dt


class _Trip:
    """The selected hotel and legs with the offers around them."""

    def __init__(self, plan: Plan):
        self.by_id: dict[str, Offer] = {o.offer_id: o for o in plan.offers}
        chosen = [self.by_id[i] for i in plan.selected if i in self.by_id]
        self.picked = {o.offer_id for o in chosen}
        self.hotel = next((o for o in chosen if isinstance(o, HotelOffer)), None)
        self.legs = [o for o in chosen if isinstance(o, TransportOffer)]
        self.tz = ZoneInfo(self.hotel.tz) if self.hotel else None
        city = self.hotel.city if self.hotel else None
        self.out = next((x for x in self.legs if x.destination == city), None)
        self.back = next((x for x in self.legs if x.origin == city), None)
        # Own tickets: legs of the timeline, not of the cart; "picked" so that only a selected
        # transfer counts (transfer()).
        self.own = own_legs(plan.spec, city) if city else []
        self.picked |= {x.offer_id for x in self.own}
        self.out = self.out or next((x for x in self.own if x.destination == city), None)
        self.back = self.back or next((x for x in self.own if x.origin == city), None)

    def room(self) -> tuple[HotelOffer, ZoneInfo]:
        """The hotel and its zone. Callers check the hotel first; no hidden server zone."""
        assert self.hotel is not None and self.tz is not None
        return self.hotel, self.tz

    def extra(self, tag: str) -> ExtraOffer | None:
        h = self.hotel
        return next(
            (
                o
                for o in self.by_id.values()
                if isinstance(o, ExtraOffer)
                and h
                and o.for_offer_id == h.offer_id
                and _tag(o) == tag
            ),
            None,
        )

    def has(self, tag: str) -> bool:
        x = self.extra(tag)
        return x is not None and x.offer_id in self.picked

    def available(self, tag: str) -> ExtraOffer | None:
        """The add-on if the hotel offers it and it is not bought yet."""
        x = self.extra(tag)
        return x if x is not None and x.offer_id not in self.picked else None

    def ready(self) -> datetime:
        """The earliest moment the room is ours."""
        h, tz = self.room()
        if self.has("prev"):
            return _local(h.date_in - timedelta(days=1), h.check_in, tz)
        early = _extra_time(self.extra("early")) if self.has("early") else None
        return _local(h.date_in, min(early or h.check_in, h.check_in), tz)

    def leave_by(self) -> datetime:
        """The moment the room must be free."""
        h, tz = self.room()
        late = _extra_time(self.extra("late")) if self.has("late") else None
        return _local(h.date_out, max(late or h.check_out, h.check_out), tz)

    def offered(self, leg: TransportOffer) -> TransferOffer | None:
        """The transfer of the hotel and `leg` among the offers, selected or not."""
        if not self.hotel or leg.mode == "car":
            return None
        x = self.by_id.get(transfer_id(self.hotel.offer_id, leg.offer_id))
        return x if isinstance(x, TransferOffer) else None

    def refused(self, leg: TransportOffer) -> bool:
        """The user took out the transfer of this direction (#200): the selected leg of the
        same route has a transfer offer, and it is not selected. The plan selects the transfer
        of every pair (pick_hotel); only the user takes it out. Same rule as api/cart.py."""
        now = next(
            (x for x in self.legs if (x.origin, x.destination) == (leg.origin, leg.destination)),
            None,
        )
        x = self.offered(now) if now else None
        return x is not None and x.offer_id not in self.picked

    def transfer(self, leg: TransportOffer) -> TransferOffer | None:
        """The transfer the trip has with `leg` (TRAP-5). A selected leg: only a selected
        transfer, never one derived from the pair. A leg offered as a fix: the transfer it
        brings into the cart, none if the user refused the transfer of this direction."""
        x = self.offered(leg)
        if x is None:
            return None
        if leg.offer_id in self.picked:
            return x if x.offer_id in self.picked else None
        return None if self.refused(leg) else x

    def own_way(self, leg: TransportOffer) -> tuple[timedelta, str]:
        """The road between the hub of `leg` and the hotel without a transfer, and its distance
        text. The time of the pair's transfer offer comes from the distance (offers.py): after an
        arrival from the landing to the hotel, before a departure the ride. Not under OWN_WAY."""
        x = self.offered(leg)
        if x is None:
            return OWN_WAY[leg.mode], ""
        start = leg.arrive_at if self.hotel and leg.destination == self.hotel.city else x.depart_at
        m = KM_RE.search(x.title)
        km = f", ≈{m.group(1)} км" if m else ""
        return max(OWN_WAY[leg.mode], _utc(x.arrive_at) - _utc(start)), km

    def stay_day(self, dt: datetime):
        """The calendar day of the stay: the small hours still belong to the day before."""
        local = dt.astimezone(self.room()[1])
        if local.timetz().replace(tzinfo=None) < NIGHT_TO:
            return local.date() - timedelta(days=1)
        return local.date()

    def alternatives(self, leg: TransportOffer, check, clean: bool) -> list[str]:
        """Other legs of the same route, nearest in time first. They have no errors, and no
        warnings either when `clean` (the issue to fix is a warning, so a swap must cure it)."""
        found = []
        for o in self.by_id.values():
            if not isinstance(o, TransportOffer) or o.offer_id in self.picked:
                continue
            if (o.origin, o.destination) != (leg.origin, leg.destination):
                continue
            if o.mode != "car" and self.offered(o) is None:
                continue
            issues = check(self, o, False)
            if issues if clean else any(i.severity == "error" for i in issues):
                continue
            found.append((len(issues), abs(_utc(o.depart_at) - _utc(leg.depart_at)), o.offer_id))
        return [i for _, _, i in sorted(found)[:MAX_ALTERNATIVES]]


def _fixes(t: _Trip, tags: tuple[str, ...], leg: TransportOffer, check, fixes: bool) -> list[str]:
    if not fixes:
        return []
    extras = [x.offer_id for x in map(t.available, tags) if x]
    return extras + t.alternatives(leg, check, clean=True)


def _hotel_at(t: _Trip, tr: TransferOffer | None, leg: TransportOffer) -> datetime:
    """When the guest is at the hotel after `leg`: the transfer arrival, or the own way."""
    return _utc(tr.arrive_at) if tr else _utc(leg.arrive_at) + t.own_way(leg)[0]


def _reach(tr: TransferOffer | None, leg: TransportOffer, shown: datetime) -> str:
    if tr or leg.mode == "car":
        return f"в отель вы приедете в {_hm(shown)}"
    return f"до отеля добираетесь сами — примерно к {_hm(shown)}"


def _arrival_issues(t: _Trip, leg: TransportOffer, fixes: bool = True) -> list[TimelineIssue]:
    """Issues between the arrival of `leg` and the check-in."""
    h, tz = t.room()
    tr = t.transfer(leg)
    verb = VERBS[leg.mode][1].lower()
    arrive = leg.arrive_at.astimezone(tz)
    if tr and _utc(tr.depart_at) - _utc(leg.arrive_at) < BUFFER[leg.mode]:
        gap = _span(_utc(tr.depart_at) - _utc(leg.arrive_at))
        text = (
            f"Короткая стыковка: {verb} в {_hm(arrive)}, а трансфер отправляется через {gap} — "
            f"нужно не меньше {_span(BUFFER[leg.mode])}."
        )
        return [
            TimelineIssue(
                severity="error",
                text=text,
                fixes=_alt(t, leg, _arrival_issues, fixes, clean=False),
            )
        ]
    at_hotel = _hotel_at(t, tr, leg)
    stay = t.stay_day(at_hotel)
    if stay > h.date_in:
        text = (
            f"Вы приедете в отель {at_hotel.astimezone(tz):%d.%m в %H:%M}, а заезд по брони "
            f"{h.date_in:%d.%m}: ночь заезда оплачена, но не используется."
        )
        return [
            TimelineIssue(
                severity="error",
                text=text,
                fixes=_alt(t, leg, _arrival_issues, fixes, clean=False),
            )
        ]
    ready = t.ready()
    wait = _utc(ready) - at_hotel
    shown = at_hotel.astimezone(tz)
    night = _night(shown)
    tr_start = tr.depart_at.astimezone(tz) if tr else None
    night_tr = tr_start is not None and _night(tr_start)
    if wait > EARLY_WAIT:
        text = (
            f"{VERBS[leg.mode][1]} в {_hm(arrive)}, {_reach(tr, leg, shown)}, "
            f"а заезд с {_hm(ready)}: ждать {_span(wait)}."
        )
        if night:
            text += " Ночью стойка отеля может не работать."
        text += " Багаж можно оставить на хранение — уточните у отеля."
        fix_ids = _fixes(t, ("early", "prev"), leg, _arrival_issues, fixes)
        return [TimelineIssue(severity="warn", text=text, fixes=fix_ids)]
    if night or night_tr:
        parts = [f"{VERBS[leg.mode][1]} в {_hm(arrive)}"]
        if tr_start is not None and night_tr:
            parts.append(f"ночной трансфер в {_hm(tr_start)}")
        parts.append(_reach(tr, leg, shown))
        text = (
            f"{', '.join(parts)} — поздний заезд, после {NIGHT_FROM:%H:%M} стойка может не "
            "работать. Предупредите отель заранее"
        ) + (" и подтвердите ночной трансфер." if tr else ".")
        return [
            TimelineIssue(severity="warn", text=text, fixes=_alt(t, leg, _arrival_issues, fixes))
        ]
    return []


def _alt(t: _Trip, leg: TransportOffer, check, fixes: bool, clean: bool = True) -> list[str]:
    return t.alternatives(leg, check, clean) if fixes else []


def _return_issues(t: _Trip, leg: TransportOffer, fixes: bool = True) -> list[TimelineIssue]:
    """Issues between the check-out and the departure of `leg`."""
    h, tz = t.room()
    tr = t.transfer(leg)
    depart = _utc(leg.depart_at)
    verb = VERBS[leg.mode][0].lower()
    hub = HUBS.get(leg.mode, "")

    def alt(clean: bool = True) -> list[str]:
        return _alt(t, leg, _return_issues, fixes, clean)

    stay = t.stay_day(depart)
    if stay != h.date_out:
        how = "раньше" if stay < h.date_out else "позже"
        text = (
            f"Обратный рейс ({verb} {depart.astimezone(tz):%d.%m в %H:%M}) {how} выезда из отеля "
            f"{h.date_out:%d.%m}: "
        ) + (
            "оплаченные ночи не используются."
            if stay < h.date_out
            else "после выезда ночь без номера."
        )
        return [TimelineIssue(severity="error", text=text, fixes=alt(clean=False))]
    margin = MARGIN[leg.mode]
    if tr and _utc(tr.arrive_at) > depart - margin:
        left = depart - _utc(tr.arrive_at)
        text = (
            f"Трансфер приедет {hub} в {_hm(tr.arrive_at.astimezone(tz))}, до {DEPARTURE_OF[leg.mode]} "
            f"({_hm(depart.astimezone(tz))}) останется {_span(left)} — нужно не меньше "
            f"{_span(margin)}."
        )
        return [TimelineIssue(severity="error", text=text, fixes=alt(clean=False))]
    leave = _utc(tr.depart_at) if tr else depart - margin - t.own_way(leg)[0]
    out = t.leave_by()
    gap = leave - _utc(out)
    found: list[TimelineIssue] = []
    if tr and _night(tr.depart_at.astimezone(tz)):
        text = (
            f"Ночной трансфер {hub}: выезд из отеля в {_hm(tr.depart_at.astimezone(tz))}. "
            "Подтвердите его заранее."
        )
        found.append(TimelineIssue(severity="warn", text=text, fixes=alt()))
    if gap > LATE_WAIT:  # a night transfer can come with a long wait after the check-out
        text = (
            f"Выезд из отеля в {_hm(out)}, а {hub or 'в пункт отправления'} нужно выехать только "
            f"в {_hm(leave.astimezone(tz))}: ждать {_span(gap)}. "
            "Вещи можно оставить на хранение — уточните у отеля."
        )
        late = t.available("late")
        ids = ([late.offer_id] if late else []) if fixes else []
        found.append(TimelineIssue(severity="warn", text=text, fixes=ids + alt()))
    return found


def _leg_issues(t: _Trip) -> list[TimelineIssue]:
    out: list[TimelineIssue] = []
    for leg in t.legs:
        if _utc(leg.arrive_at) <= _utc(leg.depart_at):
            out.append(
                TimelineIssue(severity="error", text=f"{leg.title}: прибытие не позже отправления.")
            )
    if t.out and t.back and _utc(t.back.depart_at) < _utc(t.out.arrive_at):
        out.append(
            TimelineIssue(severity="error", text="Обратный рейс отправляется раньше прибытия туда.")
        )
    return out


def _drive_issues(t: _Trip, leg: TransportOffer, fixes: bool = True) -> list[TimelineIssue]:
    """A car leg with a driving day over MAX_DRIVE (#276). Nights on the road come from the
    title (offers.road_nights); the driving days are equal (offers.drive_time)."""
    if leg.mode != "car":
        return []
    stops = road_nights(leg)
    day = drive_time(leg) / (stops + 1)
    if day <= MAX_DRIVE:
        return []
    what = "без ночёвки" if not stops else "за один день, ночёвок в пути мало"
    text = (
        f"{leg.origin} → {leg.destination}: {_span(day)} за рулём {what} — больше "
        f"{_span(MAX_DRIVE)}. Разбейте дорогу на дни с ночёвкой в пути."
    )
    return [TimelineIssue(severity="warn", text=text, fixes=_alt(t, leg, _drive_issues, fixes))]


def _leg_events(leg: TransportOffer, t: _Trip) -> list[TimelineEvent]:
    start, end = VERBS[leg.mode]
    depart = _at_place(leg.depart_at, leg.origin)
    arrive = _at_place(leg.arrive_at, leg.destination)
    if t.hotel and leg is t.back:
        depart = leg.depart_at.astimezone(t.room()[1])
    if t.hotel and leg is t.out:
        arrive = leg.arrive_at.astimezone(t.room()[1])
    return [
        TimelineEvent(
            at=depart, title=f"{start}: {leg.origin} → {leg.destination}", offer_id=leg.offer_id
        ),
        TimelineEvent(at=arrive, title=f"{end}: {leg.destination}", offer_id=leg.offer_id),
    ]


def _leg_id(t: _Trip, leg: TransportOffer) -> str | None:
    """offer_id of an event of `leg`; own tickets are no offer."""
    return None if leg in t.own else leg.offer_id


def _events(t: _Trip) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for leg in t.legs:
        events += _leg_events(leg, t)
    for leg in t.own:
        verb = VERBS[leg.mode][1 if leg is t.out else 0]
        events.append(
            TimelineEvent(at=leg.arrive_at.astimezone(t.room()[1]), title=f"{verb}: свои билеты")
        )
    if t.hotel:
        h, tz = t.room()
        ready = t.ready()
        tr_in = t.transfer(t.out) if t.out else None
        if tr_in:
            events.append(
                TimelineEvent(
                    at=tr_in.depart_at.astimezone(tz), title=tr_in.title, offer_id=tr_in.offer_id
                )
            )
        if t.out and not tr_in and t.out.mode != "car":
            way, km = t.own_way(t.out)
            events.append(
                TimelineEvent(
                    at=t.out.arrive_at.astimezone(tz),
                    title=f"До отеля добираетесь сами: трансфер не выбран{km}, "
                    f"на дорогу закладываем {_span(way)}",
                    offer_id=_leg_id(t, t.out),
                )
            )
        arrive = _hotel_at(t, tr_in, t.out) if t.out else None
        if arrive and arrive < _utc(ready):
            events.append(
                TimelineEvent(
                    at=arrive.astimezone(tz),
                    title=f"Приезд в отель, ждём заселения: {h.title}",
                    offer_id=h.offer_id,
                )
            )
        events.append(
            TimelineEvent(
                at=max(_utc(ready), arrive or _utc(ready)).astimezone(tz),
                title=f"Заселение: {h.title}",
                offer_id=h.offer_id,
            )
        )
        events.append(
            TimelineEvent(at=t.leave_by(), title=f"Выезд из отеля: {h.title}", offer_id=h.offer_id)
        )
        tr_out = t.transfer(t.back) if t.back else None
        if tr_out:
            events.append(
                TimelineEvent(
                    at=tr_out.depart_at.astimezone(tz),
                    title=tr_out.title,
                    offer_id=tr_out.offer_id,
                )
            )
        elif t.back and t.back.mode != "car":
            way, km = t.own_way(t.back)
            events.append(
                TimelineEvent(
                    at=(_utc(t.back.depart_at) - MARGIN[t.back.mode] - way).astimezone(tz),
                    title=f"Выезд {HUBS[t.back.mode]}: добираетесь сами, трансфер не выбран{km}, "
                    f"на дорогу закладываем {_span(way)}",
                    offer_id=_leg_id(t, t.back),
                )
            )

    # At the same instant the check-out comes before the start back (a car at CAR_BACK).
    def starts_back(e: TimelineEvent) -> bool:
        return (
            t.back is not None
            and e.offer_id == t.back.offer_id
            and e.title.startswith(f"{VERBS[t.back.mode][0]}:")
        )

    return sorted(events, key=lambda e: (_utc(e.at), starts_back(e)))


def build_timeline(plan: Plan) -> tuple[list[TimelineEvent], list[TimelineIssue]]:
    """Events of the selected items in local time, and the docking issues with their fixes.

    Errors come first. Add-ons already in `plan.selected` change the check-in and check-out
    times, so a bought add-on removes the issue it fixes.
    """
    t = _Trip(plan)
    issues = _leg_issues(t)
    for leg in t.legs:
        issues += _drive_issues(t, leg)
    if t.hotel:
        if t.out:
            issues += _arrival_issues(t, t.out)
        if t.back:
            issues += _return_issues(t, t.back)
    issues.sort(key=lambda i: i.severity != "error")
    return _events(t), issues
