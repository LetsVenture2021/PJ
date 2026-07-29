"""Release, migration, encrypted-sync, backup, and operator continuity primitives."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SCHEMA_VERSION = 1
PROTOCOL_VERSION = 1
CONFLICT_RULES = {
    "artifact": "coexist",
    "comment": "append",
    "preference": "explicit_version_conflict",
    "document": "branch",
    "approval": "never_merge",
    "external_action_receipt": "immutable",
}
REASON_CODES = frozenset(
    {
        "OK",
        "PARITY_MISMATCH",
        "MIGRATION_REQUIRED",
        "SYNC_STALE",
        "BACKUP_STALE",
        "RESTORE_UNVERIFIED",
        "CONNECTOR_UNHEALTHY",
        "KILL_SWITCH_ACTIVE",
    }
)


class IntegrityError(ValueError):
    """Raised when authenticated or checksummed state is invalid."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sign_manifest(payload: Mapping[str, Any], private_key: Ed25519PrivateKey) -> dict[str, Any]:
    body = dict(payload)
    body.pop("signature", None)
    body["signature"] = base64.b64encode(private_key.sign(canonical_json(body))).decode()
    return body


def verify_manifest(manifest: Mapping[str, Any], public_key: Ed25519PublicKey) -> None:
    body = dict(manifest)
    try:
        signature = base64.b64decode(body.pop("signature"), validate=True)
        public_key.verify(signature, canonical_json(body))
    except Exception as exc:
        raise IntegrityError("manifest signature is invalid") from exc


def build_release_manifest(
    root: Path,
    *,
    git_commit: str,
    routes: Iterable[str],
    required_config_keys: Iterable[str],
    deployed_at: str,
) -> dict[str, Any]:
    assets = [root / "webrtc_client.html", root / "assets" / "pj_web_utils.js"]
    runtime_files = [root / "requirements.txt", root / "runtime_config.py"]
    return {
        "manifest_version": 1,
        "git_commit": git_commit,
        "client_assets": {str(p.relative_to(root)): sha256_file(p) for p in assets},
        "worker_hash": sha256_file(root / "pj_realtime_backend_worker.js"),
        "runtime_hash": hashlib.sha256(
            canonical_json({str(p.relative_to(root)): sha256_file(p) for p in runtime_files})
        ).hexdigest(),
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "tool_policy_hash": sha256_file(root / "tool_policy.json"),
        "routes": sorted(routes),
        "waf_paths": sorted(
            ["/execute-tool", "/health", "/responses/", "/tool-schemas", "/upload/"]
        ),
        "required_config_keys": sorted(required_config_keys),
        "deployed_at": deployed_at,
    }


def compare_release(manifest: Mapping[str, Any], observations: Mapping[str, Any]) -> list[str]:
    """Return stable field names that differ; never include observed sensitive values."""
    fields = ("git_commit", "worker_hash", "runtime_hash", "schema_version", "protocol_version")
    mismatches = [field for field in fields if observations.get(field) != manifest.get(field)]
    for field in ("client_assets", "routes", "waf_paths"):
        if field in observations and observations[field] != manifest.get(field):
            mismatches.append(field)
    return mismatches


@dataclass(frozen=True)
class Migration:
    version: int
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


class MigrationLedger:
    def __init__(self, migrations: Iterable[Migration], supported_version: int = SCHEMA_VERSION):
        self.migrations = {item.version: item for item in migrations}
        self.supported_version = supported_version

    def apply(self, database: Path, backup: Path) -> None:
        if not backup.is_file() or backup.stat().st_size == 0:
            raise IntegrityError("a completed pre-migration backup is required")
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS pj_schema_ledger (version INTEGER PRIMARY KEY, "
                "checksum TEXT NOT NULL, applied_at INTEGER NOT NULL)"
            )
            rows = dict(connection.execute("SELECT version, checksum FROM pj_schema_ledger"))
            if rows and max(rows) > self.supported_version:
                raise IntegrityError("database uses an unknown future schema")
            for version, checksum in rows.items():
                migration = self.migrations.get(version)
                if migration is None or migration.checksum != checksum:
                    raise IntegrityError(f"migration checksum mismatch at version {version}")
            for version in range(max(rows, default=0) + 1, self.supported_version + 1):
                migration = self.migrations.get(version)
                if migration is None:
                    raise IntegrityError(f"missing migration {version}")
                checksum = migration.checksum
                applied_at = int(time.time())
                try:
                    connection.executescript(
                        f"BEGIN IMMEDIATE;\n{migration.sql}\n"
                        "INSERT INTO pj_schema_ledger VALUES "
                        f"({version}, '{checksum}', {applied_at});\nCOMMIT;"
                    )
                except Exception:
                    connection.rollback()
                    raise
        finally:
            connection.close()


