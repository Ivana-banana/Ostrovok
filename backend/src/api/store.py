"""Session store: session_id -> the last ChatReply as JSON (stdlib sqlite3)."""

import os
import sqlite3
from pathlib import Path

from api.contracts import ChatReply

DEFAULT_DB = Path(__file__).resolve().parents[3] / "data" / "sessions.db"


def db_path() -> Path:
    return Path(os.environ.get("SESSIONS_DB") or DEFAULT_DB)


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "id TEXT PRIMARY KEY, reply TEXT NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )

    def _run(self, sql: str, args: tuple = ()) -> tuple | None:
        con = sqlite3.connect(self.path)
        try:
            with con:
                return con.execute(sql, args).fetchone()
        finally:
            con.close()

    def get(self, session_id: str) -> ChatReply | None:
        row = self._run("SELECT reply FROM sessions WHERE id = ?", (session_id,))
        return ChatReply.model_validate_json(row[0]) if row else None

    def put(self, reply: ChatReply) -> None:
        self._run(
            "INSERT INTO sessions (id, reply) VALUES (?, ?) ON CONFLICT(id) DO UPDATE "
            "SET reply = excluded.reply, updated_at = CURRENT_TIMESTAMP",
            (reply.session_id, reply.model_dump_json()),
        )
