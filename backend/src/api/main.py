"""FastAPI: /api/* и собранный фронтенд (web/dist) на /."""

from functools import cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from api.cart import cart_route, checkout_route
from api.contracts import Cart, CartAction, ChatReply, ChatRequest, CheckoutRequest, Voucher
from api.model import chat
from api.store import SessionStore, db_path

app = FastAPI(title="hackathon")


@cache
def get_store() -> SessionStore:
    return SessionStore(db_path())


Store = Annotated[SessionStore, Depends(get_store)]


@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat")
def chat_route(req: ChatRequest, store: Store) -> ChatReply:
    return chat(req, store)


@app.get("/api/session/{session_id}")
def session_route(session_id: str, store: Store) -> ChatReply:
    reply = store.get(session_id)
    if reply is None:
        raise HTTPException(status_code=404, detail="сессия не найдена")
    return reply


@app.post("/api/cart")
def cart_edit_route(req: CartAction, store: Store) -> Cart:
    return cart_route(req, store)


@app.post("/api/checkout")
def checkout_post_route(req: CheckoutRequest, store: Store) -> Voucher:
    return checkout_route(req, store)


DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
