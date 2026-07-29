"""SQLite persistence and transaction boundaries for jobs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .models import JobState


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, handler TEXT NOT NULL, state TEXT NOT NULL, input_json TEXT NOT NULL, public_result_json TEXT, budget_units INTEGER NOT NULL, spent_units INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL, version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS job_steps (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), step_key TEXT NOT NULL, position INTEGER NOT NULL, state TEXT NOT NULL, input_json TEXT NOT NULL, approval_sensitive INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3, UNIQUE(job_id, step_key));
CREATE TABLE IF NOT EXISTS job_attempts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), step_id TEXT REFERENCES job_steps(id), number INTEGER NOT NULL, state TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL, error_code TEXT);
CREATE TABLE IF NOT EXISTS job_leases (job_id TEXT PRIMARY KEY REFERENCES jobs(id), owner TEXT NOT NULL, token TEXT NOT NULL, acquired_at REAL NOT NULL, expires_at REAL NOT NULL, heartbeat_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS job_checkpoints (attempt_id TEXT PRIMARY KEY REFERENCES job_attempts(id), payload BLOB NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS job_dependencies (step_id TEXT NOT NULL REFERENCES job_steps(id), depends_on_id TEXT NOT NULL REFERENCES job_steps(id), PRIMARY KEY(step_id, depends_on_id));
CREATE TABLE IF NOT EXISTS job_schedules (id TEXT PRIMARY KEY, handler TEXT NOT NULL, kind TEXT NOT NULL, config_json TEXT NOT NULL, timezone TEXT NOT NULL, missed_run_policy TEXT NOT NULL, quiet_hours_json TEXT, budget_units INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, next_run_at REAL);
CREATE TABLE IF NOT EXISTS job_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id), type TEXT NOT NULL, public_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS job_idempotency_claims (operation_key TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), request_hash TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, updated_at REAL NOT NULL);
"""


class JobRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_job(
        self,
        handler: str,
        payload: Mapping[str, Any],
        budget_units: int,
        steps: list[Any],
        *,
        job_id: str | None = None,
    ) -> str:
        job_id = job_id or f"job_{uuid.uuid4().hex}"
        now = time.time()
        with self.transaction(immediate=True) as db:
            db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,0)",
                (
                    job_id,
                    handler,
                    JobState.QUEUED,
                    json.dumps(payload, separators=(",", ":")),
                    None,
                    budget_units,
                    0,
                    now,
                    now,
                ),
            )
            ids = {}
            for position, step in enumerate(steps):
                step_id = f"step_{uuid.uuid4().hex}"
                ids[step.key] = step_id
                db.execute(
                    "INSERT INTO job_steps VALUES(?,?,?,?,?,?,?,?)",
                    (
                        step_id,
                        job_id,
                        step.key,
                        position,
                        JobState.QUEUED,
                        json.dumps(step.input, separators=(",", ":")),
                        int(step.approval_sensitive),
                        step.max_attempts,
                    ),
                )
            for step in steps:
                for dependency in step.dependencies:
                    if dependency not in ids:
                        raise ValueError(f"unknown dependency: {dependency}")
                    db.execute(
                        "INSERT INTO job_dependencies VALUES(?,?)", (ids[step.key], ids[dependency])
                    )
            self._event(db, job_id, "job.created", {"state": JobState.QUEUED})
        return job_id

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["input"] = json.loads(result.pop("input_json"))
            public_result = result.pop("public_result_json")
            result["public_result"] = json.loads(public_result) if public_result else None
            return result

    def put_schedule(
        self,
        schedule_id: str,
        handler: str,
        kind: str,
        config: Mapping[str, Any],
        timezone: str,
        missed_run_policy: str,
        budget_units: int,
        *,
        quiet_hours: Mapping[str, Any] | None = None,
        next_run_at: float | None = None,
    ) -> None:
        with self.transaction(immediate=True) as db:
            db.execute(
                "INSERT INTO job_schedules VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET handler=excluded.handler, kind=excluded.kind, config_json=excluded.config_json, timezone=excluded.timezone, missed_run_policy=excluded.missed_run_policy, quiet_hours_json=excluded.quiet_hours_json, budget_units=excluded.budget_units, enabled=excluded.enabled, next_run_at=excluded.next_run_at",
                (
                    schedule_id,
                    handler,
                    kind,
                    json.dumps(config),
                    timezone,
                    missed_run_policy,
                    json.dumps(quiet_hours) if quiet_hours else None,
                    budget_units,
                    1,
                    next_run_at,
                ),
            )

    def schedules(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,handler,kind,timezone,missed_run_policy,budget_units,enabled,next_run_at FROM job_schedules ORDER BY id"
                )
            ]

    def delete_schedule(self, schedule_id: str) -> bool:
        with self.transaction(immediate=True) as db:
            return bool(db.execute("DELETE FROM job_schedules WHERE id=?", (schedule_id,)).rowcount)

    def events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                {**dict(r), "public": json.loads(r["public_json"])}
                for r in db.execute(
                    "SELECT * FROM job_events WHERE job_id=? AND sequence>? ORDER BY sequence",
                    (job_id, after),
                )
            ]

    def transition(
        self,
        job_id: str,
        expected: set[str],
        state: JobState,
        public: Mapping[str, Any] | None = None,
    ) -> bool:
        now = time.time()
        with self.transaction(immediate=True) as db:
            marks = ",".join("?" for _ in expected)
            changed = db.execute(
                f"UPDATE jobs SET state=?, updated_at=?, version=version+1 WHERE id=? AND state IN ({marks})",
                (state, now, job_id, *expected),
            ).rowcount
            if changed:
                self._event(db, job_id, "job.state_changed", {"state": state, **(public or {})})
            return bool(changed)

    def acquire_lease(
        self, owner: str, ttl: float = 30, *, now: float | None = None
    ) -> dict[str, str] | None:
        now = time.time() if now is None else now
        token = uuid.uuid4().hex
        with self.transaction(immediate=True) as db:
            db.execute("DELETE FROM job_leases WHERE expires_at<=?", (now,))
            row = db.execute(
                "SELECT j.id FROM jobs j LEFT JOIN job_leases l ON l.job_id=j.id WHERE j.state IN ('queued','leased','running','cancelling') AND l.job_id IS NULL ORDER BY j.created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            db.execute(
                "INSERT INTO job_leases VALUES(?,?,?,?,?,?)",
                (row["id"], owner, token, now, now + ttl, now),
            )
            db.execute(
                "UPDATE jobs SET state='leased', updated_at=? WHERE id=? AND state='queued'",
                (now, row["id"]),
            )
            self._event(db, row["id"], "job.leased", {"owner": owner, "expires_at": now + ttl})
            return {"job_id": row["id"], "token": token}

    def heartbeat(self, job_id: str, token: str, ttl: float = 30) -> bool:
        now = time.time()
        with self.transaction(immediate=True) as db:
            return bool(
                db.execute(
                    "UPDATE job_leases SET heartbeat_at=?, expires_at=? WHERE job_id=? AND token=? AND expires_at>?",
                    (now, now + ttl, job_id, token, now),
                ).rowcount
            )

    def release_lease(self, job_id: str, token: str) -> None:
        with self.transaction(immediate=True) as db:
            db.execute("DELETE FROM job_leases WHERE job_id=? AND token=?", (job_id, token))

    def claim_effect(
        self, job_id: str, operation_key: str, request: Mapping[str, Any]
    ) -> tuple[str, Any]:
        digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.transaction(immediate=True) as db:
            row = db.execute(
                "SELECT * FROM job_idempotency_claims WHERE operation_key=?", (operation_key,)
            ).fetchone()
            if row:
                if row["request_hash"] != digest:
                    raise ValueError("operation key reused with a different request")
                return row["status"], json.loads(row["result_json"]) if row["result_json"] else None
            db.execute(
                "INSERT INTO job_idempotency_claims VALUES(?,?,?,?,?,?)",
                (operation_key, job_id, digest, "started", None, time.time()),
            )
            return "started", None

    def complete_effect(self, operation_key: str, result: Any, status: str = "completed") -> None:
        with self.transaction(immediate=True) as db:
            db.execute(
                "UPDATE job_idempotency_claims SET status=?, result_json=?, updated_at=? WHERE operation_key=?",
                (status, json.dumps(result), time.time(), operation_key),
            )

    @staticmethod
    def _event(db: sqlite3.Connection, job_id: str, kind: str, public: Mapping[str, Any]) -> None:
        db.execute(
            "INSERT INTO job_events(job_id,type,public_json,created_at) VALUES(?,?,?,?)",
            (job_id, kind, json.dumps(public, separators=(",", ":")), time.time()),
        )
