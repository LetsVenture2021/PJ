"""Offline meeting artifact ingestion; no microphone or live service assumptions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from .models import MeetingArtifact, SourceRef, TranscriptSegment


CONSENT_STATES = {"granted", "denied", "unknown", "not_required"}


def ingest_transcript(
    *,
    artifact_id: str,
    connector: str,
    source_timestamp: datetime,
    segments: list[dict[str, object]],
    consent_status: str,
    recording_source: str,
    retention_policy: str,
) -> MeetingArtifact:
    if consent_status not in CONSENT_STATES:
        raise ValueError("invalid consent status")
    if consent_status in {"denied", "unknown"}:
        raise PermissionError("transcript processing requires resolved consent")
    normalized = []
    for index, raw in enumerate(segments):
        confidence = raw.get("speaker_confidence")
        if confidence is not None and not 0 <= float(confidence) <= 1:
            raise ValueError("speaker confidence must be between zero and one")
        segment_id = str(raw.get("id") or index)
        normalized.append(
            TranscriptSegment(
                segment_id,
                float(raw["start_seconds"]),
                float(raw["end_seconds"]),
                str(raw["text"]),
                str(raw["speaker_label"]) if raw.get("speaker_label") else None,
                float(confidence) if confidence is not None else None,
                SourceRef(connector, artifact_id, source_timestamp, segment_id),
            )
        )
    return MeetingArtifact(
        artifact_id or uuid.uuid4().hex,
        recording_source,
        consent_status,
        retention_policy,
        tuple(normalized),
        datetime.now(UTC),
    )
