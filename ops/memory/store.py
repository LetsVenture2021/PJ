"""SQLite persistence for memory lifecycle metadata and content."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from ops.shared.semantic_memory import delete_memory_vector

from .models import utc_now


class MemoryStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._init()

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
              id TEXT PRIMARY KEY, content TEXT, category TEXT NOT NULL,
              source_type TEXT NOT NULL, source_ref TEXT NOT NULL,
              project_scope TEXT NOT NULL, confidence TEXT NOT NULL,
              created_at TEXT NOT NULL, expires_at TEXT, status TEXT NOT NULL,
              supersedes_id TEXT, superseded_by_id TEXT, content_hash TEXT,
              pinned INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS memory_retrieval ON memories(project_scope,status,expires_at);
            CREATE TABLE IF NOT EXISTS memory_tombstones (
              id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL, prior_content_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memory_settings (
              key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    def insert(
        self, proposal: dict[str, Any], *, status="proposed", expires_at=None, supersedes_id=None
    ):
        memory_id = "MEM-" + uuid.uuid4().hex
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO memories
              (id,content,category,source_type,source_ref,project_scope,confidence,created_at,
               expires_at,status,supersedes_id,content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    memory_id,
                    proposal["content"],
                    proposal["category"],
                    proposal["source_type"],
                    proposal["source_ref"],
                    proposal["project_scope"],
                    proposal["confidence"],
                    now,
                    expires_at,
                    status,
                    supersedes_id,
                    proposal["content_hash"],
                ),
            )
            if supersedes_id:
                db.execute(
                    "UPDATE memories SET status='superseded', superseded_by_id=? WHERE id=? AND status='accepted'",
                    (memory_id, supersedes_id),
                )
        return self.get(memory_id)

    def get(self, memory_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return dict(row) if row else None

    def update(self, memory_id, **fields):
        allowed = {"content", "content_hash", "status", "expires_at", "pinned"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("invalid update")
        with self.connect() as db:
            db.execute(
                f"UPDATE memories SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                (*fields.values(), memory_id),
            )
        return self.get(memory_id)

    def list(self, *, status=None, project_scope=None):
        clauses, args = [], []
        if status:
            clauses.append("status=?")
            args.append(status)
        if project_scope:
            clauses.append("project_scope=?")
            args.append(project_scope)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM memories" + where + " ORDER BY created_at DESC", args
                )
            ]

    def delete(self, memory_id):
        row = self.get(memory_id)
        if not row:
            return False
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO memory_tombstones VALUES (?,?,?)",
                (memory_id, utc_now(), row["content_hash"] or ""),
            )
            db.execute(
                "UPDATE memories SET content=NULL, content_hash=NULL, status='deleted', pinned=0 WHERE id=?",
                (memory_id,),
            )
        delete_memory_vector(memory_id, db_path=self.path)
        return True

    def setting(self, key, default):
        with self.connect() as db:
            row = db.execute("SELECT value FROM memory_settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO memory_settings VALUES (?,?)", (key, json.dumps(value))
            )