def make_change_record(
    *,
    device_id: str,
    scope: str,
    local_sequence: int,
    entity_type: str,
    entity_id: str,
    entity_version: int,
    operation_id: str,
    content: bytes,
    conflict_of: str | None = None,
) -> dict[str, Any]:
    if local_sequence < 1 or entity_version < 1 or entity_type not in CONFLICT_RULES:
        raise ValueError("invalid change record")
    return {
        "device_id": device_id,
        "scope": scope,
        "local_sequence": local_sequence,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_version": entity_version,
        "operation_id": operation_id,
        "content_hash": hashlib.sha256(content).hexdigest(),
        "conflict": {"rule": CONFLICT_RULES[entity_type], "of": conflict_of},
    }


class EncryptedSync:
    """AES-GCM envelope validation; key material is accepted separately and never serialized."""

    def __init__(self) -> None:
        self._last_sequence: dict[tuple[str, str], int] = {}
        self._revoked: set[str] = set()

    def revoke(self, device_id: str) -> None:
        self._revoked.add(device_id)

    def seal(
        self, record: Mapping[str, Any], content: bytes, key_id: str, key: bytes
    ) -> dict[str, Any]:
        metadata = dict(record)
        aad = canonical_json(metadata)
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, content, aad)
        return {
            "key_id": key_id,
            "record": metadata,
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
        }

    def open(self, envelope: Mapping[str, Any], keys: Mapping[str, bytes]) -> bytes:
        record = envelope["record"]
        device = record["device_id"]
        marker = (device, record["scope"])
        if device in self._revoked:
            raise IntegrityError("device is revoked")
        if record["local_sequence"] <= self._last_sequence.get(marker, 0):
            raise IntegrityError("replayed or stale change")
        try:
            key = keys[envelope["key_id"]]
            plaintext = AESGCM(key).decrypt(
                base64.b64decode(envelope["nonce"]),
                base64.b64decode(envelope["ciphertext"]),
                canonical_json(record),
            )
        except Exception as exc:
            raise IntegrityError("change authentication failed") from exc
        if hashlib.sha256(plaintext).hexdigest() != record["content_hash"]:
            raise IntegrityError("change content hash mismatch")
        self._last_sequence[marker] = record["local_sequence"]
        return plaintext


def create_backup(
    database: Path,
    artifacts: Iterable[Path],
    destination: Path,
    *,
    encryption: str = "external-key/aes-256-gcm",
) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    database_copy = destination / "database.sqlite"
    source = sqlite3.connect(database)
    target = sqlite3.connect(database_copy)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()
    inventory: dict[str, str] = {}
    artifact_dir = destination / "artifacts"
    for artifact in artifacts:
        artifact_dir.mkdir(exist_ok=True)
        copied = artifact_dir / artifact.name
        shutil.copy2(artifact, copied)
        inventory[artifact.name] = sha256_file(copied)
    manifest = {
        "database_hash": sha256_file(database_copy),
        "artifact_inventory": inventory,
        "schema_version": SCHEMA_VERSION,
        "encryption": encryption,
        "complete": True,
    }
    path = destination / "backup-manifest.json"
    path.write_bytes(canonical_json(manifest) + b"\n")
    verify_backup(destination)
    return path


def verify_backup(backup: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((backup / "backup-manifest.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError("backup manifest is corrupt or missing") from exc
    if manifest.get("complete") is not True:
        raise IntegrityError("backup is incomplete")
    if sha256_file(backup / "database.sqlite") != manifest.get("database_hash"):
        raise IntegrityError("backup database hash mismatch")
    for name, digest in manifest.get("artifact_inventory", {}).items():
        path = backup / "artifacts" / name
        if not path.is_file() or sha256_file(path) != digest:
            raise IntegrityError("backup artifact missing or corrupt")
    return manifest


def restore_backup(backup: Path, active: Path, validator: Callable[[Path], bool]) -> Path:
    verify_backup(backup)
    isolated = Path(tempfile.mkdtemp(prefix="pj-restore-", dir=active.parent))
    shutil.copytree(backup, isolated / "candidate")
    candidate = isolated / "candidate"
    if not validator(candidate):
        raise IntegrityError("isolated restore verification failed")
    replacement = active.with_name(f"{active.name}.replacement-{uuid.uuid4().hex}")
    shutil.copytree(candidate, replacement)
    if active.exists():
        os.replace(active, active.with_name(f"{active.name}.rollback"))
    os.replace(replacement, active)
    return isolated


def dashboard_snapshot(**metrics: Any) -> dict[str, Any]:
    """Produce a payload-only dashboard with bounded codes, never connector payloads."""
    result: dict[str, Any] = {}
    for name in (
        "release_parity",
        "migration_state",
        "sync_lag",
        "backup_age",
        "restore_verification",
        "connector_health",
        "kill_switches",
    ):
        item = metrics.get(name, {})
        code = item.get("reason_code", "OK") if isinstance(item, dict) else "OK"
        result[name] = {
            "ok": bool(item.get("ok", False)),
            "reason_code": code if code in REASON_CODES else "CONNECTOR_UNHEALTHY",
        }
    return result
