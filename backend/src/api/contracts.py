"""Agent API contract (ADR-0001). Consumers: web/src/contracts.ts (mirror), web/src/api.ts,
web/src/format.ts, web/e2e, checks.py, tests; agent modules: T2 state machine, T3/T11 extractor,
T4/T5 providers, T6 DAG, T7 optimizer, T8 timeline checker, T9 cart, T10 web,
T12 inspiration, T14 eval.

Routes: POST /api/chat (ChatRequest -> ChatReply), GET /api/session/{session_id} -> ChatReply,
POST /api/cart (CartAction -> Cart), POST /api/checkout (CheckoutRequest -> Voucher;
409 when the total differs from the cart or the booking provider refuses),
GET /api/mode -> LlmMode (known before the first reply; consumers: web/src/api.ts getMode,
web/src/App.tsx, web/src/Sources.tsx, web/e2e/noai-badge.spec.ts, backend/tests/test_api.py).
ChatRequest.real_data and ChatRequest.llm (#490, #566: both on by default; consumers:
api/main.py, api/offers.py real_data/hotel_port(), api/llm.py allowed, api/partner.py plan_reply (keeps the switches of the request),
api/checks.py _post (eval: llm on, real_data off), web/src/api.ts, web/src/App.tsx,
web/src/Sources.tsx, backend/tests/test_sources.py, README.md).
GET /health stays exactly {"status": "ok"} (task/rules.md): the LLM mode lives only in /api/mode.

Partner API (#289, #435): every route above also lives under /api/v1 with the same models.
POST /api/v1/plan (ChatRequest -> ChatReply): the chat driven to a filled cart (stage
"checkout") or to a blocking question; it books nothing. With PARTNER_API_KEYS set, /api/v1
routes but health and mode need the header X-API-Key, else 401; /api/* (the web) needs none.
POST /api/checkout and /api/v1/checkout take the optional header Idempotency-Key: a repeat of
the key returns the first Voucher, the key with another body is 422. A new booking is POSTed as
Voucher JSON to BOOKING_WEBHOOK_URL. CORS: ALLOWED_ORIGINS. No new models: the web mirror does
not change. Consumers: api/main.py, api/partner.py, api/webhook.py, api/cart.py (webhook),
api/store.py (idempotency_keys), backend/src/api/embed.js and examples/embed.html (the widget
loads the web app), README «Как встроить», backend/tests/test_partner_api.py,
backend/tests/test_api_v1.py.

Money: whole roubles for everything (all guests, all nights, taxes included). Time: ISO 8601
with a time zone, local to the place of the event. Only providers write prices and times
(TRAP-4); LLM text refers to offer_id.

A change is a separate PR labelled contract; web/src/contracts.ts changes in the same PR.
Ports for data sources and the purchase (Place, HotelProvider, TransportProvider,
TransferProvider, BookingProvider; ADR-0002) are in the section «Порты» at the end, with their
consumers; they are backend only.
"""

from datetime import date, datetime, time
from typing import Annotated, Literal, Protocol, Required, TypedDict

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Stage = Literal["clarify", "inspire", "select", "compare", "assemble", "checkout", "booked"]
Meal = Literal["RO", "BB", "HB", "FB", "AI"]
Mode = Literal["flight", "train", "car"]
# Booking status from the BookingProvider; a refusal is an exception, not a status (ADR-0002).
BookingStatus = Literal["created", "confirmed", "cancelled"]
# What the user cares about when offers are compared; the optimizer turns the order into weights.
Priority = Literal["price", "cancellation", "meal", "location", "travel_time", "rating"]
# Hotel features that make it fit a guest: child (family, kids_club), hard to walk
# (wheelchair, elevator), sea (beach), dog by car (parking). From OSM tags or synthetic.
Amenity = Literal["family", "kids_club", "wheelchair", "elevator", "beach", "parking"]


class TripSpec(BaseModel):
    """Single source of truth about the trip. An empty field means unknown, not "any"."""

    origin: str | None = None
    destinations: list[str] = []  # empty: the agent suggests destinations
    date_from: date | None = None
    date_to: date | None = None
    month: int | None = Field(None, ge=1, le=12)  # when there are no exact dates
    nights: int | None = Field(None, ge=1)
    adults: int = Field(2, ge=1)
    children_ages: list[int] = []
    pets: bool = False
    budget_rub: int | None = Field(None, ge=0)
    # hotel only, all nights, ₽; budget_rub is the whole trip
    hotel_budget_rub: int | None = Field(None, ge=0)
    meal: Meal | None = None
    refundable: bool | None = None
    modes: list[Mode] = []
    max_travel_hours: float | None = None
    # C5 (#408): the user has tickets. No transport is searched; transfers start from the
    # user's own arrival and end before the own departure (local to the destination).
    # Consumers: api/extract.py (rules), model.py (assume, compare text), offers.py (own_legs),
    # optimizer.py, timeline.py, dag.py; web/src/Board.tsx (SpecSummary).
    own_transport: bool = False
    arrive_at: AwareDatetime | None = None
    leave_at: AwareDatetime | None = None
    wishes: list[str] = []  # user's own words: sea, skiing, hard to walk
    priorities: list[Priority] = []  # most important first; empty: default weights
    assumptions: list[str] = []  # what the agent assumed; shown to the user


