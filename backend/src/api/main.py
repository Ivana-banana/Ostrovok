"""FastAPI: /api/* и собранный фронтенд (web/dist) на /."""

import logging
from functools import cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from api import ops
from api.cart import cart_route, checkout_route
from api.contracts import Cart, CartAction, ChatReply, ChatRequest, CheckoutRequest, Voucher
from api.model import chat
from api.store import SessionStore, db_path

ops.setup_logging()
log = logging.getLogger(__name__)
app = FastAPI(title="hackathon")


@cache
def get_store() -> SessionStore:
    return SessionStore(db_path())


Store = Annotated[SessionStore, Depends(get_store)]


@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, object]:
    """Liveness and the modes (#290): data (синтетика, снапшот, реальный) and LLM (правила or
    the model name). No key, no address."""
    return ops.health()


@app.post("/api/chat")
def chat_route(req: ChatRequest, store: Store) -> ChatReply:
    return chat(req, store)


@app.get("/api/session/{session_id}")
def session_route(session_id: str, store: Store) -> ChatReply:
    with ops.session(session_id):
        reply = store.get(session_id)
    if reply is None:
        raise HTTPException(status_code=404, detail="сессия не найдена")
    return reply


@app.post("/api/cart")
def cart_edit_route(req: CartAction, store: Store) -> Cart:
    with ops.session(req.session_id):
        cart = cart_route(req, store)
        log.info("cart %s items=%d total=%d", req.action, len(cart.items), cart.total_rub)
        return cart


@app.post("/api/checkout")
def checkout_post_route(req: CheckoutRequest, store: Store) -> Voucher:
    with ops.session(req.session_id):
        voucher = checkout_route(req, store)
        log.info("checkout booking=%s total=%d", voucher.booking_id, voucher.total_rub)
        return voucher


DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
