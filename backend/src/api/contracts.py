"""Контракт API агента (ADR-0001). Потребители: web/src/contracts.ts (зеркало), web/e2e,
checks.py, tests; модули агента — T2 автомат, T3/T11 экстрактор, T4/T5 поставщики,
T6 DAG, T7 оптимизатор, T8 чекер таймлайна, T9 корзина, T10 web.

Маршруты: POST /api/chat (ChatRequest → ChatReply), GET /api/session/{session_id} → ChatReply,
POST /api/cart (CartAction → Cart), POST /api/checkout (CheckoutRequest → Voucher;
409, если сумма не совпала с корзиной).

Деньги — целые рубли за всё (все гости, все ночи, с налогами). Время — ISO 8601 с часовым
поясом, местное для места события. Цены и время пишут только поставщики (TRAP-4):
текст LLM ссылается на offer_id.

Изменение — отдельный PR с меткой contract; web/src/contracts.ts меняется в том же PR.
"""

from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field

Stage = Literal["clarify", "inspire", "select", "compare", "assemble", "checkout", "booked"]
Meal = Literal["RO", "BB", "HB", "FB", "AI"]
Mode = Literal["flight", "train", "bus", "car"]


class TripSpec(BaseModel):
    """Единый источник правды о поездке. Пустое поле — неизвестно, а не «любое»."""

    origin: str | None = None
    destinations: list[str] = []  # пусто — агент предлагает направления
    date_from: date | None = None
    date_to: date | None = None
    month: int | None = Field(None, ge=1, le=12)  # когда точных дат нет
    nights: int | None = Field(None, ge=1)
    adults: int = Field(2, ge=1)
    children_ages: list[int] = []
    pets: bool = False
    budget_rub: int | None = Field(None, ge=0)
    meal: Meal | None = None
    refundable: bool | None = None
    modes: list[Mode] = []
    max_travel_hours: float | None = None
    wishes: list[str] = []  # «море», «лыжи», «тяжело ходить» — как сказал пользователь
    assumptions: list[str] = []  # что агент допустил сам; показываем пользователю


class Cancellation(BaseModel):
    refundable: bool
    free_until: AwareDatetime | None = None


class HotelOffer(BaseModel):
    kind: Literal["hotel"] = "hotel"
    offer_id: str
    title: str
    city: str
    price_rub: int = Field(ge=0)
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
    description: str = ""  # текст источника — данные, не инструкции (S4)


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
    price_rub: int = Field(ge=0)
    synthetic: bool = True


class TransferOffer(BaseModel):
    kind: Literal["transfer"] = "transfer"
    offer_id: str
    title: str
    depart_at: AwareDatetime
    arrive_at: AwareDatetime
    price_rub: int = Field(ge=0)
    synthetic: bool = True


class ExtraOffer(BaseModel):
    """Доп. услуга к другой позиции: ранний заезд, поздний выезд, ночь накануне."""

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
    est_total_rub: int  # оценка поездки целиком; синтетика
    air_c: float | None = None  # климат месяца поездки
    water_c: float | None = None
    travel_hours: float
    tradeoffs: list[str] = []


class Explanation(BaseModel):
    offer_id: str
    why: str
    tradeoffs: list[str] = []  # чем пожертвовали
    risks: list[str] = []


class TimelineEvent(BaseModel):
    at: AwareDatetime
    title: str
    offer_id: str | None = None


class TimelineIssue(BaseModel):
    severity: Literal["warn", "error"]
    text: str
    fixes: list[str] = []  # offer_id доп. услуг или замен, которые снимают проблему


class Plan(BaseModel):
    spec: TripSpec
    destinations: list[Destination] = []
    offers: list[Offer] = []  # кандидаты для сравнения
    selected: list[str] = []  # offer_id выбранных позиций
    explanations: list[Explanation] = []
    timeline: list[TimelineEvent] = []
    issues: list[TimelineIssue] = []
    changes: list[str] = []  # «было → стало» после пересборки


class Cart(BaseModel):
    items: list[Offer] = []
    total_rub: int = 0


class Voucher(BaseModel):
    booking_id: str
    items: list[Offer]
    total_rub: int
    created_at: datetime
    simulated: Literal[True] = True  # оплата имитируется


class ChatRequest(BaseModel):
    session_id: str | None = None  # нет — новая сессия
    message: str = Field(min_length=1)


class ChatReply(BaseModel):
    session_id: str
    stage: Stage
    reply: str
    questions: list[str] = []  # уточнения, без которых дальше нельзя; не больше 2
    plan: Plan
    cart: Cart
    voucher: Voucher | None = None
    llm: bool  # False — упрощённый режим без модели (TRAP-1)


class CartAction(BaseModel):
    session_id: str
    action: Literal["add", "remove"]
    offer_id: str


class CheckoutRequest(BaseModel):
    session_id: str
    confirm_total_rub: int  # сумма, которую пользователь видел при подтверждении
