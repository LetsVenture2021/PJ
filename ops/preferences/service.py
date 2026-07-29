"""Preference definitions and owner controls.

Explicit settings and unapproved memory proposals use separate tables so an
inference can never accidentally become an effective preference.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class PreferenceScope(StrEnum):
    GLOBAL = "global"
    PROJECT = "project"
    CONVERSATION = "conversation"


class PreferenceSource(StrEnum):
    EXPLICIT = "explicit"
    IMPORTED = "imported"
    INFERRED = "inferred"


class ConsentStatus(StrEnum):
    APPROVED = "approved"
    PENDING = "pending"
    DISABLED = "disabled"


@dataclass(frozen=True)
class Preference:
    category: str
    value: Any
    scope: PreferenceScope
    source: PreferenceSource
    consent: ConsentStatus
    version: int
    expires_at: str | None = None
    project_id: str | None = None


@dataclass(frozen=True)
class Proposal:
    proposal_id: int
    category: str
    value: Any
    scope: PreferenceScope
    sensitive: bool


class PreferenceStore:
    """SQLite-backed preferences with deterministic scope precedence."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS preferences (
                    category TEXT NOT NULL, scope TEXT NOT NULL,
                    project_id TEXT NOT NULL DEFAULT '', value_json TEXT NOT NULL,
                    source TEXT NOT NULL, consent TEXT NOT NULL, version INTEGER NOT NULL,
                    expires_at TEXT, PRIMARY KEY(category, scope, project_id));
                CREATE TABLE IF NOT EXISTS preference_proposals (
                    id INTEGER PRIMARY KEY, category TEXT NOT NULL, value_json TEXT NOT NULL,
                    scope TEXT NOT NULL, project_id TEXT NOT NULL DEFAULT '', sensitive INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending');
            """)

    def set(self, preference: Preference) -> None:
        if preference.source == PreferenceSource.INFERRED:
            raise ValueError("inferred values must be submitted as proposals")
        if preference.scope == PreferenceScope.PROJECT and not preference.project_id:
            raise ValueError("project preferences require project_id")
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO preferences VALUES(?,?,?,?,?,?,?,?)",
                (
                    preference.category,
                    preference.scope,
                    preference.project_id or "",
                    json.dumps(preference.value),
                    preference.source,
                    preference.consent,
                    preference.version,
                    preference.expires_at,
                ),
            )

    def propose(
        self,
        category: str,
        value: Any,
        *,
        scope: PreferenceScope,
        project_id: str | None = None,
        sensitive: bool = False,
    ) -> int:
        if sensitive:
            raise ValueError("sensitive preferences cannot be inferred")
        if scope == PreferenceScope.GLOBAL:
            raise ValueError("inferred project or conversation style cannot become global")
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO preference_proposals(category,value_json,scope,project_id,sensitive) VALUES(?,?,?,?,0)",
                (category, json.dumps(value), scope, project_id or ""),
            )
            return int(cursor.lastrowid)

    def approve(self, proposal_id: int, *, version: int = 1) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM preference_proposals WHERE id=? AND status='pending'", (proposal_id,)
            ).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            db.execute(
                "INSERT OR REPLACE INTO preferences VALUES(?,?,?,?,?,?,?,NULL)",
                (
                    row["category"],
                    row["scope"],
                    row["project_id"],
                    row["value_json"],
                    PreferenceSource.INFERRED,
                    ConsentStatus.APPROVED,
                    version,
                ),
            )
            db.execute(
                "UPDATE preference_proposals SET status='approved' WHERE id=?", (proposal_id,)
            )

    def effective(
        self,
        category: str,
        *,
        project_id: str | None = None,
        conversation: dict[str, Any] | None = None,
        use_defaults: bool = False,
        default: Any = None,
    ) -> Any:
        if use_defaults:
            return default
        if conversation and category in conversation:
            return conversation[category]
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM preferences WHERE category=? AND consent='approved' "
                "AND (expires_at IS NULL OR expires_at>?) AND (scope='global' OR (scope='project' AND project_id=?))",
                (category, now, project_id or ""),
            ).fetchall()
        ranked = sorted(
            rows, key=lambda row: (row["scope"] == "project", row["version"]), reverse=True
        )
        return json.loads(ranked[0]["value_json"]) if ranked else default

    def disable(
        self, category: str, *, scope: PreferenceScope, project_id: str | None = None
    ) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE preferences SET consent='disabled' WHERE category=? AND scope=? AND project_id=?",
                (category, scope, project_id or ""),
            )

    def delete(
        self, category: str, *, scope: PreferenceScope, project_id: str | None = None
    ) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM preferences WHERE category=? AND scope=? AND project_id=?",
                (category, scope, project_id or ""),
            )

    def export(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM preferences ORDER BY category,scope,project_id"
            ).fetchall()
        return [{**dict(row), "value": json.loads(row["value_json"])} for row in rows]
