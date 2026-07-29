"""SQLite-checkpointed research lifecycle, safe to resume after interruption."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

from .models import ResearchStage, utc_now

STAGES = tuple(ResearchStage)


class ResearchCheckpointStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS research_jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS research_checkpoints (job_id TEXT NOT NULL, stage TEXT NOT NULL, payload TEXT NOT NULL, completed_at TEXT NOT NULL, PRIMARY KEY(job_id, stage))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS research_events (job_id TEXT NOT NULL, sequence INTEGER PRIMARY KEY AUTOINCREMENT, stage TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)"
            )

    def _connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def create(self, job_id: str) -> None:
        now = utc_now()
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO research_jobs VALUES (?, 'pending', 0, ?, ?)",
                (job_id, now, now),
            )

    def save(self, job_id: str, stage: ResearchStage, payload: dict) -> None:
        now = utc_now()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO research_checkpoints VALUES (?, ?, ?, ?)",
                (job_id, stage.value, encoded, now),
            )
            db.execute(
                "INSERT INTO research_events(job_id,stage,status,created_at) VALUES (?,?,'completed',?)",
                (job_id, stage.value, now),
            )
            db.execute(
                "UPDATE research_jobs SET status='running', updated_at=? WHERE id=?", (now, job_id)
            )

    def load(self, job_id: str, stage: ResearchStage) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM research_checkpoints WHERE job_id=? AND stage=?",
                (job_id, stage.value),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def cancel(self, job_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE research_jobs SET cancelled=1,status='cancelled',updated_at=? WHERE id=?",
                (utc_now(), job_id),
            )

    def cancelled(self, job_id: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT cancelled FROM research_jobs WHERE id=?", (job_id,)).fetchone()
        return bool(row and row[0])

    def timeline(self, job_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT sequence,stage,status,created_at FROM research_events WHERE job_id=? ORDER BY sequence",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]


class ResearchLifecycle:
    def __init__(self, store: ResearchCheckpointStore):
        self.store = store

    def run(
        self, job_id: str, initial: dict, handlers: dict[ResearchStage, Callable[[dict], dict]]
    ) -> dict:
        self.store.create(job_id)
        state = initial
        for stage in STAGES:
            completed = self.store.load(job_id, stage)
            if completed is not None:
                state = completed
                continue
            if self.store.cancelled(job_id):
                return state
            state = handlers.get(stage, lambda value: value)(state)
            self.store.save(job_id, stage, state)
        return state
