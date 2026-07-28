"""Governed image assets and opt-in image generation for PJ."""

from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import tempfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

import docops


_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("PJ_DB_PATH", _ROOT / "pj_data.sqlite3"))
ASSET_ROOT = Path(
    os.getenv(
        "PJ_IMAGE_ASSET_ROOT",
        _ROOT / "documents" / "exports" / "image-assets",
    )
)
_IMAGE_FORMATS = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}
_SVG_TAGS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
_SVG_ATTRS = {
    "xmlns", "viewBox", "width", "height", "fill", "stroke", "stroke-width",
    "d", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "points", "transform", "opacity", "fill-opacity", "stroke-opacity",
    "stroke-linecap", "stroke-linejoin",
}
_SVG_NAME_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9:-]*)\b")
_SVG_ATTR_RE = re.compile(r"\s([A-Za-z_:][A-Za-z0-9:._-]*)\s*=")
_SVG_UNSAFE_RE = re.compile(
    r"<\s*(?:script|foreignObject|image|use|a|style)\b|"
    r"\bon[a-z]+\s*=|(?:href|xlink:href)\s*=|url\s*\(|"
    r"\b(?:javascript|data|file|https?|ftp)\s*:",
    re.IGNORECASE,
)
_CREDENTIAL_RE = re.compile(
    rb"(?:sk-[A-Za-z0-9_-]{20,}|"
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"(?:api[_-]?key|client[_-]?secret|access[_-]?token)"
    rb"\s*[:=]\s*[\"'][^\"'\s]{12,})",
    re.IGNORECASE,
)


class ImageOpsError(RuntimeError):
    """Typed image-operation failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def as_dict(self) -> dict[str, str]:
        return {"status": "error", "code": self.code, "error": str(self)}


def _now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS imageops_assets (
                asset_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                parent_asset_id TEXT,
                operation TEXT NOT NULL,
                prompt_sha256 TEXT,
                provider TEXT,
                provider_asset_id TEXT,
                width INTEGER,
                height INTEGER,
                mime_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                quality_status TEXT NOT NULL,
                idempotency_key TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                tombstoned_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_imageops_idempotency "
            "ON imageops_assets(idempotency_key) WHERE idempotency_key IS NOT NULL"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS imageops_idempotency (
                idempotency_key TEXT PRIMARY KEY,
                request_sha256 TEXT NOT NULL,
                asset_id TEXT,
                status TEXT NOT NULL,
                provider_payload_sha256 TEXT,
                provider_name TEXT,
                provider_asset_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(imageops_idempotency)"
            ).fetchall()
        }
        for name in (
            "provider_payload_sha256",
            "provider_name",
            "provider_asset_id",
        ):
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE imageops_idempotency ADD COLUMN {name} TEXT"
                )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS imageops_budget_reservations (
                idempotency_key TEXT PRIMARY KEY,
                estimated_usd REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS imageops_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id TEXT NOT NULL,
                rating INTEGER NOT NULL,
                comments TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(asset_id) REFERENCES imageops_assets(asset_id)
            )
            """
        )
        conn.commit()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _safe_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, item in list(value.items())[:24]:
        if not isinstance(key, str):
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            safe[key[:80]] = item if not isinstance(item, str) else item[:1000]
    return safe


def _canonical_sha(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _storage_claim_is_stale(updated_at: str) -> bool:
    raw_ttl = os.getenv("PJ_IMAGE_STORAGE_CLAIM_TTL_SECONDS", "300")
    try:
        ttl_seconds = int(raw_ttl)
        timestamp = datetime.fromisoformat(updated_at)
    except (TypeError, ValueError) as exc:
        raise ImageOpsError(
            "image_config_invalid",
            "image storage claim state or TTL is invalid",
        ) from exc
    if not 30 <= ttl_seconds <= 86_400:
        raise ImageOpsError(
            "image_config_invalid",
            "PJ_IMAGE_STORAGE_CLAIM_TTL_SECONDS must be between 30 and 86400",
        )
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return (datetime.now(UTC) - timestamp).total_seconds() >= ttl_seconds


def _claim_idempotency(
    key: str | None,
    request_sha256: str,
) -> dict[str, Any]:
    if not key:
        return {"state": "unclaimed"}
    now = _now()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT request_sha256, asset_id, status, "
            "provider_payload_sha256, provider_name, provider_asset_id, "
            "updated_at "
            "FROM imageops_idempotency WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        if row:
            if row[0] != request_sha256:
                raise ImageOpsError(
                    "idempotency_conflict",
                    "idempotency key was already used for a different image request",
                )
            if row[2] == "ready" and row[1]:
                return {"state": "ready", "asset_id": row[1]}
            if row[2] == "provider_complete":
                cursor = conn.execute(
                    "UPDATE imageops_idempotency SET status='storing', "
                    "updated_at=? WHERE idempotency_key=? "
                    "AND request_sha256=? AND status='provider_complete'",
                    (now, key, request_sha256),
                )
                if cursor.rowcount != 1:
                    raise ImageOpsError(
                        "idempotency_in_progress",
                        "an image request with this idempotency key is already in progress",
                    )
                return {
                    "state": "provider_complete",
                    "payload_sha256": row[3],
                    "provider_name": row[4],
                    "provider_asset_id": row[5],
                }
            if row[2] == "storing" and _storage_claim_is_stale(row[6]):
                if row[3] and row[4]:
                    cursor = conn.execute(
                        "UPDATE imageops_idempotency SET updated_at=? "
                        "WHERE idempotency_key=? AND request_sha256=? "
                        "AND status='storing' AND updated_at=?",
                        (now, key, request_sha256, row[6]),
                    )
                    if cursor.rowcount != 1:
                        raise ImageOpsError(
                            "idempotency_in_progress",
                            "an image request with this idempotency key is already in progress",
                        )
                    return {
                        "state": "provider_complete",
                        "payload_sha256": row[3],
                        "provider_name": row[4],
                        "provider_asset_id": row[5],
                    }
                cursor = conn.execute(
                    "UPDATE imageops_idempotency SET status='pending', "
                    "updated_at=? WHERE idempotency_key=? "
                    "AND request_sha256=? AND status='storing' "
                    "AND updated_at=?",
                    (now, key, request_sha256, row[6]),
                )
                if cursor.rowcount == 1:
                    return {"state": "claimed"}
            raise ImageOpsError(
                "idempotency_in_progress",
                "an image request with this idempotency key is already in progress",
            )
        conn.execute(
            "INSERT INTO imageops_idempotency "
            "(idempotency_key, request_sha256, status, created_at, updated_at) "
            "VALUES (?,?,'pending',?,?)",
            (key, request_sha256, now, now),
        )
    return {"state": "claimed"}


def _provider_staging_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return ASSET_ROOT / ".provider-staging" / f"{digest}.bin"


def _stage_provider_result(
    key: str,
    request_sha256: str,
    provider: str,
    result: "ProviderImage",
) -> Path:
    path = _provider_staging_path(key)
    payload_sha256 = hashlib.sha256(result.data).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    _atomic_write(path, result.data)
    path.chmod(0o600)
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE imageops_idempotency SET status='storing', "
            "provider_payload_sha256=?, provider_name=?, provider_asset_id=?, "
            "updated_at=? WHERE idempotency_key=? AND request_sha256=? "
            "AND status='pending'",
            (
                payload_sha256,
                provider,
                result.provider_asset_id,
                _now(),
                key,
                request_sha256,
            ),
        )
        if cursor.rowcount != 1:
            path.unlink(missing_ok=True)
            raise ImageOpsError(
                "idempotency_state_error",
                "image provider result could not be persisted",
            )
    return path


