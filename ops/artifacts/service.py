"""Artifact facade: immutable catalog over the existing domain-owned state."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import sqlite3
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .models import ArtifactDescriptor, OutcomeRecord, RevisionRequest, ValidationFinding
from .previews import MAX_PREVIEW_BYTES, compare, preview

_ID = re.compile(r"^ART-[a-f0-9]{32}$")
_DOMAINS = {"docs", "images", "presentations", "code"}
_REVISION_ROUTERS: dict[str, Callable[[RevisionRequest, ArtifactDescriptor], str]] = {}


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class ArtifactError(RuntimeError):
    """Safe facade error with a stable machine code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArtifactFacade:
    """SQLite metadata catalog; domain bytes and state remain where they are."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS artifacts_facade (
                    artifact_id TEXT PRIMARY KEY, domain TEXT NOT NULL, media_type TEXT NOT NULL,
                    source_version TEXT NOT NULL, content_hash TEXT NOT NULL, path TEXT NOT NULL,
                    lineage_json TEXT NOT NULL, project_id TEXT, session_id TEXT, job_id TEXT,
                    verification_status TEXT NOT NULL, validation_json TEXT NOT NULL,
                    operations_json TEXT NOT NULL, created_at TEXT NOT NULL, tombstoned_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS artifacts_facade_version
                    ON artifacts_facade(domain, source_version, content_hash);
                CREATE TABLE IF NOT EXISTS artifact_revision_keys (
                    idempotency_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                    result_artifact_id TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_outcomes (
                    outcome_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL,
                    project_id TEXT, session_id TEXT, job_id TEXT, created_at TEXT NOT NULL
                );
            """)

    def register(
        self,
        *,
        path: Path,
        domain: str,
        source_version: str,
        artifact_id: str | None = None,
        media_type: str | None = None,
        lineage_parents: tuple[str, ...] = (),
        project_id: str | None = None,
        session_id: str | None = None,
        job_id: str | None = None,
    ) -> ArtifactDescriptor:
        path = Path(path).resolve(strict=True)
        if domain not in _DOMAINS or not path.is_file():
            raise ArtifactError("invalid_artifact", "Artifact domain or content is invalid.")
        artifact_id = artifact_id or f"ART-{uuid.uuid4().hex}"
        if not _ID.fullmatch(artifact_id):
            raise ArtifactError("invalid_artifact", "Artifact ID is invalid.")
        digest = _sha(path)
        media_type = media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        operations = ("preview", "validate", "compare", "download", "revise", "restore")
        created = _now()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO artifacts_facade VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        artifact_id,
                        domain,
                        media_type,
                        str(source_version),
                        digest,
                        str(path),
                        json.dumps(lineage_parents),
                        project_id,
                        session_id,
                        job_id,
                        "verified",
                        "[]",
                        json.dumps(operations),
                        created,
                        None,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ArtifactError("artifact_conflict", "Artifact version already exists.") from exc
        return self.get(artifact_id, project_id=project_id, session_id=session_id)

    def get(
        self, artifact_id: str, *, project_id: str | None, session_id: str | None
    ) -> ArtifactDescriptor:
        row = self._authorized_row(artifact_id, project_id, session_id)
        return self._descriptor(row)

    def _authorized_row(
        self, artifact_id: str, project_id: str | None, session_id: str | None
    ) -> sqlite3.Row:
        if not _ID.fullmatch(artifact_id):
            raise ArtifactError("artifact_not_found", "Artifact was not found.")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts_facade WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
        if (
            row is None
            or (row["project_id"] and row["project_id"] != project_id)
            or (row["session_id"] and row["session_id"] != session_id)
        ):
            raise ArtifactError("artifact_not_found", "Artifact was not found.")
        return row

    @staticmethod
    def _descriptor(row: sqlite3.Row) -> ArtifactDescriptor:
        findings = tuple(ValidationFinding(**item) for item in json.loads(row["validation_json"]))
        return ArtifactDescriptor(
            row["artifact_id"],
            row["domain"],
            row["media_type"],
            row["source_version"],
            row["content_hash"],
            tuple(json.loads(row["lineage_json"])),
            row["project_id"],
            row["session_id"],
            row["job_id"],
            row["verification_status"],
            findings,
            row["created_at"],
            tuple(json.loads(row["operations_json"])),
            row["tombstoned_at"],
        )

    def verified_path(
        self, artifact_id: str, *, project_id: str | None, session_id: str | None
    ) -> Path:
        row = self._authorized_row(artifact_id, project_id, session_id)
        if row["tombstoned_at"]:
            raise ArtifactError("artifact_tombstoned", "Artifact has been tombstoned.")
        path = Path(row["path"])
        if not path.is_file() or path.is_symlink():
            raise ArtifactError("artifact_missing", "Artifact content is unavailable.")
        if _sha(path) != row["content_hash"]:
            raise ArtifactError("hash_mismatch", "Artifact failed integrity verification.")
        return path

    def preview(self, artifact_id: str, *, project_id: str | None, session_id: str | None) -> dict:
        descriptor = self.get(artifact_id, project_id=project_id, session_id=session_id)
        path = self.verified_path(artifact_id, project_id=project_id, session_id=session_id)
        return {
            "artifact": descriptor.as_dict(),
            "preview": preview(path, descriptor.media_type),
            "limit_bytes": MAX_PREVIEW_BYTES,
        }

    def compare(
        self, left_id: str, right_id: str, *, project_id: str | None, session_id: str | None
    ) -> dict:
        left = self.get(left_id, project_id=project_id, session_id=session_id)
        right = self.get(right_id, project_id=project_id, session_id=session_id)
        if left.domain != right.domain:
            raise ArtifactError("incompatible_artifacts", "Artifacts belong to different domains.")
        return compare(
            self.verified_path(left_id, project_id=project_id, session_id=session_id),
            self.verified_path(right_id, project_id=project_id, session_id=session_id),
            left.media_type,
        )

    def tombstone(
        self, artifact_id: str, *, project_id: str | None, session_id: str | None
    ) -> ArtifactDescriptor:
        self._authorized_row(artifact_id, project_id, session_id)
        with self._connect() as conn:
            conn.execute(
                "UPDATE artifacts_facade SET tombstoned_at=? WHERE artifact_id=? AND tombstoned_at IS NULL",
                (_now(), artifact_id),
            )
        return self.get(artifact_id, project_id=project_id, session_id=session_id)

    def restore(
        self, artifact_id: str, *, project_id: str | None, session_id: str | None
    ) -> ArtifactDescriptor:
        old = self.get(artifact_id, project_id=project_id, session_id=session_id)
        path = self.verified_path(artifact_id, project_id=project_id, session_id=session_id)
        return self.register(
            path=path,
            domain=old.domain,
            source_version=f"{old.source_version}-restored-{uuid.uuid4().hex[:8]}",
            lineage_parents=(artifact_id,),
            project_id=project_id,
            session_id=session_id,
            job_id=old.job_id,
        )

    def validate(
        self,
        artifact_id: str,
        *,
        project_id: str | None,
        session_id: str | None,
        declared_code_checks: tuple[str, ...] = (),
    ) -> ArtifactDescriptor:
        descriptor = self.get(artifact_id, project_id=project_id, session_id=session_id)
        path = self.verified_path(artifact_id, project_id=project_id, session_id=session_id)
        findings = _validate_file(path, descriptor.domain, declared_code_checks)
        status = "failed" if any(item.severity == "error" for item in findings) else "verified"
        with self._connect() as conn:
            conn.execute(
                "UPDATE artifacts_facade SET validation_json=?, verification_status=? WHERE artifact_id=?",
                (json.dumps([asdict(item) for item in findings]), status, artifact_id),
            )
        return self.get(artifact_id, project_id=project_id, session_id=session_id)

    def revise(
        self, request: RevisionRequest, *, project_id: str | None, session_id: str | None
    ) -> ArtifactDescriptor:
        current = self.get(request.artifact_id, project_id=project_id, session_id=session_id)
        if current.source_version != request.source_version:
            raise ArtifactError("version_conflict", "Revision source version is not current.")
        canonical = json.dumps(asdict(request), sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256(canonical.encode()).hexdigest()
        with self._connect() as conn:
            prior = conn.execute(
                "SELECT * FROM artifact_revision_keys WHERE idempotency_key=?",
                (request.idempotency_key,),
            ).fetchone()
        if prior:
            if prior["request_hash"] != request_hash:
                raise ArtifactError(
                    "idempotency_conflict", "Idempotency key was used for another revision."
                )
            return self.get(
                prior["result_artifact_id"], project_id=project_id, session_id=session_id
            )
        router = _REVISION_ROUTERS.get(current.domain)
        if router is None:
            raise ArtifactError(
                "revision_unavailable", "Owning domain service has no revision router."
            )
        result_id = router(request, current)
        revised = self.get(result_id, project_id=project_id, session_id=session_id)
        if request.artifact_id not in revised.lineage_parents:
            raise ArtifactError(
                "invalid_lineage", "Domain revision did not preserve its parent lineage."
            )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO artifact_revision_keys VALUES (?,?,?,?)",
                (request.idempotency_key, request_hash, result_id, _now()),
            )
        return revised

    def record_outcome(self, outcome: OutcomeRecord) -> OutcomeRecord:
        metadata = outcome.as_dict()
        # The contract deliberately has no prompt or raw-payload fields.
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO artifact_outcomes VALUES (?,?,?,?,?,?)",
                (
                    outcome.outcome_id,
                    json.dumps(metadata),
                    outcome.project_id,
                    outcome.session_id,
                    outcome.job_id,
                    outcome.created_at or _now(),
                ),
            )
        return outcome

    def get_outcome(
        self, outcome_id: str, *, project_id: str | None, session_id: str | None
    ) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifact_outcomes WHERE outcome_id=?", (outcome_id,)
            ).fetchone()
        if (
            row is None
            or (row["project_id"] and row["project_id"] != project_id)
            or (row["session_id"] and row["session_id"] != session_id)
        ):
            raise ArtifactError("outcome_not_found", "Outcome was not found.")
        return json.loads(row["metadata_json"])


