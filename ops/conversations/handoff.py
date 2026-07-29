"""Short-lived, single-use device handoff records.

This service transfers only an owner/conversation binding. Callers must perform
their normal Cloudflare Access or loopback authorization before redeeming it.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

MAX_TTL_SECONDS = 10 * 60


@dataclass(frozen=True)
class Handoff:
    owner_id: str
    conversation_id: str
    expires_at: int


class HandoffStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS conversation_handoffs ("
                "token_hash TEXT PRIMARY KEY, owner_id TEXT NOT NULL, "
                "conversation_id TEXT NOT NULL, expires_at INTEGER NOT NULL, "
                "used_at INTEGER)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def issue(
        self, owner_id: str, conversation_id: str, *, ttl_seconds: int = 300, now: int | None = None
    ) -> str:
        if not owner_id or not conversation_id:
            raise ValueError("owner and conversation are required")
        if not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError("handoff TTL is outside the permitted range")
        token = secrets.token_urlsafe(24)
        issued = int(time.time() if now is None else now)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversation_handoffs "
                "(token_hash, owner_id, conversation_id, expires_at) VALUES (?, ?, ?, ?)",
                (self._hash(token), owner_id, conversation_id, issued + ttl_seconds),
            )
        return token

    def redeem(
        self,
        token: str,
        owner_id: str,
        *,
        authorized: bool,
        now: int | None = None,
    ) -> Handoff | None:
        """Atomically redeem after the caller has independently authorized the owner."""
        if not authorized or not token or not owner_id:
            return None
        used_at = int(time.time() if now is None else now)
        token_hash = self._hash(token)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner_id, conversation_id, expires_at FROM conversation_handoffs "
                "WHERE token_hash=? AND owner_id=? AND used_at IS NULL AND expires_at>=?",
                (token_hash, owner_id, used_at),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            changed = conn.execute(
                "UPDATE conversation_handoffs SET used_at=? WHERE token_hash=? AND used_at IS NULL",
                (used_at, token_hash),
            ).rowcount
            conn.commit()
        return Handoff(row[0], row[1], row[2]) if changed == 1 else None
