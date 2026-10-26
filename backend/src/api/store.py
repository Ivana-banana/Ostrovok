"""Session store: session_id -> the last ChatReply as JSON (stdlib sqlite3)."""

import os
import sqlite3
from collections.abc import Callable
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
        self._write(reply)

    def update(self, session_id: str, change: Callable[[ChatReply | None], ChatReply]) -> ChatReply:
        """Read, change and write the session in one transaction.

        BEGIN IMMEDIATE takes the write lock before the read, so two parallel requests of one
        session run one after the other and neither loses its update. If `change` raises, the
        transaction rolls back and the stored reply stays as it was.
        """
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute(
                    "SELECT reply FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                out = change(ChatReply.model_validate_json(row[0]) if row else None)
                self._write(out, con)
                con.execute("COMMIT")
            except BaseException:
                try:
                    con.execute("ROLLBACK")
                except sqlite3.Error:
                    pass  # the original error matters more than a failed rollback
                raise
            return out
        finally:
            con.close()

    def _write(self, reply: ChatReply, con: sqlite3.Connection | None = None) -> None:
        sql = (
            "INSERT INTO sessions (id, reply) VALUES (?, ?) ON CONFLICT(id) DO UPDATE "
            "SET reply = excluded.reply, updated_at = CURRENT_TIMESTAMP"
        )
        args = (reply.session_id, reply.model_dump_json())
        if con:
            con.execute(sql, args)
        else:
            self._run(sql, args)