def register_revision_router(
    domain: str, router: Callable[[RevisionRequest, ArtifactDescriptor], str]
) -> None:
    if domain not in _DOMAINS:
        raise ValueError("unknown artifact domain")
    _REVISION_ROUTERS[domain] = router


def _validate_file(
    path: Path, domain: str, checks: tuple[str, ...]
) -> tuple[ValidationFinding, ...]:
    findings: list[ValidationFinding] = []
    if domain == "images":
        try:
            result = preview(path, mimetypes.guess_type(path.name)[0] or "image/*")
            if not result.get("width") or not result.get("height"):
                findings.append(
                    ValidationFinding(
                        "image_dimensions_missing", "error", "Image dimensions are missing."
                    )
                )
        except (OSError, ValueError) as exc:
            findings.append(ValidationFinding("image_decode_failed", "error", str(exc)))
    elif domain == "docs":
        text = (
            path.read_text("utf-8", errors="replace")
            if path.suffix.lower() in {".md", ".txt"}
            else ""
        )
        for url in re.findall(r"https?://[^\s)>]+", text):
            if " " in url:
                findings.append(ValidationFinding("broken_link", "error", "Malformed link.", url))
        if re.search(r"\[(?:citation needed|cite)\]", text, re.I):
            findings.append(
                ValidationFinding("citation_gap", "warning", "Citation marker is unresolved.")
            )
    elif domain == "presentations":
        data = preview(
            path, "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        for slide in data.get("slides", []):
            if len(slide["text"]) > 1300:
                findings.append(
                    ValidationFinding(
                        "slide_overflow",
                        "error",
                        "Slide text exceeds the safe limit.",
                        slide["slide"],
                    )
                )
    elif domain == "code":
        for check in checks:
            if check not in {"tests", "lint", "typecheck", "build", "format"}:
                findings.append(
                    ValidationFinding(
                        "code_check_not_declared",
                        "error",
                        "Check is outside the CodeOps sandbox allowlist.",
                        check,
                    )
                )
    return tuple(findings)
