"""Content-addressed retained media and metadata-only ephemeral receipts."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from .models import MediaReference


class RetentionMode(StrEnum):
    EPHEMERAL = "ephemeral"
    RETAIN = "retain"


@dataclass(frozen=True, slots=True)
class CaptureReceipt:
    receipt_id: str
    sha256: str
    byte_size: int
    media_type: str
    retention: RetentionMode
    artifact_id: str | None


class MediaStore:
    """Reuse media by digest; never retain bytes without explicit election."""

    def __init__(self, root: Path):
        self.root = root
        self.artifacts = root / "artifacts"
        self.receipts = root / "receipts"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.receipts.mkdir(parents=True, exist_ok=True)

    def ingest(self, data: bytes, media_type: str, retention: RetentionMode) -> CaptureReceipt:
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = f"MEDIA-{digest}" if retention == RetentionMode.RETAIN else None
        if artifact_id:
            destination = self.artifacts / digest
            if not destination.exists():
                temporary = destination.with_suffix(f".{uuid4().hex}.tmp")
                temporary.write_bytes(data)
                os.replace(temporary, destination)
        receipt = CaptureReceipt(
            receipt_id=f"MRC-{uuid4().hex}",
            sha256=digest,
            byte_size=len(data),
            media_type=media_type,
            retention=retention,
            artifact_id=artifact_id,
        )
        # Deliberately metadata-only: never serialize frame data or extracted text.
        (self.receipts / f"{receipt.receipt_id}.json").write_text(
            json.dumps(
                {
                    "receipt_id": receipt.receipt_id,
                    "sha256": digest,
                    "byte_size": len(data),
                    "media_type": media_type,
                    "retention": retention.value,
                    "artifact_id": artifact_id,
                },
                sort_keys=True,
            )
        )
        return receipt

    def reference(self, receipt: CaptureReceipt, *, width: int, height: int) -> MediaReference:
        if receipt.artifact_id is None:
            raise ValueError("ephemeral media has no reusable artifact reference")
        return MediaReference(
            artifact_id=receipt.artifact_id,
            sha256=receipt.sha256,
            media_type=receipt.media_type,
            byte_size=receipt.byte_size,
            width=width,
            height=height,
        )
