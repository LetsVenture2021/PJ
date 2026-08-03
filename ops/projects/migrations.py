"""Transactional, additive-only schema migrations for Projects.

Operators should back up ``pj_data.sqlite3`` (for example with
``scripts/backup_db.sh``) before migrating a valuable database. Migrations
never rewrite existing chat, document, or artifact tables.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

_V1 = """
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
 description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active'
  CHECK(status IN ('active','archived')),
 template_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 archived_at TEXT);
CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects(updated_at);
CREATE TABLE IF NOT EXISTS project_memberships (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 member_id TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','removed')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id,member_id));
CREATE INDEX IF NOT EXISTS idx_project_memberships_project ON project_memberships(project_id,status);
CREATE TABLE IF NOT EXISTS project_instructions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','superseded')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_project_instructions_project ON project_instructions(project_id,status);
CREATE TABLE IF NOT EXISTS project_source_links (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 source_type TEXT NOT NULL, source_id TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',
 provenance_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'active'
  CHECK(status IN ('active','missing','removed')), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(project_id,source_type,source_id));
CREATE INDEX IF NOT EXISTS idx_project_sources_lookup ON project_source_links(project_id,source_type,status);
CREATE TABLE IF NOT EXISTS project_conversation_links (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 conversation_type TEXT NOT NULL CHECK(conversation_type IN ('responses','realtime')),
 conversation_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active'
  CHECK(status IN ('active','removed')), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(conversation_type,conversation_id));
CREATE INDEX IF NOT EXISTS idx_project_conversations_lookup ON project_conversation_links(project_id,status);
CREATE TABLE IF NOT EXISTS project_goals (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open'
  CHECK(status IN ('open','in_progress','completed','cancelled')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_project_goals_lookup ON project_goals(project_id,status,updated_at);
CREATE TABLE IF NOT EXISTS project_decisions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 summary TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'accepted'
  CHECK(status IN ('proposed','accepted','superseded','rejected')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_project_decisions_lookup ON project_decisions(project_id,status,updated_at);
CREATE TABLE IF NOT EXISTS project_budgets (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 budget_type TEXT NOT NULL, ceiling REAL NOT NULL CHECK(ceiling >= 0), consumed REAL NOT NULL DEFAULT 0
  CHECK(consumed >= 0), currency TEXT NOT NULL DEFAULT 'USD', status TEXT NOT NULL DEFAULT 'active'
  CHECK(status IN ('active','exhausted','closed')), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(project_id,budget_type));
CREATE INDEX IF NOT EXISTS idx_project_budgets_lookup ON project_budgets(project_id,status);
CREATE TABLE IF NOT EXISTS project_artifact_links (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 artifact_type TEXT NOT NULL CHECK(artifact_type IN ('docops_document','presentation_spec','codeops_task','image_asset','response_artifact')),
 artifact_id TEXT NOT NULL, relative_path TEXT, sha256 TEXT, provenance_json TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','missing','removed')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id,artifact_type,artifact_id));
CREATE INDEX IF NOT EXISTS idx_project_artifacts_lookup ON project_artifact_links(project_id,artifact_type,status);
CREATE TABLE IF NOT EXISTS project_memory_links (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 memory_kind TEXT NOT NULL, memory_ref_id TEXT NOT NULL, owner_global_approved INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','removed')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id,memory_kind,memory_ref_id));
CREATE INDEX IF NOT EXISTS idx_project_memory_lookup ON project_memory_links(project_id,status);
CREATE TABLE IF NOT EXISTS project_approval_links (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 approval_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','removed')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id,approval_id));
CREATE INDEX IF NOT EXISTS idx_project_approvals_lookup ON project_approval_links(project_id,status);
CREATE TABLE IF NOT EXISTS project_templates (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, instructions TEXT NOT NULL DEFAULT '',
 source_requirements_json TEXT NOT NULL DEFAULT '[]', output_expectations_json TEXT NOT NULL DEFAULT '[]',
 approval_policy_ref TEXT NOT NULL, budget_ceilings_json TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_project_templates_status ON project_templates(status,updated_at);
"""

MIGRATIONS = {1: _V1}


class MigrationError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(db_path: str | Path) -> None:
    """Bring an old or empty database to the known schema, idempotently."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("""CREATE TABLE IF NOT EXISTS project_schema_versions (
            version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, started_at TEXT NOT NULL,
            ended_at TEXT, status TEXT NOT NULL CHECK(status IN ('running','applied','failed')),
            error TEXT)""")
        conn.commit()
        future = conn.execute("SELECT MAX(version) FROM project_schema_versions").fetchone()[0]
        if future is not None and future > SCHEMA_VERSION:
            raise MigrationError(
                f"database project schema version {future} is newer than supported {SCHEMA_VERSION}"
            )
        for version, sql in MIGRATIONS.items():
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            prior = conn.execute(
                "SELECT checksum,status FROM project_schema_versions WHERE version=?", (version,)
            ).fetchone()
            if prior and prior[1] == "applied":
                if prior[0] != checksum:
                    raise MigrationError(f"checksum mismatch for applied migration {version}")
                continue
            started = _now()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT OR REPLACE INTO project_schema_versions "
                    "(version,checksum,started_at,ended_at,status,error) VALUES (?,?,?,?,?,NULL)",
                    (version, checksum, started, None, "running"),
                )
                for statement in sql.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute(
                    "UPDATE project_schema_versions SET ended_at=?,status='applied' WHERE version=?",
                    (_now(), version),
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                conn.execute(
                    "INSERT OR REPLACE INTO project_schema_versions VALUES (?,?,?,?,?,?)",
                    (version, checksum, started, _now(), "failed", str(exc)[:500]),
                )
                conn.commit()
                raise MigrationError(f"project migration {version} failed") from exc
    finally:
        conn.close()
