"""Session store: session_id -> the last ChatReply as JSON (stdlib sqlite3).

Table `booking_attempts` keeps the booking attempts of a session (#376): POST /api/checkout
and the chat confirmation book through it (#382, cart.checkout_reply). The attempt
is saved before the provider call and its voucher right after it, outside the session lock.
A repeat after a failed session write finds the voucher here and does not book again.

Table `idempotency_keys` (#435): the Idempotency-Key header of POST /api/v1/checkout -> the
request it came with and its voucher. A repeat of the key returns that voucher and books nothing.
"""

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

from api.contracts import ChatReply, Voucher

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
        self._run(
            "CREATE TABLE IF NOT EXISTS booking_attempts ("
            "session_id TEXT NOT NULL, attempt INTEGER NOT NULL, voucher TEXT, "
            "PRIMARY KEY (session_id, attempt))"
        )
        self._run(
            "CREATE TABLE IF NOT EXISTS idempotency_keys ("
            "key TEXT PRIMARY KEY, request TEXT NOT NULL, voucher TEXT)"
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

    def last_attempt(self, session_id: str) -> tuple[int, Voucher | None]:
        """The last booking attempt of the session and its voucher; (0, None) without one."""
        row = self._run(
            "SELECT attempt, voucher FROM booking_attempts WHERE session_id = ? "
            "ORDER BY attempt DESC LIMIT 1",
            (session_id,),
        )
        if not row:
            return 0, None
        return row[0], Voucher.model_validate_json(row[1]) if row[1] else None

    def start_attempt(self, session_id: str, attempt: int) -> None:
        self._run(
            "INSERT OR IGNORE INTO booking_attempts (session_id, attempt) VALUES (?, ?)",
            (session_id, attempt),
        )

    def save_voucher(self, session_id: str, attempt: int, voucher: Voucher) -> Voucher:
        """Keep the first voucher of the attempt and return the kept one: of two parallel
        requests with one attempt key, both store the same booking in the session."""
        self._run(
            "UPDATE booking_attempts SET voucher = ? "
            "WHERE session_id = ? AND attempt = ? AND voucher IS NULL",
            (voucher.model_dump_json(), session_id, attempt),
        )
        row = self._run(
            "SELECT voucher FROM booking_attempts WHERE session_id = ? AND attempt = ?",
            (session_id, attempt),
        )
        return Voucher.model_validate_json(row[0]) if row and row[0] else voucher

    def claim_key(self, key: str, request: str) -> tuple[str, Voucher | None]:
        """The request the key was first used with and its voucher; a new key is saved with
        `request` and returns (request, None)."""
        self._run(
            "INSERT OR IGNORE INTO idempotency_keys (key, request) VALUES (?, ?)", (key, request)
        )
        row = self._run("SELECT request, voucher FROM idempotency_keys WHERE key = ?", (key,))
        assert row is not None  # inserted above or before
        return row[0], Voucher.model_validate_json(row[1]) if row[1] else None

    def keep_key(self, key: str, voucher: Voucher) -> Voucher:
        """Store the voucher of the key once; return the stored one."""
        self._run(
            "UPDATE idempotency_keys SET voucher = ? WHERE key = ? AND voucher IS NULL",
            (voucher.model_dump_json(), key),
        )
        row = self._run("SELECT voucher FROM idempotency_keys WHERE key = ?", (key,))
        return Voucher.model_validate_json(row[0]) if row and row[0] else voucher

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