def _load_staged_provider_result(
    key: str,
    *,
    payload_sha256: str,
    provider_asset_id: str | None,
) -> tuple[Path, "ProviderImage"]:
    path = _provider_staging_path(key)
    root = (ASSET_ROOT / ".provider-staging").resolve()
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ImageOpsError(
            "provider_result_unavailable",
            "the completed provider result is unavailable for retry",
        ) from exc
    if resolved.parent != root or not resolved.is_file() or resolved.is_symlink():
        raise ImageOpsError(
            "provider_result_unavailable",
            "the completed provider result is not a safe regular file",
        )
    data = resolved.read_bytes()
    if not payload_sha256 or hashlib.sha256(data).hexdigest() != payload_sha256:
        raise ImageOpsError(
            "provider_result_integrity_failed",
            "the completed provider result failed checksum validation",
        )
    return resolved, ProviderImage(
        data=data,
        provider_asset_id=provider_asset_id,
    )


def _mark_provider_result_retryable(
    key: str,
    request_sha256: str,
) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE imageops_idempotency SET status='provider_complete', "
            "updated_at=? WHERE idempotency_key=? AND request_sha256=? "
            "AND status='storing'",
            (_now(), key, request_sha256),
        )


def _mark_storage_started(key: str, request_sha256: str) -> None:
    with _db() as conn:
        cursor = conn.execute(
            "UPDATE imageops_idempotency SET status='storing', updated_at=? "
            "WHERE idempotency_key=? AND request_sha256=? AND status='pending'",
            (_now(), key, request_sha256),
        )
        if cursor.rowcount != 1:
            raise ImageOpsError(
                "idempotency_state_error",
                "image storage could not be started",
            )


def _complete_idempotency(
    key: str | None,
    request_sha256: str,
    asset_id: str,
) -> None:
    if not key:
        return
    with _db() as conn:
        cursor = conn.execute(
            "UPDATE imageops_idempotency SET asset_id=?, status='ready', "
            "updated_at=? WHERE idempotency_key=? AND request_sha256=? "
            "AND status='storing'",
            (asset_id, _now(), key, request_sha256),
        )
        if cursor.rowcount != 1:
            raise ImageOpsError(
                "idempotency_state_error",
                "image idempotency state could not be finalized",
            )


def _release_idempotency(key: str | None, request_sha256: str) -> None:
    if not key:
        return
    with _db() as conn:
        conn.execute(
            "DELETE FROM imageops_idempotency WHERE idempotency_key=? "
            "AND request_sha256=? AND status IN ('pending','storing')",
            (key, request_sha256),
        )


def _budget_settings() -> tuple[float, float]:
    try:
        budget = float(os.getenv("PJ_IMAGE_BUDGET_USD", "0"))
        estimate = float(os.getenv("PJ_IMAGE_ESTIMATED_CALL_USD", "0"))
    except ValueError:
        return 0.0, 0.0
    return max(0.0, budget), max(0.0, estimate)


