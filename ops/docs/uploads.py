"""Managed upload registry for source documents used by DocOps."""

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ops.docs import extraction, formats, service
from ops.shared.io import sha256_file


UPLOADS_DIR = service.DOCS_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)


@contextmanager
def _db():
    conn = sqlite3.connect(service._DB_PATH)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS docops_uploads (
                upload_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (upload_id, relative_path)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_docops_uploads_session "
            "ON docops_uploads(session_id, created_at)"
        )
        yield conn
        conn.commit()
    finally:
        conn.close()


def _public_upload(row) -> dict:
    return {
        "upload_id": row[0],
        "session_id": row[1],
        "saved_path": row[2],
        "name": row[3],
        "mime": row[5],
        "size": row[6],
        "sha256": row[7],
        "created_at": row[8],
    }


def register_uploaded_documents(upload_id: str, session_id: str, files: list[dict]) -> dict:
    """Register an already-persisted upload batch in the DocOps index."""
    if not re.fullmatch(r"UPL-[a-f0-9]{32}", upload_id or ""):
        raise ValueError("invalid upload_id")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", session_id or ""):
        raise ValueError("invalid session_id")
    if not files:
        raise ValueError("at least one uploaded file is required")

    upload_root = UPLOADS_DIR.resolve()
    rows = []
    seen = set()
    for item in files:
        relative_path = str(item.get("saved_path") or "")
        path = Path(item.get("path") or "")
        if not relative_path or relative_path in seen:
            raise ValueError("uploaded paths must be present and unique")
        seen.add(relative_path)
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(upload_root)
        except (OSError, ValueError) as exc:
            raise ValueError("uploaded file is outside the managed upload directory") from exc
        if path.is_symlink() or not resolved.is_file():
            raise ValueError("uploaded file must be a regular file")
        byte_size = resolved.stat().st_size
        sha256 = sha256_file(resolved)
        if byte_size != item.get("size") or sha256 != item.get("sha256"):
            raise ValueError("uploaded file integrity verification failed")
        rows.append(
            (
                upload_id,
                session_id,
                relative_path,
                str(item.get("name") or resolved.name),
                str(resolved),
                str(item.get("mime") or "application/octet-stream"),
                byte_size,
                sha256,
            )
        )

    with _db() as conn:
        conn.executemany(
            "INSERT INTO docops_uploads "
            "(upload_id, session_id, relative_path, name, path, mime_type, byte_size, sha256) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        registered = conn.execute(
            "SELECT upload_id, session_id, relative_path, name, path, mime_type, "
            "byte_size, sha256, created_at FROM docops_uploads "
            "WHERE upload_id=? ORDER BY relative_path",
            (upload_id,),
        ).fetchall()
    return {
        "upload_id": upload_id,
        "count": len(registered),
        "documents": [_public_upload(row) for row in registered],
    }


def list_uploaded_documents(session_id: str = "", query: str = "", limit: int = 50) -> dict:
    """List uploaded source documents registered with DocOps."""
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        return {"error": "limit must be an integer from 1 to 100"}
    like = f"%{query}%"
    with _db() as conn:
        rows = conn.execute(
            "SELECT upload_id, session_id, relative_path, name, path, mime_type, "
            "byte_size, sha256, created_at FROM docops_uploads "
            "WHERE (? = '' OR session_id = ?) "
            "AND (name LIKE ? OR relative_path LIKE ? OR upload_id LIKE ?) "
            "ORDER BY created_at DESC, relative_path LIMIT ?",
            (session_id, session_id, like, like, like, limit),
        ).fetchall()
    return {"count": len(rows), "documents": [_public_upload(row) for row in rows]}


def get_uploaded_document(upload_id: str, saved_path: str = "") -> dict:
    """Resolve an uploaded source document and preview supported text formats."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT upload_id, session_id, relative_path, name, path, mime_type, "
            "byte_size, sha256, created_at FROM docops_uploads "
            "WHERE upload_id=? AND (? = '' OR relative_path=?) ORDER BY relative_path",
            (upload_id, saved_path, saved_path),
        ).fetchall()
    if not rows:
        return {"error": f"unknown uploaded document '{upload_id}'"}
    if len(rows) > 1:
        return {
            "upload_id": upload_id,
            "count": len(rows),
            "documents": [_public_upload(row) for row in rows],
        }

    row = rows[0]
    metadata = _public_upload(row)
    path = Path(row[4])
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(UPLOADS_DIR.resolve())
    except (OSError, ValueError):
        return {"error": "uploaded document is missing or outside managed storage"}
    if resolved.stat().st_size != row[6] or sha256_file(resolved) != row[7]:
        return {"error": "uploaded document failed integrity verification"}

    with resolved.open("rb") as handle:
        head = handle.read(4096)
    classification = formats.classify(resolved.name, head, row[6])
    metadata["classification"] = classification.public()
    metadata["content"] = None
    metadata["content_truncated"] = False
    if classification.spec.handling in {"extract", "header_only"} and not classification.rejection:
        try:
            preview = extraction.extract_preview(resolved, classification, char_cap=20000)
        except extraction.ExtractionError as exc:
            metadata["content_error"] = exc.code
        else:
            metadata["content"] = preview
            metadata["content_truncated"] = len(preview) >= 20000
    return metadata


def upload_inventory_count() -> int:
    """Return the number of source documents in the upload registry."""
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) FROM docops_uploads").fetchone()[0]


UPLOAD_SCHEMAS = [
    {
        "type": "function",
        "name": "list_uploaded_documents",
        "description": (
            "List user-uploaded source documents registered in DocOps, "
            "optionally filtered by session or keyword."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "get_uploaded_document",
        "description": (
            "Read metadata for an uploaded source document. Text-based formats "
            "also return a bounded content preview."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "upload_id": {"type": "string"},
                "saved_path": {
                    "type": "string",
                    "description": ("Specific saved path when an upload contains multiple files"),
                },
            },
            "required": ["upload_id"],
        },
    },
]

UPLOAD_DISPATCH = {
    "list_uploaded_documents": list_uploaded_documents,
    "get_uploaded_document": get_uploaded_document,
}