class Cancellation(BaseModel):
    refundable: bool
    free_until: AwareDatetime | None = None


class Sourced(BaseModel):
    """Where a price comes from (#469, TRAP-7). source: the provider, "101hotels",
    "travelpayouts", "tutu" or "синтетика" (the default: a generated price is never real).
    fetched_at: when the provider answered (a cached answer: when it was fetched); None for a
    synthetic offer, which has no fetch. Every adapter fills both; synthetic=False needs a real
    source. Consumers: api/offers.py (hotel, transport), api/live_hotels.py, api/travelpayouts.py,
    api/tutu.py, web/src/Board.tsx (Price, sourceOf), the TRAP-7 tests in backend/tests/test_live_hotels.py, test_tutu.py, test_travelpayouts.py,
    test_offers.py."""

    source: str = "синтетика"
    fetched_at: AwareDatetime | None = None


class HotelOffer(Sourced):
    kind: Literal["hotel"] = "hotel"
    offer_id: str
    title: str
    city: str
    tz: str  # IANA zone of the hotel, e.g. Europe/Moscow
    date_in: date
    date_out: date
    adults: int = Field(ge=1)
    children_ages: list[int] = []
    price_rub: int = Field(gt=0)
    synthetic: bool = True
    stars: int | None = None
    rating: float | None = None
    meal: Meal
    cancellation: Cancellation
    distance_center_km: float | None = None
    lat: float
    lon: float
    check_in: time = time(14, 0)
    check_out: time = time(12, 0)
    pets_allowed: bool = False
    amenities: list[Amenity] = []
    description: str = ""  # source text is data, not instructions (S4)


class TransportOffer(Sourced):
    kind: Literal["transport"] = "transport"
    offer_id: str
    title: str
    mode: Mode
    origin: str
    destination: str
    depart_at: AwareDatetime
    arrive_at: AwareDatetime
    changes: int = 0
    price_rub: int = Field(gt=0)
    synthetic: bool = True
    pets_allowed: bool = False


class TransferOffer(Sourced):
    kind: Literal["transfer"] = "transfer"
    offer_id: str
    title: str
    depart_at: AwareDatetime
    arrive_at: AwareDatetime
    price_rub: int = Field(gt=0)
    synthetic: bool = True
    # The pair of the ride (ADR-0002, #307). Both set: offers_for_place makes offer_id the core
    # key transfer_id(hotel_offer_id, leg_offer_id) and keeps the provider id in ref. None: the
    # offer_id must already be that key (the old rule).
    hotel_offer_id: str | None = None
    leg_offer_id: str | None = None
    ref: str | None = None  # the id of the ride at the provider


class ExtraOffer(Sourced):
    """Add-on to another item: early check-in, late check-out, night before."""

    kind: Literal["extra"] = "extra"
    offer_id: str
    title: str
    for_offer_id: str
    price_rub: int = Field(ge=0)
    synthetic: bool = True
    # What the timeline reads (ADR-0002, #307): tag "early", "late" or "prev" (night before);
    # another tag or None is a plain add-on. at: the local time of the hotel when the room is
    # ours ("early") or must be free ("late"). None: the old parse, the offer_id suffix
    # "-early"/"-late"/"-prev" and the HH:MM in the title.
    tag: str | None = None
    at: time | None = None


Offer = Annotated[
    HotelOffer | TransportOffer | TransferOffer | ExtraOffer, Field(discriminator="kind")
]


class Destination(BaseModel):
    name: str
    country: str
    lat: float
    lon: float
    why: str
    est_total_rub: int  # whole-trip estimate
    synthetic: bool = True
    air_c: float | None = None  # climate of the travel month
    water_c: float | None = None
    travel_hours: float
    tradeoffs: list[str] = []
    violates: list[str] = []  # user requirements this option breaks, e.g. flight over 5 h
    risks: list[str] = []  # e.g. visa, rainy season


class Explanation(BaseModel):
    offer_id: str
    why: str
    tradeoffs: list[str] = []  # what was sacrificed
    risks: list[str] = []


class TimelineEvent(BaseModel):
    at: AwareDatetime
    title: str
    offer_id: str | None = None


class TimelineIssue(BaseModel):
    severity: Literal["warn", "error"]
    text: str
    fixes: list[str] = []  # offer_id of add-ons or replacements that fix the issue


