"""Agent API contract (ADR-0001). Consumers: web/src/contracts.ts (mirror), web/src/api.ts,
web/src/format.ts, web/e2e, checks.py, tests; agent modules: T2 state machine, T3/T11 extractor,
T4/T5 providers, T6 DAG, T7 optimizer, T8 timeline checker, T9 cart, T10 web.

Routes: POST /api/chat (ChatRequest -> ChatReply), GET /api/session/{session_id} -> ChatReply,
POST /api/cart (CartAction -> Cart), POST /api/checkout (CheckoutRequest -> Voucher;
409 when the total differs from the cart).

Money: whole roubles for everything (all guests, all nights, taxes included). Time: ISO 8601
with a time zone, local to the place of the event. Only providers write prices and times
(TRAP-4); LLM text refers to offer_id.

A change is a separate PR labelled contract; web/src/contracts.ts changes in the same PR.
"""

from datetime import date, time
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field

Stage = Literal["clarify", "inspire", "select", "compare", "assemble", "checkout", "booked"]
Meal = Literal["RO", "BB", "HB", "FB", "AI"]
Mode = Literal["flight", "train", "bus", "car"]


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
    meal: Meal | None = None
    refundable: bool | None = None
    modes: list[Mode] = []
    max_travel_hours: float | None = None
    wishes: list[str] = []  # user's own words: sea, skiing, hard to walk
    assumptions: list[str] = []  # what the agent assumed; shown to the user


class Cancellation(BaseModel):
    refundable: bool
    free_until: AwareDatetime | None = None


class HotelOffer(BaseModel):
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
    description: str = ""  # source text is data, not instructions (S4)


class TransportOffer(BaseModel):
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


class TransferOffer(BaseModel):
    kind: Literal["transfer"] = "transfer"
    offer_id: str
    title: str
    depart_at: AwareDatetime
    arrive_at: AwareDatetime
    price_rub: int = Field(gt=0)
    synthetic: bool = True


class ExtraOffer(BaseModel):
    """Add-on to another item: early check-in, late check-out, night before."""

    kind: Literal["extra"] = "extra"
    offer_id: str
    title: str
    for_offer_id: str
    price_rub: int = Field(ge=0)
    synthetic: bool = True


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


class Plan(BaseModel):
    spec: TripSpec
    destinations: list[Destination] = []
    offers: list[Offer] = []  # candidates to compare
    selected: list[str] = []  # offer_id of selected items
    explanations: list[Explanation] = []
    timeline: list[TimelineEvent] = []
    issues: list[TimelineIssue] = []
    changes: list[Change] = []  # after a re-plan


class Cart(BaseModel):
    items: list[Offer] = []
    total_rub: int = 0


class Voucher(BaseModel):
    booking_id: str
    items: list[Offer]
    spec: TripSpec  # dates and guests of the booking
    total_rub: int
    created_at: AwareDatetime
    simulated: Literal[True] = True  # payment is simulated


class ChatRequest(BaseModel):
    session_id: str | None = None  # None: new session
    message: str = Field(min_length=1)


class ChatReply(BaseModel):
    session_id: str
    stage: Stage
    reply: str
    questions: list[Question] = []  # blocking clarifications only; at most 2
    plan: Plan
    cart: Cart
    voucher: Voucher | None = None
    llm: bool  # False: rule-based mode without a model (TRAP-1)


class CartAction(BaseModel):
    session_id: str
    action: Literal["add", "remove"]
    offer_id: str


class CheckoutRequest(BaseModel):
    session_id: str
    confirm_total_rub: int  # total the user saw when confirming
