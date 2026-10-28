"""FastAPI: /api/* for the web client, /api/v1/* for partners (#289, #435), /embed.js and the
built frontend (web/dist) on /. Partner keys, CORS and idempotency — api/partner.py."""

import logging
import os
from functools import cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api import offers, ops
from api.cart import cart_route, checkout_route
from api.contracts import Cart, CartAction, ChatReply, ChatRequest, CheckoutRequest, Voucher
from api.guards import llm_available
from api.model import chat
from api.partner import checkout_once, cors_options, plan_reply, require_key
from api.store import SessionStore, db_path
from api.travelpayouts import TravelpayoutsTransport

V1 = "Партнёрский API /api/v1"
WEB = "Веб-клиент /api"
DESCRIPTION = """\
Агент путешествий «О!дин запрос»: одно сообщение путешественника → план, корзина и
имитированная бронь. Цены, время и стыковки — из провайдеров, не из LLM; все цены — синтетика.

**Партнёрам — `/api/v1`.** `POST /api/v1/plan` — одним вызовом текст запроса → план с
корзиной. `POST /api/v1/chat` — диалог по шагам. `POST /api/v1/checkout` — бронь,
заголовок `Idempotency-Key` защищает от второй брони.

**Ключ.** Если в `.env` задан `PARTNER_API_KEYS`, маршруты `/api/v1` (кроме health и mode)
требуют заголовок `X-API-Key`; без ключа или с чужим ключом — 401. Пусто — доступ открыт.

**Вебхук.** `BOOKING_WEBHOOK_URL` получает `POST` с JSON ваучера после каждой новой брони.

**Виджет.** `<script src="http://localhost:8080/embed.js" data-partner="…"></script>`.
"""

# Real flights (#261) only with a token in .env; without it synthetic transport (TRAP-1).
if token := os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip():
    offers.TRANSPORT = TravelpayoutsTransport(token)

ops.setup_logging()
log = logging.getLogger(__name__)
app = FastAPI(title="О!дин запрос — API агента путешествий", version="1.0", description=DESCRIPTION)
if cors := cors_options():
    app.add_middleware(CORSMiddleware, **cors)

KEY = [Depends(require_key)]


@cache
def get_store() -> SessionStore:
    return SessionStore(db_path())


Store = Annotated[SessionStore, Depends(get_store)]
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        max_length=255,
        description="Любая уникальная строка на одну покупку. Повтор с тем же ключом вернёт "
        "тот же ваучер и не создаст вторую бронь; тот же ключ с другим телом — 422.",
    ),
]


@app.get("/health", tags=["Служебное"], summary="Проверка живости и режимы")
@app.get("/api/health", tags=[WEB], summary="Проверка живости и режимы")
@app.get("/api/v1/health", tags=[V1], summary="Проверка живости и режимы (без ключа)")
def health() -> dict[str, object]:
    """Liveness and the modes (#290): data (синтетика, снапшот, реальный) and LLM (правила or
    the model name). No key, no address."""
    return ops.health()


@app.get("/api/mode", tags=[WEB], summary="Режим: с LLM или по правилам")
@app.get("/api/v1/mode", tags=[V1], summary="Режим: с LLM или по правилам (без ключа)")
def mode() -> dict[str, bool]:
    # llm: false — агент отвечает по правилам (TRAP-1); фронт показывает «Без ИИ» до первого ответа.
    # /health тоже показывает режимы (#290), но с ключом "status": "ok" из правил хакатона.
    return {"llm": llm_available()}


@app.post(
    "/api/v1/plan",
    tags=[V1],
    dependencies=KEY,
    summary="План одним вызовом: текст → план и корзина",
    description="Тот же агент, что и `/api/v1/chat`: он сам выбирает первое направление, "
    "лучший вариант («беру») и соглашается с планом («да»). Итог — `stage=checkout`, корзина "
    "`cart` заполнена, объяснения — `plan.explanations`. Если агенту нужен ответ "
    "путешественника, `questions` не пуст: передайте ответ в `message` с тем же `session_id`. "
    "Бронь не оформляется: для неё `POST /api/v1/checkout`.",
)
def plan_route(req: ChatRequest, store: Store) -> ChatReply:
    return plan_reply(req, store)


@app.post("/api/chat", tags=[WEB], summary="Сообщение в диалог")
@app.post(
    "/api/v1/chat",
    tags=[V1],
    dependencies=KEY,
    summary="Сообщение в диалог",
    description="Один ход агента. Без `session_id` — новая сессия.",
)
def chat_route(req: ChatRequest, store: Store) -> ChatReply:
    return chat(req, store)


@app.get("/api/session/{session_id}", tags=[WEB], summary="Последний ответ сессии")
@app.get(
    "/api/v1/session/{session_id}", tags=[V1], dependencies=KEY, summary="Последний ответ сессии"
)
def session_route(session_id: str, store: Store) -> ChatReply:
    with ops.session(session_id):
        reply = store.get(session_id)
    if reply is None:
        raise HTTPException(status_code=404, detail="сессия не найдена")
    return reply


@app.post("/api/cart", tags=[WEB], summary="Добавить или убрать позицию корзины")
@app.post(
    "/api/v1/cart", tags=[V1], dependencies=KEY, summary="Добавить или убрать позицию корзины"
)
def cart_edit_route(req: CartAction, store: Store) -> Cart:
    with ops.session(req.session_id):
        cart = cart_route(req, store)
        log.info("cart %s items=%d total=%d", req.action, len(cart.items), cart.total_rub)
        return cart


@app.post("/api/checkout", tags=[WEB], summary="Имитированная бронь корзины")
@app.post(
    "/api/v1/checkout",
    tags=[V1],
    dependencies=KEY,
    summary="Имитированная бронь корзины",
    description="`confirm_total_rub` — итог, который видел путешественник; другой итог — 409. "
    "После новой брони ваучер уходит на `BOOKING_WEBHOOK_URL`; сбой вебхука бронь не отменяет.",
)
def checkout_post_route(
    req: CheckoutRequest, store: Store, idempotency_key: IdempotencyKey = None
) -> Voucher:
    with ops.session(req.session_id):
        voucher = checkout_once(req, store, idempotency_key, lambda: checkout_route(req, store))
        log.info("checkout booking=%s total=%d", voucher.booking_id, voucher.total_rub)
        return voucher


EMBED = Path(__file__).with_name("embed.js")


@app.get("/embed.js", tags=["Служебное"], summary="Виджет чата для страницы партнёра")
def embed_js() -> FileResponse:
    return FileResponse(EMBED, media_type="text/javascript; charset=utf-8")


DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