class Change(BaseModel):
    """One before -> after line of a re-plan; the DAG code fills it, not the LLM (TRAP-4)."""

    what: str
    before: str
    after: str


class Question(BaseModel):
    text: str
    options: list[str] = []  # quick answers for buttons; empty: free text


class Bundle(BaseModel):
    """Whole-trip option: hotel + transport + transfers, compared as one (a cheap hotel far away
    can lose to the transfer). The optimizer fills it, not the LLM (TRAP-4)."""

    bundle_id: str
    label: str  # e.g. cheapest, best fit
    offer_ids: list[str]
    total_rub: int = Field(gt=0)
    travel_hours: float  # door to door, one way
    tradeoffs: list[str] = []


class Plan(BaseModel):
    spec: TripSpec
    destinations: list[Destination] = []
    offers: list[Offer] = []  # candidates to compare
    bundles: list[Bundle] = []  # whole-trip options built from offers
    selected: list[str] = []  # offer_id of selected items
    explanations: list[Explanation] = []
    timeline: list[TimelineEvent] = []
    issues: list[TimelineIssue] = []
    changes: list[Change] = []  # after a re-plan


class Cart(BaseModel):
    items: list[Offer] = []
    total_rub: int = 0


class Guest(BaseModel):
    """Lead guest typed at checkout (#367). Simulated: stored with the session in the SQLite
    session store (data/), not sent to the booking provider."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    contact: str = Field("", max_length=100)  # phone or e-mail; "" when not given


class Voucher(BaseModel):
    booking_id: str
    items: list[Offer]
    spec: TripSpec  # dates and guests of the booking
    total_rub: int
    created_at: AwareDatetime
    status: BookingStatus = "confirmed"
    simulated: bool = True  # True: booking and payment are simulated; the voucher says so
    guest: Guest | None = None  # None: no name given (chat «подтверждаю», old clients)


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"message": "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч"},
                {"session_id": "<session_id из ответа>", "message": "а подешевле?"},
            ]
        }
    )

    session_id: str | None = None  # None: new session
    message: str = Field(min_length=1)
    # #566: both on by default (the web «Синтетика» switch sends real_data=false). real_data:
    # the live sources (101hotels, Travelpayouts when TRAVELPAYOUTS_TOKEN is set), each with a
    # synthetic fallback; false: every source synthetic. llm: the model when it is configured,
    # else rules (TRAP-1). Per request: parallel sessions do not share them.
    real_data: bool = True
    llm: bool = True


class ChatReply(BaseModel):
    session_id: str
    stage: Stage
    reply: str
    questions: list[Question] = []  # blocking clarifications only; at most 2
    plan: Plan
    cart: Cart
    voucher: Voucher | None = None
    llm: bool  # False: rule-based mode without a model (TRAP-1)


class LlmMode(BaseModel):
    """GET /api/mode: what the switches of ChatRequest can turn on (#490)."""

    llm: bool  # False: no model in .env, every reply is rule-based (TRAP-1)
    real_data: bool  # False: no live source is set up (LIVE_HOTELS=0, no TRAVELPAYOUTS_TOKEN)


class CartAction(BaseModel):
    session_id: str
    action: Literal["add", "remove"]
    offer_id: str


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "session_id": "<session_id из ответа>",
                    "confirm_total_rub": 24100,
                    "guest": {"name": "Анна Петрова", "contact": "+7 900 000-00-00"},
                }
            ]
        }
    )

    session_id: str
    confirm_total_rub: int  # total the user saw when confirming
    guest: Guest | None = None  # to be copied to Voucher.guest by checkout (#367)


# --- Порты (ADR-0002, D-023) ---------------------------------------------------------------
# Data sources and the purchase behind the core. One Protocol per source; the active adapter
# is one module variable: api/offers.py (HOTELS, TRANSPORT, TRANSFERS), api/cart.py (BOOKING).
# Swap it with one assignment at start-up (main.py) or in a test; no factories, no env switch.
# Exception (#490): offers.REAL_TRANSPORT (Travelpayouts) is built at start-up and serves only
# the chat requests with real_data (offers.transport()); without real_data offers.hotel_port()
# drops the live layers of HOTELS (#566).
# Backend only: no mirror in web/src/contracts.ts, the types the web sees do not change.
# Consumers: api/offers.py (adapters, offers_for_place), via find_offers: api/dag.py (the live
# ports, only the chosen place). The estimate path (ESTIMATE_* in api/offers.py, synthetic):
# api/fixtures.py (probe_legs, travel_hours), api/inspire.py (hours door to door and at the
# wheel, #321; prices), api/model.py (_pet_dead_cities), api/checks.py (eval).
# Fields the core reads (#307): ExtraOffer.tag, at: api/timeline.py; TransferOffer
# hotel_offer_id, leg_offer_id, ref: api/offers.py (offers_for_place), and through the key
# transfer_id: api/timeline.py, api/cart.py, api/optimizer.py, api/checks.py. Mirror:
# web/src/contracts.ts, contracts.test.ts, tests/fixtures/chat_reply.json (round-trip in
# backend/tests/test_api.py). Tests: backend/tests/test_ports.py (conformance, foreign adapters).
# BookingProvider consumers: api/cart.py (SimulatedBooking, checkout_route, checkout_reply),
# api/store.py (booking attempts), api/main.py (POST /api/checkout), api/model.py (chat
# «подтверждаю»), api/checks.py (eval); tests/test_booking_port.py (conformance, a foreign
# adapter). Voucher.status and Voucher.simulated: web/src/contracts.ts, web/src/api.ts,
# web/src/Board.tsx, Board.test.tsx, App.tsx, App.test.tsx, web/e2e/demo.spec.ts.
# Guest (CheckoutRequest.guest, Voucher.guest; #367): api/cart.py, api/main.py, api/store.py,
# api/checks.py, web/src/api.ts, web/src/App.tsx, web/src/Board.tsx and their tests.


