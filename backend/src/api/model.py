"""Агент. Пока — заглушка демо-пути: её заменяет автомат этапов (T2, #6)."""

from uuid import uuid4

from api.contracts import Cart, ChatReply, ChatRequest, Plan, TripSpec


def chat(req: ChatRequest) -> ChatReply:
    # TODO(T2): заглушка — сквозной путь демо работает до появления автомата этапов.
    return ChatReply(
        session_id=req.session_id or uuid4().hex,
        stage="clarify",
        reply="заглушка: агента ещё нет",
        plan=Plan(spec=TripSpec()),
        cart=Cart(),
        llm=False,
    )
