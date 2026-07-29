"""Retrieve hosted-container outputs into the DocOps upload registry.

Hosted shell and Code Interpreter write artifacts to ``/mnt/data`` inside
OpenAI-managed containers, which expire. This tool copies those files into
managed upload storage and registers them like any other upload, so container
outputs get durable, Access-gated download paths and lineage.
"""

from __future__ import annotations

import re
import secrets
from pathlib import PurePosixPath

from ops.docs import uploads as document_uploads
from ops.shared.io import sha256_file

MAX_FILES = 20
MAX_FILE_BYTES = 25 * 1024 * 1024
_CONTAINER_ID = re.compile(r"^cntr_[A-Za-z0-9]{8,64}$")


def fetch_container_artifacts(container_id: str = "", session_id: str = "") -> dict:
    """Copy a hosted container's /mnt/data files into the DocOps upload store."""
    if not _CONTAINER_ID.fullmatch(str(container_id or "")):
        return {"error": "container_id must look like cntr_..."}
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", str(session_id or "")):
        session_id = f"container_{secrets.token_hex(8)}"
    from openai import OpenAI

    client = OpenAI()
    try:
        listed = client.containers.files.list(container_id, limit=MAX_FILES)
    except Exception as exc:
        return {"error": f"container_list_failed: {str(exc)[:200]}"}

    upload_id = f"UPL-{secrets.token_hex(16)}"
    target_dir = document_uploads.UPLOADS_DIR / session_id / upload_id
    saved = []
    skipped = []
    for item in listed.data:
        name = PurePosixPath(str(item.path or item.id)).name or item.id
        if getattr(item, "bytes", 0) and item.bytes > MAX_FILE_BYTES:
            skipped.append({"name": name, "reason": "too_large"})
            continue
        try:
            content = client.containers.files.content(item.id, container_id=container_id)
            data = content.read() if hasattr(content, "read") else bytes(content)
        except Exception as exc:
            skipped.append({"name": name, "reason": f"fetch_failed: {str(exc)[:80]}"})
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / name
        destination.write_bytes(data)
        destination.chmod(0o600)
        saved.append(
            {
                "saved_path": (PurePosixPath("uploads") / session_id / upload_id / name).as_posix(),
                "path": destination,
                "name": name,
                "mime": "application/octet-stream",
                "size": destination.stat().st_size,
                "sha256": sha256_file(destination),
            }
        )
    if not saved:
        return {"error": "no_retrievable_files", "skipped": skipped}
    registered = document_uploads.register_uploaded_documents(upload_id, session_id, saved)
    return {
        "status": "retrieved",
        "container_id": container_id,
        "count": registered["count"],
        "documents": registered["documents"],
        "skipped": skipped,
    }


CONTAINER_ARTIFACT_SCHEMAS = [
    {
        "type": "function",
        "name": "fetch_container_artifacts",
        "description": (
            "After a hosted shell or code interpreter run writes files to "
            "/mnt/data, copy those container files into durable DocOps storage "
            "and register them so the owner can download them. Call this "
            "automatically whenever a container run produces files worth keeping."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "container_id": {"type": "string", "description": "cntr_... id"},
                "session_id": {"type": "string"},
            },
            "required": ["container_id"],
        },
    }
]

CONTAINER_ARTIFACT_DISPATCH = {
    "fetch_container_artifacts": lambda container_id="", session_id="": fetch_container_artifacts(
        container_id, session_id
    )
}