def _reserve_budget(idempotency_key: str) -> float:
    budget, estimate = _budget_settings()
    if budget <= 0 or estimate <= 0:
        raise ImageOpsError(
            "budget_not_approved",
            "approved image budget and per-call estimate must both be configured",
        )
    now = _now()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT estimated_usd, status FROM imageops_budget_reservations "
            "WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            if existing[1] in {"reserved", "committed"}:
                return float(existing[0])
            conn.execute(
                "DELETE FROM imageops_budget_reservations WHERE idempotency_key=?",
                (idempotency_key,),
            )
        used = conn.execute(
            "SELECT COALESCE(SUM(estimated_usd), 0) "
            "FROM imageops_budget_reservations "
            "WHERE status IN ('reserved','committed')"
        ).fetchone()[0]
        if float(used) + estimate > budget:
            raise ImageOpsError(
                "budget_exhausted",
                "the approved image-generation budget is exhausted",
            )
        conn.execute(
            "INSERT INTO imageops_budget_reservations "
            "(idempotency_key, estimated_usd, status, created_at, updated_at) "
            "VALUES (?,?,'reserved',?,?)",
            (idempotency_key, estimate, now, now),
        )
    return estimate


def _mark_budget(idempotency_key: str, status: str) -> None:
    if status not in {"committed", "failed"}:
        raise ValueError("invalid budget reservation status")
    with _db() as conn:
        conn.execute(
            "UPDATE imageops_budget_reservations SET status=?, updated_at=? "
            "WHERE idempotency_key=? AND status='reserved'",
            (status, _now(), idempotency_key),
        )


def _validate_raster(data: bytes) -> tuple[str, str, int, int]:
    if not data:
        raise ImageOpsError("empty_asset", "image bytes are empty")
    max_bytes = int(os.getenv("PJ_IMAGE_MAX_BYTES", str(25 * 1024 * 1024)))
    if len(data) > max_bytes:
        raise ImageOpsError("asset_too_large", "image exceeds the configured size limit")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            fmt = str(image.format or "").upper()
            width, height = image.size
    except (OSError, ValueError) as exc:
        raise ImageOpsError("invalid_image", "image bytes failed validation") from exc
    if fmt not in _IMAGE_FORMATS:
        raise ImageOpsError("unsupported_image_format", f"unsupported image format: {fmt}")
    max_pixels = int(os.getenv("PJ_IMAGE_MAX_PIXELS", "40000000"))
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ImageOpsError("invalid_dimensions", "image dimensions exceed configured limits")
    extension, mime_type = _IMAGE_FORMATS[fmt]
    return extension, mime_type, width, height


