"""SQLite persistence for immutable workflow versions and durable jobs."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .compiler import CompiledWorkflow
from .models import canonical_json, WorkflowError


class WorkflowStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workflow_versions (
                  name TEXT NOT NULL, version TEXT NOT NULL, author TEXT NOT NULL,
                  source TEXT NOT NULL, manifest_hash TEXT NOT NULL UNIQUE,
                  manifest_json TEXT NOT NULL, permissions_json TEXT NOT NULL,
                  test_results_json TEXT NOT NULL, compatibility_version TEXT NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('draft','active','inactive','rejected')),
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY(name, version));
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_workflow
                  ON workflow_versions(name) WHERE status='active';
                CREATE TABLE IF NOT EXISTS workflow_jobs (
                  id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL, inputs_json TEXT NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
                  current_step TEXT, result_json TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(manifest_hash) REFERENCES workflow_versions(manifest_hash));
                CREATE TABLE IF NOT EXISTS workflow_runs (
                  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, manifest_hash TEXT NOT NULL,
                  status TEXT NOT NULL, metadata_json TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """)

    def publish(
        self, compiled: CompiledWorkflow, test_results: dict[str, Any], *, activate: bool
    ) -> None:
        definition = compiled.definition
        status = "active" if activate else "draft"
        with self._connect() as db:
            if db.execute(
                "SELECT 1 FROM workflow_versions WHERE name=? AND version=?",
                (definition.name, definition.version),
            ).fetchone():
                raise WorkflowError("workflow versions are immutable; publish a new version")
            if activate:
                db.execute(
                    "UPDATE workflow_versions SET status='inactive' WHERE name=? AND status='active'",
                    (definition.name,),
                )
            db.execute(
                "INSERT INTO workflow_versions VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                (
                    definition.name,
                    definition.version,
                    definition.author,
                    definition.source,
                    compiled.manifest_hash,
                    canonical_json(definition.as_dict()).decode(),
                    json.dumps(compiled.required_permissions),
                    json.dumps(test_results),
                    definition.compatibility_version,
                    status,
                ),
            )

    def activate(self, name: str, version: str) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT manifest_hash FROM workflow_versions WHERE name=? AND version=?",
                (name, version),
            ).fetchone()
            if row is None:
                raise WorkflowError("unknown workflow version")
            db.execute(
                "UPDATE workflow_versions SET status='inactive' WHERE name=? AND status='active'",
                (name,),
            )
            db.execute(
                "UPDATE workflow_versions SET status='active' WHERE name=? AND version=?",
                (name, version),
            )

    def create_job(self, compiled: CompiledWorkflow, inputs: Any) -> str:
        job_id = "WFJ-" + uuid.uuid4().hex
        with self._connect() as db:
            active = db.execute(
                "SELECT 1 FROM workflow_versions WHERE manifest_hash=? AND status='active'",
                (compiled.manifest_hash,),
            ).fetchone()
            if active is None:
                raise WorkflowError("only an active immutable workflow may create a job")
            db.execute(
                "INSERT INTO workflow_jobs(id,manifest_hash,inputs_json,status) VALUES(?,?,?,'queued')",
                (job_id, compiled.manifest_hash, canonical_json(inputs).decode()),
            )
        return job_id

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM workflow_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise WorkflowError("unknown workflow job")
        return dict(row)
