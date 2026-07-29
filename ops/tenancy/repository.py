"""SQLite repository with mandatory tenant scoping and metadata-only audit."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .models import AdminRole, GovernedResource, TenantContext

_POLICY_KEYS = frozenset(
    {
        "connectors",
        "models",
        "retention",
        "external_sharing",
        "tool_approvals",
        "budgets",
        "export",
        "automation",
        "regional_controls",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Mapping[str, object]) -> tuple[str, str]:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return raw, hashlib.sha256(raw.encode()).hexdigest()


class TenantRepository:
    """Persistence boundary whose public methods always require tenant context."""

    def __init__(self, database: str | Path):
        self.database = str(database)
        self._initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS tenants (
              tenant_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tenant_users (
              tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, email TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1, deprovisioned_at TEXT,
              PRIMARY KEY (tenant_id, user_id), FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
            );
            CREATE TABLE IF NOT EXISTS tenant_roles (
              tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
              PRIMARY KEY (tenant_id, user_id, role),
              FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_users(tenant_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS resource_grants (
              tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, resource_type TEXT NOT NULL,
              resource_id TEXT NOT NULL, permission TEXT NOT NULL,
              PRIMARY KEY (tenant_id, user_id, resource_type, resource_id, permission)
            );
            CREATE TABLE IF NOT EXISTS organization_policies (
              tenant_id TEXT NOT NULL, version INTEGER NOT NULL, policy_json TEXT NOT NULL,
              policy_hash TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
              PRIMARY KEY (tenant_id, version), UNIQUE (tenant_id, policy_hash)
            );
            CREATE TABLE IF NOT EXISTS execution_receipts (
              tenant_id TEXT NOT NULL, receipt_id TEXT NOT NULL, policy_version INTEGER NOT NULL,
              policy_hash TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
              PRIMARY KEY (tenant_id, receipt_id)
            );
            CREATE TABLE IF NOT EXISTS retention_records (
              tenant_id TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
              expires_at TEXT NOT NULL, legal_hold INTEGER NOT NULL DEFAULT 0,
              hold_reason TEXT, deleted_at TEXT,
              PRIMARY KEY (tenant_id, resource_type, resource_id)
            );
            CREATE TABLE IF NOT EXISTS tenant_audit_events (
              tenant_id TEXT NOT NULL, event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              actor_id TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
              action TEXT NOT NULL, policy_decision TEXT NOT NULL, request_id TEXT NOT NULL,
              status TEXT NOT NULL, timestamp TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_tenant_time
              ON tenant_audit_events(tenant_id, timestamp DESC);
            CREATE TABLE IF NOT EXISTS deletion_runs (
              tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, status TEXT NOT NULL,
              manifest_hash TEXT, created_at TEXT NOT NULL, completed_at TEXT,
              PRIMARY KEY (tenant_id, run_id)
            );
            """)

    def create_tenant(self, context: TenantContext, display_name: str) -> None:
        if context.actor_id != "bootstrap":
            raise PermissionError("tenant creation requires bootstrap context")
        with self._db() as db:
            db.execute(
                "INSERT INTO tenants VALUES (?,?,?)", (context.tenant_id, display_name, _now())
            )

    def put_user(self, context: TenantContext, user_id: str, email: str, *, active: bool) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO tenant_users(tenant_id,user_id,email,active,deprovisioned_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(tenant_id,user_id) DO UPDATE SET email=excluded.email,active=excluded.active,"
                "deprovisioned_at=excluded.deprovisioned_at",
                (context.tenant_id, user_id, email, int(active), None if active else _now()),
            )

    def set_roles(self, context: TenantContext, user_id: str, roles: set[AdminRole]) -> None:
        with self._db() as db:
            db.execute(
                "DELETE FROM tenant_roles WHERE tenant_id=? AND user_id=?",
                (context.tenant_id, user_id),
            )
            db.executemany(
                "INSERT INTO tenant_roles VALUES (?,?,?)",
                [(context.tenant_id, user_id, role.value) for role in roles],
            )

    def roles(self, context: TenantContext, user_id: str) -> set[AdminRole]:
        with self._db() as db:
            rows = db.execute(
                "SELECT role FROM tenant_roles r JOIN tenant_users u USING(tenant_id,user_id) "
                "WHERE r.tenant_id=? AND r.user_id=? AND u.active=1",
                (context.tenant_id, user_id),
            ).fetchall()
        return {AdminRole(row[0]) for row in rows}

    def replace_grants(
        self, context: TenantContext, user_id: str, grants: set[tuple[str, str, str]]
    ) -> None:
        with self._db() as db:
            db.execute(
                "DELETE FROM resource_grants WHERE tenant_id=? AND user_id=?",
                (context.tenant_id, user_id),
            )
            db.executemany(
                "INSERT INTO resource_grants VALUES (?,?,?,?,?)",
                [(context.tenant_id, user_id, *g) for g in grants],
            )

    def publish_policy(
        self, context: TenantContext, policy: Mapping[str, object], *, expected_version: int | None
    ) -> dict[str, object]:
        missing = _POLICY_KEYS.difference(policy)
        if missing:
            raise ValueError(f"policy missing sections: {', '.join(sorted(missing))}")
        raw, digest = _canonical(policy)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT COALESCE(MAX(version),0) FROM organization_policies WHERE tenant_id=?",
                (context.tenant_id,),
            ).fetchone()[0]
            if expected_version is not None and current != expected_version:
                raise RuntimeError("policy version conflict")
            version = current + 1
            db.execute(
                "INSERT INTO organization_policies VALUES (?,?,?,?,?,?)",
                (context.tenant_id, version, raw, digest, context.actor_id, _now()),
            )
        return {"version": version, "policy_hash": digest, "policy": dict(policy)}

    def current_policy(self, context: TenantContext) -> dict[str, object]:
        with self._db() as db:
            row = db.execute(
                "SELECT version,policy_json,policy_hash FROM organization_policies WHERE tenant_id=? ORDER BY version DESC LIMIT 1",
                (context.tenant_id,),
            ).fetchone()
        if row is None:
            raise LookupError("tenant has no policy")
        return {"version": row[0], "policy": json.loads(row[1]), "policy_hash": row[2]}

    def record_receipt(
        self, context: TenantContext, receipt_id: str, status: str
    ) -> dict[str, object]:
        policy = self.current_policy(context)
        with self._db() as db:
            db.execute(
                "INSERT INTO execution_receipts VALUES (?,?,?,?,?,?)",
                (
                    context.tenant_id,
                    receipt_id,
                    policy["version"],
                    policy["policy_hash"],
                    status,
                    _now(),
                ),
            )
        return {
            "receipt_id": receipt_id,
            "policy_version": policy["version"],
            "policy_hash": policy["policy_hash"],
            "status": status,
        }

    def schedule_retention(
        self, context: TenantContext, resource: GovernedResource, resource_id: str, expires_at: str
    ) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO retention_records(tenant_id,resource_type,resource_id,expires_at) VALUES(?,?,?,?) ON CONFLICT(tenant_id,resource_type,resource_id) DO UPDATE SET expires_at=excluded.expires_at",
                (context.tenant_id, resource.value, resource_id, expires_at),
            )

    def set_legal_hold(
        self,
        context: TenantContext,
        resource: GovernedResource,
        resource_id: str,
        reason: str | None,
    ) -> None:
        with self._db() as db:
            changed = db.execute(
                "UPDATE retention_records SET legal_hold=?,hold_reason=? WHERE tenant_id=? AND resource_type=? AND resource_id=?",
                (int(reason is not None), reason, context.tenant_id, resource.value, resource_id),
            ).rowcount
            if not changed:
                raise LookupError("retention record not found")

    def mark_deleted(
        self, context: TenantContext, resource: GovernedResource, resource_id: str
    ) -> None:
        with self._db() as db:
            row = db.execute(
                "SELECT legal_hold FROM retention_records WHERE tenant_id=? AND resource_type=? AND resource_id=?",
                (context.tenant_id, resource.value, resource_id),
            ).fetchone()
            if row is None:
                raise LookupError("retention record not found")
            if row[0]:
                raise PermissionError("resource is under legal hold")
            db.execute(
                "UPDATE retention_records SET deleted_at=? WHERE tenant_id=? AND resource_type=? AND resource_id=?",
                (_now(), context.tenant_id, resource.value, resource_id),
            )

    def audit(
        self,
        context: TenantContext,
        *,
        resource_type: str,
        resource_id: str,
        action: str,
        policy_decision: str,
        status: str,
    ) -> None:
        # Deliberately no payload/body/header columns or parameters.
        with self._db() as db:
            db.execute(
                "INSERT INTO tenant_audit_events(tenant_id,actor_id,resource_type,resource_id,action,policy_decision,request_id,status,timestamp) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    context.tenant_id,
                    context.actor_id,
                    resource_type,
                    resource_id,
                    action,
                    policy_decision,
                    context.request_id,
                    status,
                    _now(),
                ),
            )

    def list_audit(self, context: TenantContext, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT actor_id,resource_type,resource_id,action,policy_decision,request_id,status,timestamp FROM tenant_audit_events WHERE tenant_id=? ORDER BY event_id DESC LIMIT ?",
                (context.tenant_id, min(max(limit, 1), 1000)),
            ).fetchall()
        return [dict(row) for row in rows]

    def tenant_table_counts(self, context: TenantContext) -> dict[str, int]:
        tables = (
            "tenant_users",
            "tenant_roles",
            "resource_grants",
            "organization_policies",
            "execution_receipts",
            "retention_records",
            "tenant_audit_events",
        )
        with self._db() as db:
            return {
                table: db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE tenant_id=?", (context.tenant_id,)
                ).fetchone()[0]
                for table in tables
            }