def _validate_svg(svg: str) -> tuple[bytes, int, int]:
    if not isinstance(svg, str) or not svg.strip():
        raise ImageOpsError("invalid_svg", "SVG content is required")
    if len(svg.encode("utf-8")) > int(os.getenv("PJ_IMAGE_MAX_BYTES", str(25 * 1024 * 1024))):
        raise ImageOpsError("asset_too_large", "SVG exceeds the configured size limit")
    active_scan = re.sub(
        r'\sxmlns\s*=\s*["\']http://www\.w3\.org/2000/svg["\']',
        "",
        svg,
        flags=re.IGNORECASE,
    )
    if _SVG_UNSAFE_RE.search(active_scan) or "<!DOCTYPE" in svg.upper():
        raise ImageOpsError("unsafe_svg", "SVG contains disallowed active or external content")
    names = {name.split(":")[-1] for name in _SVG_NAME_RE.findall(svg)}
    if not names or not names.issubset(_SVG_TAGS):
        raise ImageOpsError("unsafe_svg", "SVG contains disallowed elements")
    attrs = {name.split(":")[-1] for name in _SVG_ATTR_RE.findall(svg)}
    if not attrs.issubset(_SVG_ATTRS):
        raise ImageOpsError("unsafe_svg", "SVG contains disallowed attributes")
    viewbox = re.search(
        r'\bviewBox\s*=\s*["\']\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)\s*["\']',
        svg,
        re.IGNORECASE,
    )
    width_match = re.search(r'\bwidth\s*=\s*["\']([\d.]+)', svg, re.IGNORECASE)
    height_match = re.search(r'\bheight\s*=\s*["\']([\d.]+)', svg, re.IGNORECASE)
    width = int(float(width_match.group(1))) if width_match else 0
    height = int(float(height_match.group(1))) if height_match else 0
    if viewbox and (not width or not height):
        width, height = int(float(viewbox.group(1))), int(float(viewbox.group(2)))
    if width <= 0 or height <= 0 or width * height > int(
        os.getenv("PJ_IMAGE_MAX_PIXELS", "40000000")
    ):
        raise ImageOpsError("invalid_dimensions", "SVG requires bounded positive dimensions")
    return svg.strip().encode("utf-8"), width, height


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _public_asset(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    artifact = docops.resolve_export_artifact(str(row[1]))
    result = {
        "asset_id": row[0],
        "artifact_id": row[1],
        "parent_asset_id": row[2],
        "operation": row[3],
        "width": row[7],
        "height": row[8],
        "mime_type": row[9],
        "byte_size": row[10],
        "sha256": row[11],
        "quality_status": row[12],
        "metadata": json.loads(row[14] or "{}"),
        "tombstoned": bool(row[15]),
        "created_at": row[16],
    }
    if artifact.get("status") == "ready":
        result["filename"] = artifact["filename"]
        result["download_url"] = artifact["download_url"]
        result["artifact"] = artifact
    return result


def _fetch_asset(asset_id: str, *, include_tombstoned: bool = False) -> tuple[Any, ...]:
    with _db() as conn:
        row = conn.execute(
            "SELECT asset_id, artifact_id, parent_asset_id, operation, prompt_sha256, "
            "provider, provider_asset_id, width, height, mime_type, byte_size, sha256, "
            "quality_status, idempotency_key, metadata_json, tombstoned_at, created_at "
            "FROM imageops_assets WHERE asset_id=?",
            (str(asset_id or "").strip(),),
        ).fetchone()
    if not row or (row[15] and not include_tombstoned):
        raise ImageOpsError("asset_not_found", "image asset was not found")
    return row


def _store_asset(
    data: bytes,
    *,
    extension: str,
    mime_type: str,
    width: int,
    height: int,
    operation: str,
    prompt: str | None = None,
    parent_asset_id: str | None = None,
    provider: str | None = None,
    provider_asset_id: str | None = None,
    idempotency_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = str(idempotency_key or "").strip() or None
    sha = hashlib.sha256(data).hexdigest()
    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest() if prompt else None
    if key:
        with _db() as conn:
            existing = conn.execute(
                "SELECT asset_id, operation, prompt_sha256, parent_asset_id, "
                "sha256, tombstoned_at FROM imageops_assets WHERE idempotency_key=?",
                (key,),
            ).fetchone()
        if existing:
            if existing[1:5] != (operation, prompt_sha, parent_asset_id, sha):
                raise ImageOpsError(
                    "idempotency_conflict",
                    "idempotency key was already used for a different image request",
                )
            if existing[5]:
                raise ImageOpsError("asset_tombstoned", "idempotent image asset was deleted")
            return get_image_asset(existing[0])
    asset_id = "IMG-" + hashlib.sha256(
        b"\0".join(
            (
                operation.encode(),
                sha.encode(),
                str(parent_asset_id or "").encode(),
            )
        )
    ).hexdigest()[:32]
    with _db() as conn:
        existing_asset = conn.execute(
            "SELECT tombstoned_at FROM imageops_assets WHERE asset_id=?",
            (asset_id,),
        ).fetchone()
    if existing_asset:
        if existing_asset[0]:
            raise ImageOpsError("asset_tombstoned", "matching image asset was deleted")
        return get_image_asset(asset_id)
    target = ASSET_ROOT / sha[:2] / f"{asset_id}.{extension}"
    _atomic_write(target, data)
    if target.stat().st_size != len(data) or hashlib.sha256(target.read_bytes()).hexdigest() != sha:
        raise ImageOpsError("asset_integrity_failed", "stored image failed integrity validation")
    artifact = docops.register_external_artifact(
        asset_id,
        1,
        extension,
        target,
        audience_ready=True,
    )
    now = _now()
    values = (
        asset_id,
        artifact["artifact_id"],
        parent_asset_id,
        operation,
        prompt_sha,
        provider,
        provider_asset_id,
        width,
        height,
        mime_type,
        len(data),
        sha,
        "validated",
        key,
        json.dumps(_safe_metadata(metadata), sort_keys=True),
        now,
    )
    with _db() as conn:
        try:
            conn.execute(
                "INSERT INTO imageops_assets "
                "(asset_id, artifact_id, parent_asset_id, operation, prompt_sha256, "
                "provider, provider_asset_id, width, height, mime_type, byte_size, "
                "sha256, quality_status, idempotency_key, metadata_json, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )
            conn.commit()
        except sqlite3.IntegrityError:
            if not key:
                raise
            existing = conn.execute(
                "SELECT asset_id FROM imageops_assets WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if not existing:
                raise
            return get_image_asset(existing[0])
    return get_image_asset(asset_id)


def _store_asset_idempotently(
    data: bytes,
    **kwargs: Any,
) -> dict[str, Any]:
    key = str(kwargs.get("idempotency_key") or "").strip()
    if not key:
        return _store_asset(data, **kwargs)
    if len(key) > 200:
        raise ImageOpsError(
            "invalid_idempotency_key",
            "idempotency key must contain at most 200 characters",
        )
    prompt = kwargs.get("prompt")
    request_sha = _canonical_sha(
        {
            "operation": kwargs.get("operation"),
            "prompt_sha256": (
                hashlib.sha256(prompt.encode()).hexdigest()
                if isinstance(prompt, str) and prompt
                else None
            ),
            "parent_asset_id": kwargs.get("parent_asset_id"),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    )
    claim = _claim_idempotency(key, request_sha)
    if claim["state"] == "ready":
        return get_image_asset(claim["asset_id"])
    if claim["state"] != "claimed":
        raise ImageOpsError(
            "idempotency_state_error",
            "image asset request could not be claimed",
        )
    try:
        _mark_storage_started(key, request_sha)
        result = _store_asset(data, **kwargs)
        _complete_idempotency(key, request_sha, result["asset_id"])
        return result
    except Exception:
        _release_idempotency(key, request_sha)
        raise


def register_image_bytes(
    data: bytes,
    *,
    operation: str = "import",
    prompt: str | None = None,
    parent_asset_id: str | None = None,
    provider: str | None = None,
    provider_asset_id: str | None = None,
    idempotency_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extension, mime_type, width, height = _validate_raster(data)
    return _store_asset_idempotently(
        data,
        extension=extension,
        mime_type=mime_type,
        width=width,
        height=height,
        operation=operation,
        prompt=prompt,
        parent_asset_id=parent_asset_id,
        provider=provider,
        provider_asset_id=provider_asset_id,
        idempotency_key=idempotency_key,
        metadata=metadata,
    )


def register_controlled_svg(
    svg: str,
    *,
    idempotency_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data, width, height = _validate_svg(svg)
    return _store_asset_idempotently(
        data,
        extension="svg",
        mime_type="image/svg+xml",
        width=width,
        height=height,
        operation="controlled_svg",
        idempotency_key=idempotency_key,
        metadata=metadata,
    )


def create_controlled_svg(
    *,
    width: int,
    height: int,
    background: str = "#101826",
    foreground: str = "#f5f7fa",
    title: str = "",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    width, height = int(width), int(height)
    color = re.compile(r"^#[0-9A-Fa-f]{6}$")
    if width <= 0 or height <= 0 or width * height > int(
        os.getenv("PJ_IMAGE_MAX_PIXELS", "40000000")
    ):
        raise ImageOpsError("invalid_dimensions", "SVG dimensions exceed configured limits")
    if not color.fullmatch(background) or not color.fullmatch(foreground):
        raise ImageOpsError("invalid_color", "colors must be six-digit hexadecimal values")
    label = html.escape(str(title or "")[:120])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}"><rect x="0" y="0" width="{width}" '
        f'height="{height}" fill="{background}"/>'
    )
    if label:
        # Text is converted into a simple decorative bar; arbitrary SVG text is not accepted.
        bar_width = max(24, min(width - 32, len(label) * 8))
        svg += (
            f'<rect x="16" y="{max(16, height // 2 - 8)}" width="{bar_width}" '
            f'height="16" rx="8" fill="{foreground}"/>'
        )
    svg += "</svg>"
    return register_controlled_svg(
        svg,
        idempotency_key=idempotency_key,
        metadata={"title": str(title or "")[:120]},
    )


def _generation_enabled() -> bool:
    return os.getenv("PJ_IMAGE_GENERATION_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


@dataclass(frozen=True)
class ProviderImage:
    data: bytes
    provider_asset_id: str | None = None


class ImageProvider(Protocol):
    name: str

    def generate(self, prompt: str, *, size: str, quality: str) -> ProviderImage:
        ...

    def edit(
        self,
        image: bytes,
        prompt: str,
        *,
        size: str,
        quality: str,
    ) -> ProviderImage:
        ...

    def variation(
        self,
        image: bytes,
        *,
        size: str,
    ) -> ProviderImage:
        ...


class OpenAIImageProvider:
    name = "openai"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _json_request(self, path: str, payload: dict[str, Any]) -> ProviderImage:
        request = urllib.request.Request(
            f"https://api.openai.com/v1/images/{path}",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=float(os.getenv("PJ_IMAGE_TIMEOUT_SECONDS", "120")),
            ) as response:
                result = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ImageOpsError(
                "provider_error", "image provider request failed"
            ) from exc
        items = result.get("data") if isinstance(result, dict) else None
        item = items[0] if isinstance(items, list) and items else None
        encoded = item.get("b64_json") if isinstance(item, dict) else None
        if not isinstance(encoded, str):
            raise ImageOpsError(
                "provider_contract_error",
                "image provider returned no image bytes",
            )
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ImageOpsError(
                "provider_contract_error", "provider image data is invalid"
            ) from exc
        provider_asset_id = (
            str(item.get("id")).strip()
            if isinstance(item, dict) and item.get("id")
            else None
        )
        return ProviderImage(data=data, provider_asset_id=provider_asset_id)

    def generate(self, prompt: str, *, size: str, quality: str) -> ProviderImage:
        return self._json_request(
            "generations",
            {
                "model": os.getenv("PJ_IMAGE_MODEL", "gpt-image-1"),
                "prompt": prompt,
                "size": size,
                "quality": quality,
                "n": 1,
                "output_format": "png",
            },
        )

    def edit(
        self,
        image: bytes,
        prompt: str,
        *,
        size: str,
        quality: str,
    ) -> ProviderImage:
        raise ImageOpsError(
            "edit_adapter_unavailable",
            "the configured OpenAI adapter does not yet support binary edits",
        )

    def variation(
        self,
        image: bytes,
        *,
        size: str,
    ) -> ProviderImage:
        raise ImageOpsError(
            "variation_adapter_unavailable",
            "the configured OpenAI adapter does not yet support binary variations",
        )


def _provider() -> ImageProvider:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ImageOpsError(
            "provider_unconfigured", "OpenAI image generation is not configured"
        )
    return OpenAIImageProvider(api_key)


def _require_paid_operation(idempotency_key: str) -> float:
    if not _generation_enabled():
        raise ImageOpsError("generation_disabled", "paid image generation is disabled")
    if not str(idempotency_key or "").strip():
        raise ImageOpsError("idempotency_required", "an idempotency key is required")
    return _reserve_budget(idempotency_key)


def _asset_bytes(asset_id: str) -> tuple[tuple[Any, ...], bytes]:
    row = _fetch_asset(asset_id)
    artifact = docops.resolve_export_artifact(row[1], include_path=True)
    if artifact.get("status") != "ready":
        raise ImageOpsError(
            "asset_integrity_failed", "source image artifact is unavailable"
        )
    data = Path(artifact["path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != row[11]:
        raise ImageOpsError(
            "asset_integrity_failed", "source image artifact failed checksum validation"
        )
    return row, data


def _run_provider_operation(
    *,
    operation: str,
    prompt: str | None,
    parent_asset_id: str | None,
    size: str,
    quality: str | None,
    idempotency_key: str,
    call,
) -> dict[str, Any]:
    idempotency_key = str(idempotency_key or "").strip()
    if not idempotency_key or len(idempotency_key) > 200:
        raise ImageOpsError(
            "idempotency_required",
            "an idempotency key of 1 to 200 characters is required",
        )
    request_sha = _canonical_sha(
        {
            "operation": operation,
            "prompt": prompt,
            "parent_asset_id": parent_asset_id,
            "size": size,
            "quality": quality,
        }
    )
    claim = _claim_idempotency(idempotency_key, request_sha)
    if claim["state"] == "ready":
        return get_image_asset(claim["asset_id"])
    if claim["state"] not in {"claimed", "provider_complete"}:
        raise ImageOpsError(
            "idempotency_state_error", "image request could not be claimed"
        )
    budget_reserved = False
    provider_result_staged = claim["state"] == "provider_complete"
    staging_path = None
    try:
        if provider_result_staged:
            staging_path, provider_image = _load_staged_provider_result(
                idempotency_key,
                payload_sha256=claim["payload_sha256"],
                provider_asset_id=claim["provider_asset_id"],
            )
            provider_name = claim["provider_name"] or "unknown"
        else:
            _require_paid_operation(idempotency_key)
            budget_reserved = True
            provider = _provider()
            provider_image = call(provider)
            provider_name = provider.name
            staging_path = _stage_provider_result(
                idempotency_key,
                request_sha,
                provider_name,
                provider_image,
            )
            provider_result_staged = True
        result = register_image_bytes(
            provider_image.data,
            operation=operation,
            prompt=prompt,
            parent_asset_id=parent_asset_id,
            provider=provider_name,
            provider_asset_id=provider_image.provider_asset_id,
            metadata={
                "size": size,
                **({"quality": quality} if quality else {}),
            },
        )
        _complete_idempotency(
            idempotency_key, request_sha, result["asset_id"]
        )
        _mark_budget(idempotency_key, "committed")
        if staging_path:
            staging_path.unlink(missing_ok=True)
        return result
    except Exception:
        if provider_result_staged:
            _mark_provider_result_retryable(idempotency_key, request_sha)
        elif budget_reserved:
            _mark_budget(idempotency_key, "failed")
            _release_idempotency(idempotency_key, request_sha)
        else:
            _release_idempotency(idempotency_key, request_sha)
        raise


def _validate_provider_options(
    size: str,
    quality: str | None = None,
) -> None:
    if size not in {"1024x1024", "1536x1024", "1024x1536"}:
        raise ImageOpsError("invalid_size", "unsupported image size")
    if quality is not None and quality not in {"low", "medium", "high"}:
        raise ImageOpsError("invalid_quality", "unsupported image quality")


def generate_image(
    prompt: str,
    *,
    size: str = "1024x1024",
    quality: str = "medium",
    idempotency_key: str,
) -> dict[str, Any]:
    prompt = str(prompt or "").strip()
    if not prompt or len(prompt) > 8000:
        raise ImageOpsError("invalid_prompt", "prompt must contain 1 to 8000 characters")
    _validate_provider_options(size, quality)
    return _run_provider_operation(
        operation="generate",
        prompt=prompt,
        parent_asset_id=None,
        size=size,
        quality=quality,
        idempotency_key=idempotency_key,
        call=lambda provider: provider.generate(
            prompt, size=size, quality=quality
        ),
    )


def edit_image(
    asset_id: str,
    prompt: str,
    *,
    size: str = "1024x1024",
    quality: str = "medium",
    idempotency_key: str,
) -> dict[str, Any]:
    prompt = str(prompt or "").strip()
    if not prompt or len(prompt) > 8000:
        raise ImageOpsError("invalid_prompt", "prompt must contain 1 to 8000 characters")
    _validate_provider_options(size, quality)
    row, data = _asset_bytes(asset_id)
    if row[9] == "image/svg+xml":
        raise ImageOpsError("unsupported_edit_source", "SVG assets cannot be edited")
    return _run_provider_operation(
        operation="edit",
        prompt=prompt,
        parent_asset_id=asset_id,
        size=size,
        quality=quality,
        idempotency_key=idempotency_key,
        call=lambda provider: provider.edit(
            data, prompt, size=size, quality=quality
        ),
    )


def create_image_variation(
    asset_id: str,
    *,
    size: str = "1024x1024",
    idempotency_key: str,
) -> dict[str, Any]:
    _validate_provider_options(size)
    row, data = _asset_bytes(asset_id)
    if row[9] == "image/svg+xml":
        raise ImageOpsError("unsupported_variation_source", "SVG assets cannot be varied")
    return _run_provider_operation(
        operation="variation",
        prompt=None,
        parent_asset_id=asset_id,
        size=size,
        quality=None,
        idempotency_key=idempotency_key,
        call=lambda provider: provider.variation(data, size=size),
    )


def get_image_asset(asset_id: str) -> dict[str, Any]:
    return _public_asset(_fetch_asset(asset_id))


def delete_image_asset(asset_id: str) -> dict[str, Any]:
    row = _fetch_asset(asset_id)
    tombstoned_at = _now()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not docops.tombstone_export_artifact(
            row[1], connection=conn
        ):
            raise ImageOpsError(
                "artifact_tombstone_failed",
                "image artifact could not be tombstoned",
            )
        conn.execute(
            "UPDATE imageops_assets SET tombstoned_at=COALESCE(tombstoned_at, ?) "
            "WHERE asset_id=?",
            (tombstoned_at, asset_id),
        )
        conn.commit()
    return {"status": "tombstoned", "asset_id": asset_id, "tombstoned_at": tombstoned_at}


def record_image_feedback(
    asset_id: str,
    rating: int,
    *,
    comments: str = "",
) -> dict[str, Any]:
    _fetch_asset(asset_id)
    rating = int(rating)
    if rating < 1 or rating > 5:
        raise ImageOpsError("invalid_rating", "rating must be between 1 and 5")
    with _db() as conn:
        conn.execute(
            "INSERT INTO imageops_feedback (asset_id, rating, comments, created_at) "
            "VALUES (?,?,?,?)",
            (asset_id, rating, str(comments or "")[:2000], _now()),
        )
        conn.commit()
    return {"status": "recorded", "asset_id": asset_id, "rating": rating}


def get_image_capability_status() -> dict[str, Any]:
    manifest = os.getenv("PJ_IMAGE_TRAINING_MANIFEST", "").strip()
    training_status = "training_unavailable"
    training_reason = "canonical 29-chunk manifest and checksums have not been supplied"
    package_status = "missing"
    package_sha256 = None
    if manifest and Path(manifest).is_file():
        try:
            inspected = inspect_training_package(manifest)
            package_sha256 = inspected["manifest_sha256"]
            approved = os.getenv(
                "PJ_IMAGE_APPROVED_PACKAGE_SHA256", ""
            ).strip()
            if approved == package_sha256:
                package_status = "approved"
                training_reason = (
                    "canonical package is approved but hosted-vector ingestion "
                    "has not been verified"
                )
            else:
                package_status = "verified_unapproved"
                training_reason = (
                    "canonical package passed local inspection but its digest "
                    "is not approved"
                )
        except ImageOpsError as exc:
            package_status = "invalid"
            training_reason = str(exc)
    with _db() as conn:
        counts = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN tombstoned_at IS NULL THEN 1 ELSE 0 END) "
            "FROM imageops_assets"
        ).fetchone()
        budget_used = float(conn.execute(
            "SELECT COALESCE(SUM(estimated_usd), 0) "
            "FROM imageops_budget_reservations "
            "WHERE status IN ('reserved','committed')"
        ).fetchone()[0])
    enabled = _generation_enabled()
    budget, estimate = _budget_settings()
    budget_available = estimate > 0 and budget_used + estimate <= budget
    provider_configured = bool(os.getenv("OPENAI_API_KEY"))
    generation_active = enabled and budget_available and provider_configured
    return {
        "status": "active" if generation_active else "degraded",
        "generation": (
            "active" if generation_active
            else "disabled" if not enabled
            else "budget_blocked" if not budget_available
            else "provider_unconfigured"
        ),
        "operations": {
            "generate": "active" if generation_active else "unavailable",
            "edit": "adapter_unavailable",
            "variation": "adapter_unavailable",
            "controlled_svg": "active",
            "import": "active",
        },
        "controlled_svg": "active",
        "training": training_status,
        "training_reason": training_reason,
        "package_status": package_status,
        "package_sha256": package_sha256,
        "asset_count": int(counts[0] or 0),
        "active_asset_count": int(counts[1] or 0),
        "paid_calls_default": "disabled",
        "budget": {
            "approved_usd": budget,
            "reserved_or_committed_usd": budget_used,
            "estimated_call_usd": estimate,
            "next_call_available": budget_available,
        },
        "canonical_package_required": True,
    }


def inspect_training_package(manifest_path: str) -> dict[str, Any]:
    path = Path(str(manifest_path or "")).expanduser()
    if path.is_symlink() or not path.is_file():
        raise ImageOpsError("manifest_missing", "canonical image training manifest is missing")
    if path.stat().st_size > 1024 * 1024:
        raise ImageOpsError(
            "manifest_invalid",
            "image training manifest exceeds the 1 MiB safety limit",
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImageOpsError("manifest_invalid", "image training manifest is not valid JSON") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "1.0"
        or not isinstance(manifest.get("package_version"), str)
        or not manifest["package_version"].strip()
    ):
        raise ImageOpsError(
            "manifest_invalid",
            "manifest requires schema_version 1.0 and a package_version",
        )
    chunks = manifest.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != 29:
        raise ImageOpsError("manifest_incomplete", "canonical package must declare exactly 29 chunks")
    verified: list[dict[str, Any]] = []
    seen_names = set()
    seen_hashes = set()
    total_bytes = 0
    try:
        max_chunk_bytes = int(
            os.getenv(
                "PJ_IMAGE_MAX_TRAINING_CHUNK_BYTES",
                str(10 * 1024 * 1024),
            )
        )
        max_package_bytes = int(
            os.getenv(
                "PJ_IMAGE_MAX_TRAINING_PACKAGE_BYTES",
                str(100 * 1024 * 1024),
            )
        )
    except ValueError as exc:
        raise ImageOpsError(
            "image_config_invalid",
            "image training package byte limits must be integers",
        ) from exc
    if max_chunk_bytes <= 0 or max_package_bytes <= 0:
        raise ImageOpsError(
            "image_config_invalid",
            "image training package byte limits must be positive",
        )
    for item in chunks:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("filename"), str)
            or not item["filename"]
            or Path(item["filename"]).name != item["filename"]
            or item["filename"] in seen_names
            or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", "")))
            or isinstance(item.get("byte_size"), bool)
            or not isinstance(item.get("byte_size"), int)
            or item["byte_size"] < 1
            or item["byte_size"] > max_chunk_bytes
            or item["sha256"] in seen_hashes
        ):
            raise ImageOpsError(
                "manifest_invalid",
                "every chunk needs a unique basename, byte size, and SHA-256",
            )
        seen_names.add(item["filename"])
        seen_hashes.add(item["sha256"])
        total_bytes += item["byte_size"]
        if total_bytes > max_package_bytes:
            raise ImageOpsError(
                "manifest_invalid",
                "declared package bytes exceed the configured safety limit",
            )
        candidate = path.parent / str(item["filename"])
        if candidate.is_symlink():
            raise ImageOpsError(
                "manifest_invalid",
                f"package chunk must not be a symlink: {item['filename']}",
            )
        chunk = candidate.resolve()
        try:
            chunk.relative_to(path.parent.resolve())
        except ValueError as exc:
            raise ImageOpsError("manifest_invalid", "chunk path escapes the package root") from exc
        if not chunk.is_file():
            raise ImageOpsError("chunk_missing", f"missing package chunk: {item['filename']}")
        data = chunk.read_bytes()
        if len(data) != item["byte_size"]:
            raise ImageOpsError(
                "chunk_size_mismatch",
                f"chunk byte size mismatch: {item['filename']}",
            )
        if _CREDENTIAL_RE.search(data):
            raise ImageOpsError(
                "chunk_credential_detected",
                f"possible credential detected in chunk: {item['filename']}",
            )
        actual = hashlib.sha256(data).hexdigest()
        if actual != item["sha256"]:
            raise ImageOpsError("chunk_hash_mismatch", f"chunk checksum mismatch: {item['filename']}")
        verified.append({
            "filename": item["filename"],
            "byte_size": len(data),
            "sha256": actual,
        })
    return {
        "status": "verified",
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "package_version": manifest["package_version"].strip(),
        "chunk_count": len(verified),
        "chunks": verified,
    }


def _tool_call(function, *args, **kwargs) -> dict[str, Any]:
    try:
        return function(*args, **kwargs)
    except ImageOpsError as exc:
        return exc.as_dict()


def _tool_generate_image(**kwargs) -> dict[str, Any]:
    return _tool_call(generate_image, **kwargs)


def _tool_edit_image(**kwargs) -> dict[str, Any]:
    return _tool_call(edit_image, **kwargs)


def _tool_create_image_variation(**kwargs) -> dict[str, Any]:
    return _tool_call(create_image_variation, **kwargs)


def _tool_create_controlled_svg(**kwargs) -> dict[str, Any]:
    return _tool_call(create_controlled_svg, **kwargs)


def _tool_register_vector_image(**kwargs) -> dict[str, Any]:
    return _tool_call(register_controlled_svg, **kwargs)


def _tool_get_image_asset(**kwargs) -> dict[str, Any]:
    return _tool_call(get_image_asset, **kwargs)


def _tool_delete_image_asset(**kwargs) -> dict[str, Any]:
    return _tool_call(delete_image_asset, **kwargs)


def _tool_record_image_feedback(**kwargs) -> dict[str, Any]:
    return _tool_call(record_image_feedback, **kwargs)


IMAGEOPS_SCHEMAS = [
    {
        "type": "function",
        "name": "generate_image_asset",
        "description": (
            "Generate a governed downloadable image. Paid calls are disabled "
            "unless explicitly enabled, budget-approved, and idempotency-bound."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1536x1024", "1024x1536"],
                },
                "quality": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                },
                "idempotency_key": {"type": "string"},
            },
            "required": ["prompt", "idempotency_key"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "edit_image_asset",
        "description": "Create a governed edit of an existing PJ image asset.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "prompt": {"type": "string"},
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1536x1024", "1024x1536"],
                },
                "quality": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                },
                "idempotency_key": {"type": "string"},
            },
            "required": ["asset_id", "prompt", "idempotency_key"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "create_image_variation",
        "description": "Create a governed variation of an existing PJ image asset.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1536x1024", "1024x1536"],
                },
                "idempotency_key": {"type": "string"},
            },
            "required": ["asset_id", "idempotency_key"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "create_controlled_image",
        "description": (
            "Create a deterministic, active-content-free SVG graphic and "
            "register it as a downloadable governed asset."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "width": {"type": "integer", "minimum": 1, "maximum": 4096},
                "height": {"type": "integer", "minimum": 1, "maximum": 4096},
                "background": {"type": "string"},
                "foreground": {"type": "string"},
                "title": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["width", "height"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "register_vector_image",
        "description": (
            "Validate a strict allowlisted SVG with no scripts, links, event "
            "handlers, external resources, or embedded data, then register it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "svg": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["svg"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_image_asset",
        "description": "Resolve an image asset and its authenticated download metadata.",
        "parameters": {
            "type": "object",
            "properties": {"asset_id": {"type": "string"}},
            "required": ["asset_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "delete_image_asset",
        "description": "Tombstone a governed image asset while preserving its audit lineage.",
        "parameters": {
            "type": "object",
            "properties": {"asset_id": {"type": "string"}},
            "required": ["asset_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "record_image_feedback",
        "description": "Record bounded quality feedback for a governed image asset.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "rating": {"type": "integer", "minimum": 1, "maximum": 5},
                "comments": {"type": "string"},
            },
            "required": ["asset_id", "rating"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_image_capability_status",
        "description": (
            "Report image generation, controlled-vector, asset, and canonical "
            "training-package readiness without exposing secrets."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
]

IMAGEOPS_DISPATCH = {
    "generate_image_asset": _tool_generate_image,
    "edit_image_asset": _tool_edit_image,
    "create_image_variation": _tool_create_image_variation,
    "create_controlled_image": _tool_create_controlled_svg,
    "register_vector_image": _tool_register_vector_image,
    "get_image_asset": _tool_get_image_asset,
    "delete_image_asset": _tool_delete_image_asset,
    "record_image_feedback": _tool_record_image_feedback,
    "get_image_capability_status": get_image_capability_status,
}
