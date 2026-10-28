"""Partner API (#435): what a business needs on its first day with /api/v1.

- Keys: PARTNER_API_KEYS (comma list) in .env. Empty: /api/v1 is open, as the jury runs it
  without setup. Set: every /api/v1 route but health and mode needs the header X-API-Key with
  one of the keys, else 401. The web client uses /api/* and needs no key.
- POST /api/v1/plan: one call, request text -> a plan with the cart filled. It is the chat of
  /api/v1/chat driven by the answers a user gives (pick the first destination, «беру», «да»);
  no second engine. It stops at a blocking question: the partner answers it with the same
  session_id.
- Idempotency-Key on checkout: a repeat of the key returns the first voucher; the same key with
  another body is 422.
- CORS: ALLOWED_ORIGINS (comma list, or *) for browser calls from a partner domain.
"""

import os
import secrets
from typing import Annotated

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from api.cart import checkout_route
from api.contracts import ChatReply, ChatRequest, CheckoutRequest, Voucher
from api.model import chat
from api.store import SessionStore

API_KEY = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="Ключ партнёра из PARTNER_API_KEYS. Пусто в .env — ключ не нужен.",
)
MAX_PLAN_STEPS = 3  # inspire -> compare -> assemble -> checkout


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def allowed_origins() -> list[str]:
    return _list("ALLOWED_ORIGINS")


def require_key(key: Annotated[str | None, Security(API_KEY)] = None) -> None:
    """401 without a valid X-API-Key when PARTNER_API_KEYS is set; open access when it is empty."""
    keys = _list("PARTNER_API_KEYS")
    if not keys:
        return
    if key and any(secrets.compare_digest(key.encode(), k.encode()) for k in keys):
        return
    raise HTTPException(
        status_code=401,
        detail="Нужен заголовок X-API-Key с ключом партнёра (PARTNER_API_KEYS).",
        headers={"WWW-Authenticate": "ApiKey"},
    )


def _next_message(reply: ChatReply) -> str | None:
    """The answer a user gives at this stage to move on; None: the partner's turn."""
    if reply.questions:
        return None  # a blocking clarification: only the traveller knows the answer
    if reply.stage == "inspire" and reply.plan.destinations:
        return reply.plan.destinations[0].name  # the best-ranked destination
    if reply.stage in ("select", "compare"):
        return "беру"  # the best bundle of the optimizer
    if reply.stage == "assemble":
        return "да"  # agree with the plan: the cart opens
    return None


def plan_reply(req: ChatRequest, store: SessionStore) -> ChatReply:
    """POST /api/v1/plan: the chat until the cart is filled or a question blocks it."""
    reply = chat(req, store)
    for _ in range(MAX_PLAN_STEPS):
        message = _next_message(reply)
        if message is None:
            break
        nxt = chat(ChatRequest(session_id=reply.session_id, message=message), store)
        moved = nxt.stage != reply.stage
        reply = nxt
        if not moved:  # the core did not accept the answer; its reply says why
            break
    return reply


def checkout_once(req: CheckoutRequest, store: SessionStore, key: str | None) -> Voucher:
    """POST /api/checkout with an optional Idempotency-Key: a repeat returns the first voucher."""
    if not key:
        return checkout_route(req, store)
    body = req.model_dump_json()
    first, voucher = store.claim_key(key, body)
    if first != body:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key уже использован с другим запросом. Нужен новый ключ.",
        )
    if voucher is not None:
        return voucher
    # A 409 leaves the key without a voucher: the same request with the same key may try again.
    return store.keep_key(key, checkout_route(req, store))
