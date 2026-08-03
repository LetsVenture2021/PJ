"""Project records, explicit retrieval scopes, and portable bundles."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from ops.projects import migrations

_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = _ROOT / "pj_data.sqlite3"
BUNDLE_SCHEMA_VERSION = 1
STATUSES = {"active", "archived"}


class ProjectError(ValueError):
    """A safe, client-actionable project operation error."""


@dataclass(frozen=True)
class Project:
    id: str
    owner_id: str
    name: str
    description: str
    status: str
    template_id: str | None
    created_at: str
    updated_at: str
    archived_at: str | None


@dataclass(frozen=True)
class Membership:
    id: str
    project_id: str
    member_id: str
    role: str
    status: str


@dataclass(frozen=True)
class Instruction:
    id: str
    project_id: str
    body: str
    status: str


@dataclass(frozen=True)
class SourceLink:
    id: str
    project_id: str
    source_type: str
    source_id: str
    status: str


@dataclass(frozen=True)
class ConversationLink:
    id: str
    project_id: str
    conversation_type: str
    conversation_id: str
    status: str


@dataclass(frozen=True)
class Goal:
    id: str
    project_id: str
    title: str
    description: str
    status: str


@dataclass(frozen=True)
class Decision:
    id: str
    project_id: str
    summary: str
    rationale: str
    status: str


@dataclass(frozen=True)
class Budget:
    id: str
    project_id: str
    budget_type: str
    ceiling: float
    consumed: float
    status: str


@dataclass(frozen=True)
class ArtifactLink:
    id: str
    project_id: str
    artifact_type: str
    artifact_id: str
    status: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return str(uuid.uuid4())


def initialize(db_path: str | Path | None = None) -> None:
    migrations.run(db_path or DB_PATH)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    initialize(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _project(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def create_project(
    owner_id: str, name: str, description: str = "", *, template_id: str | None = None, db_path=None
) -> dict[str, Any]:
    if not owner_id.strip() or not name.strip():
        raise ProjectError("owner_id and name are required")
    now, project_id = _now(), _id()
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?)",
            (
                project_id,
                owner_id.strip(),
                name.strip(),
                description.strip(),
                "active",
                template_id,
                now,
                now,
                None,
            ),
        )
        conn.execute(
            "INSERT INTO project_memberships VALUES (?,?,?,?,?,?,?)",
            (_id(), project_id, owner_id.strip(), "owner", "active", now, now),
        )
    return get_project(project_id, owner_id=owner_id, db_path=db_path)


def get_project(project_id: str, *, owner_id: str | None = None, db_path=None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        sql, args = "SELECT * FROM projects WHERE id=?", [project_id]
        if owner_id is not None:
            sql += " AND owner_id=?"
            args.append(owner_id)
        row = conn.execute(sql, args).fetchone()
    if not row:
        raise ProjectError("project not found")
    return _project(row)


def list_projects(
    owner_id: str, *, include_archived: bool = False, db_path=None
) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        sql = "SELECT * FROM projects WHERE owner_id=?"
        args: list[Any] = [owner_id]
        if not include_archived:
            sql += " AND status='active'"
        return [dict(row) for row in conn.execute(sql + " ORDER BY updated_at DESC", args)]


def update_project(
    project_id: str,
    owner_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    db_path=None,
) -> dict[str, Any]:
    current = get_project(project_id, owner_id=owner_id, db_path=db_path)
    new_name = current["name"] if name is None else name.strip()
    if not new_name:
        raise ProjectError("name is required")
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE projects SET name=?,description=?,updated_at=? WHERE id=? AND owner_id=?",
            (
                new_name,
                current["description"] if description is None else description.strip(),
                _now(),
                project_id,
                owner_id,
            ),
        )
    return get_project(project_id, owner_id=owner_id, db_path=db_path)


def _set_status(project_id: str, owner_id: str, status: str, db_path=None) -> dict[str, Any]:
    if status not in STATUSES:
        raise ProjectError("invalid project status")
    get_project(project_id, owner_id=owner_id, db_path=db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE projects SET status=?,archived_at=?,updated_at=? WHERE id=?",
            (status, now if status == "archived" else None, now, project_id),
        )
    return get_project(project_id, owner_id=owner_id, db_path=db_path)


def archive_project(project_id: str, owner_id: str, *, db_path=None):
    return _set_status(project_id, owner_id, "archived", db_path)


def restore_project(project_id: str, owner_id: str, *, db_path=None):
    return _set_status(project_id, owner_id, "active", db_path)


def delete_project(project_id: str, owner_id: str, *, db_path=None) -> None:
    get_project(project_id, owner_id=owner_id, db_path=db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))


def _link(
    table: str,
    project_id: str,
    values: tuple[Any, ...],
    db_path=None,
    *,
    status: str = "active",
) -> dict[str, Any]:
    now, link_id = _now(), _id()
    placeholders = ",".join("?" for _ in range(len(values) + 5))
    with _connect(db_path) as conn:
        conn.execute(
            f"INSERT INTO {table} VALUES ({placeholders})",
            (link_id, project_id, *values, status, now, now),
        )
        return dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (link_id,)).fetchone())


def list_records(project_id: str, kind: str, *, db_path=None) -> list[dict[str, Any]]:
    tables = {
        "conversations": "project_conversation_links",
        "sources": "project_source_links",
        "artifacts": "project_artifact_links",
        "goals": "project_goals",
    }
    table = tables.get(kind)
    if not table:
        raise ProjectError("invalid project record kind")
    with _connect(db_path) as conn:
        return [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM {table} WHERE project_id=? AND status!='removed' ORDER BY updated_at DESC",
                (project_id,),
            )
        ]


def add_source(
    project_id: str,
    source_type: str,
    source_id: str,
    *,
    label: str = "",
    provenance: dict | None = None,
    db_path=None,
):
    if not source_type.strip() or not source_id.strip():
        raise ProjectError("source_type and source_id are required")
    return _link(
        "project_source_links",
        project_id,
        (
            source_type.strip(),
            source_id.strip(),
            label.strip(),
            json.dumps(provenance or {}, sort_keys=True),
        ),
        db_path,
    )


def add_goal(project_id: str, title: str, *, description: str = "", db_path=None):
    if not title.strip():
        raise ProjectError("goal title is required")
    return _link(
        "project_goals",
        project_id,
        (title.strip(), description.strip()),
        db_path,
        status="open",
    )


def link_conversation(
    project_id: str, conversation_id: str, conversation_type: str = "responses", *, db_path=None
):
    if conversation_type not in {"responses", "realtime"}:
        raise ProjectError("invalid conversation_type")
    return _link(
        "project_conversation_links", project_id, (conversation_type, conversation_id), db_path
    )


def unlink_conversation(conversation_id: str, *, db_path=None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "DELETE FROM project_conversation_links WHERE conversation_id=?", (conversation_id,)
        )


def link_artifact(
    project_id: str,
    artifact_type: str,
    artifact_id: str,
    *,
    relative_path: str | None = None,
    sha256: str | None = None,
    provenance: dict | None = None,
    db_path=None,
):
    allowed = {
        "docops_document",
        "presentation_spec",
        "codeops_task",
        "image_asset",
        "response_artifact",
    }
    if artifact_type not in allowed:
        raise ProjectError("invalid artifact_type")
    return _link(
        "project_artifact_links",
        project_id,
        (
            artifact_type,
            artifact_id,
            relative_path,
            sha256,
            json.dumps(provenance or {}, sort_keys=True),
        ),
        db_path,
    )


def scoped_references(
    scope: str = "current_project",
    *,
    project_id: str | None = None,
    owner_id: str | None = None,
    external_source_ids: list[str] | None = None,
    db_path=None,
) -> list[dict[str, Any]]:
    """Return only references admitted by an explicit retrieval boundary."""
    if scope == "current_project":
        if not project_id:
            raise ProjectError("project_id is required for current_project scope")
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT memory_kind AS kind,memory_ref_id AS ref_id FROM project_memory_links WHERE project_id=? AND status='active'",
                (project_id,),
            ).fetchall()
    elif scope == "owner_global_approved_memory":
        if not owner_id:
            raise ProjectError("owner_id is required for owner-global scope")
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT m.memory_kind AS kind,m.memory_ref_id AS ref_id FROM project_memory_links m JOIN projects p ON p.id=m.project_id WHERE p.owner_id=? AND m.owner_global_approved=1 AND m.status='active'",
                (owner_id,),
            ).fetchall()
    elif scope == "external_source_set":
        if not external_source_ids:
            raise ProjectError("external_source_ids are required for external source scope")
        return [
            {"kind": "external_source", "ref_id": item}
            for item in dict.fromkeys(external_source_ids)
        ]
    else:
        raise ProjectError("invalid retrieval scope")
    return [dict(row) for row in rows]


_EXPORT_TABLES = (
    "project_memberships",
    "project_instructions",
    "project_source_links",
    "project_conversation_links",
    "project_goals",
    "project_decisions",
    "project_budgets",
    "project_artifact_links",
    "project_memory_links",
    "project_approval_links",
)


def _safe_relative(value: str) -> Path:
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or ".." in pure.parts or "\\" in value:
        raise ProjectError("bundle contains an unsafe path")
    return Path(*pure.parts)


def export_project(
    project_id: str,
    destination: str | Path,
    *,
    owner_id: str | None = None,
    artifact_root: str | Path | None = None,
    db_path=None,
) -> Path:
    project = get_project(project_id, owner_id=owner_id, db_path=db_path)
    records: dict[str, Any] = {"projects": [project]}
    artifacts: list[dict[str, Any]] = []
    with _connect(db_path) as conn:
        for table in _EXPORT_TABLES:
            records[table] = [
                dict(row)
                for row in conn.execute(f"SELECT * FROM {table} WHERE project_id=?", (project_id,))
            ]
    root = Path(artifact_root).resolve() if artifact_root else None
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as bundle:
        if root:
            for row in records["project_artifact_links"]:
                if not row.get("relative_path"):
                    continue
                rel = _safe_relative(row["relative_path"])
                path = (root / rel).resolve()
                if root not in path.parents or not path.is_file():
                    artifacts.append(
                        {
                            "artifact_id": row["artifact_id"],
                            "relative_path": rel.as_posix(),
                            "status": "missing",
                        }
                    )
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                bundle.write(path, f"artifacts/{rel.as_posix()}")
                artifacts.append(
                    {
                        "artifact_id": row["artifact_id"],
                        "relative_path": rel.as_posix(),
                        "sha256": digest,
                        "status": "included",
                    }
                )
        manifest = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "exported_at": _now(),
            "records": records,
            "artifacts": artifacts,
            "provenance": {"application": "PJ"},
        }
        bundle.writestr("manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
    return destination


def import_project(
    bundle_path: str | Path, *, owner_id: str, artifact_root: str | Path, db_path=None
) -> dict[str, Any]:
    root = Path(artifact_root).resolve()
    with zipfile.ZipFile(bundle_path) as bundle:
        try:
            manifest = json.loads(bundle.read("manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ProjectError("invalid project bundle manifest") from exc
        if manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION or not isinstance(
            manifest.get("records"), dict
        ):
            raise ProjectError("unsupported project bundle schema")
        pending: list[tuple[Path, bytes]] = []
        for artifact in manifest.get("artifacts", []):
            if artifact.get("status") != "included":
                continue
            rel = _safe_relative(str(artifact.get("relative_path", "")))
            member = f"artifacts/{rel.as_posix()}"
            try:
                content = bundle.read(member)
            except KeyError as exc:
                raise ProjectError("bundle artifact is missing") from exc
            if hashlib.sha256(content).hexdigest() != artifact.get("sha256"):
                raise ProjectError("bundle artifact hash mismatch")
            target = (root / rel).resolve()
            if root != target and root not in target.parents:
                raise ProjectError("bundle artifact escapes destination")
            pending.append((target, content))
        source_projects = manifest["records"].get("projects", [])
        if len(source_projects) != 1:
            raise ProjectError("bundle must contain exactly one project")
        source = source_projects[0]
        imported = create_project(
            owner_id,
            str(source.get("name", "")),
            str(source.get("description", "")),
            db_path=db_path,
        )
        old_id, new_id = source.get("id"), imported["id"]
        try:
            with _connect(db_path) as conn:
                for table in _EXPORT_TABLES:
                    if table == "project_memberships":
                        continue
                    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
                    for record in manifest["records"].get(table, []):
                        if record.get("project_id") != old_id:
                            raise ProjectError("bundle contains cross-project records")
                        clean = {key: value for key, value in record.items() if key in columns}
                        clean["id"], clean["project_id"] = _id(), new_id
                        conn.execute(
                            f"INSERT INTO {table} ({','.join(clean)}) VALUES ({','.join('?' for _ in clean)})",
                            tuple(clean.values()),
                        )
                for target, content in pending:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".project-import-")
                    try:
                        with os.fdopen(fd, "wb") as handle:
                            handle.write(content)
                        os.replace(tmp, target)
                    finally:
                        if os.path.exists(tmp):
                            os.unlink(tmp)
        except Exception:
            delete_project(new_id, owner_id, db_path=db_path)
            raise
    return get_project(new_id, owner_id=owner_id, db_path=db_path)