class Place(TypedDict, total=False):
    """A place as the catalogue or the geocoder gives it. name, lat, lon, tz are required;
    the rest are hints for the synthetic adapters (api/offers.py fills defaults)."""

    name: Required[str]
    lat: Required[float]
    lon: Required[float]
    tz: Required[str]  # IANA zone
    slug: str
    country: str
    sea: bool
    level: int  # night in a 3-star room for two, roubles
    free_cancel_share: float
    airport: dict | None  # {"iata": ..., ...}
    airport_km: int | None
    station_km: int | None
    aliases: list[str]


class HotelProvider(Protocol):
    """Hotels of one place for the spec dates and guests.

    Invariants the core reads: each tariff is its own HotelOffer; tariffs of one hotel share
    title and city (dag.py); price_rub is for all guests and all nights; free_until has a
    zone; description is data, not instructions. Empty list: honest "no offers".
    """

    def hotels(
        self, spec: TripSpec, place: Place, now: datetime
    ) -> list[tuple[HotelOffer, list[ExtraOffer]]]: ...


class TransportProvider(Protocol):
    """Legs between origin and place for the spec dates.

    Invariants the core reads: legs "there" first, then "back"; destination of a leg there
    and origin of a leg back equal HotelOffer.city letter by letter (timeline.py); a leg back
    has destination == spec.origin (cart.py); times have a zone.
    """

    def legs(self, spec: TripSpec, origin: Place, place: Place) -> list[TransportOffer]: ...


class TransferProvider(Protocol):
    """Hub <-> hotel rides for the hotels and legs of one search.

    Invariant the core reads: each ride names its pair in hotel_offer_id and leg_offer_id
    (ids of `hotels` and `legs`); the core then sets offer_id = offers.transfer_id(hotel_id,
    leg_id) and keeps the provider id in ref. Without the pair, offer_id must be that key.
    """

    def transfers(
        self,
        spec: TripSpec,
        place: Place,
        hotels: list[HotelOffer],
        legs: list[TransportOffer],
    ) -> list[TransferOffer]: ...


class BookingRefused(Exception):
    """The provider refuses to quote, book or cancel. `reason` is for the user, in Russian.

    The core answers 409 with the reason and keeps the cart: the user can confirm again or
    change the plan. A network error (OSError, TimeoutError) gets the same answer without a
    reason; an adapter wraps its HTTP client errors into one of them. Any other exception is
    a bug: it goes up as a 500 with its traceback.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class BookingProvider(Protocol):
    """Purchase of a cart (ADR-0002).

    Invariants the core reads: `quote` returns the same items (same offer_id) with the prices
    of now; the core compares its total with the total the user confirmed. `book` returns a
    Voucher of exactly the quoted items and total with status "created" or "confirmed"; the
    core cancels and refuses any other voucher. `simulated` says if the booking is real.
    A refusal raises BookingRefused.

    The `session_id` argument of `book` is the idempotency key of one booking attempt, not
    the bare session id: `<session_id>:<hash>` for the first attempt, `<session_id>:<hash>:<n>`
    for the attempt n after a cancel; the hash covers the items, prices, dates and party
    (cart.attempt_key). A real provider stores it as its order key
    (for example `partner_order_id` of ETG) and returns the same booking for the same key.
    While the booking is not "cancelled", the core keeps it and does not call `book` again;
    after `cancel` the session gets a new key and a new booking_id. The core calls `quote`
    and `book` outside the session write lock (POST /api/checkout).
    """

    def quote(self, spec: TripSpec, cart: Cart) -> Cart: ...

    def book(self, spec: TripSpec, cart: Cart, session_id: str) -> Voucher: ...

    def cancel(self, booking_id: str) -> None: ...
