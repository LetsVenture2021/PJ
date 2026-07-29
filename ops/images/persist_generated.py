"""Persist hosted image_generation outputs as durable, downloadable uploads.

Base64 image results otherwise exist only inside the response payload and are
lost when the conversation scrolls away. Persistence is best-effort and never
fails a turn.
"""

from __future__ import annotations

import base64
import secrets
from pathlib import PurePosixPath

MAX_IMAGE_BYTES = 50 * 1024 * 1024


def _get(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def persist_generated_images(response) -> list:
    saved_records = []
    try:
        from ops.docs import uploads as document_uploads
        from ops.shared.io import sha256_file

        items = [
            item
            for item in (_get(response, "output") or [])
            if _get(item, "type") == "image_generation_call" and _get(item, "result")
        ]
        if not items:
            return []
        session_id = f"genimg_{secrets.token_hex(8)}"
        upload_id = f"UPL-{secrets.token_hex(16)}"
        target_dir = document_uploads.UPLOADS_DIR / session_id / upload_id
        files = []
        for index, item in enumerate(items):
            try:
                data = base64.b64decode(_get(item, "result"))
            except Exception:
                continue
            if not data or len(data) > MAX_IMAGE_BYTES:
                continue
            name = f"generated_{index}.png"
            target_dir.mkdir(parents=True, exist_ok=True)
            destination = target_dir / name
            destination.write_bytes(data)
            destination.chmod(0o600)
            files.append(
                {
                    "saved_path": (
                        PurePosixPath("uploads") / session_id / upload_id / name
                    ).as_posix(),
                    "path": destination,
                    "name": name,
                    "mime": "image/png",
                    "size": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                }
            )
        if not files:
            return []
        registered = document_uploads.register_uploaded_documents(upload_id, session_id, files)
        saved_records = [
            {
                "upload_id": upload_id,
                "saved_path": doc["saved_path"],
                "size": doc["size"],
                "revised_prompt": _get(items[0], "revised_prompt") or "",
            }
            for doc in registered["documents"]
        ]
    except Exception:
        return []
    return saved_records
