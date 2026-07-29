"""Normalized, evidence-preserving productivity models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True)
class SourceRef:
    connector: str
    record_id: str
    source_timestamp: datetime
    segment_id: str | None = None


@dataclass(frozen=True)
class Statement:
    text: str
    source: SourceRef


@dataclass(frozen=True)
class Attendee:
    name: str
    address: str | None
    response: str = "unknown"
    hidden: bool = False


@dataclass(frozen=True)
class Message:
    id: str
    thread_id: str
    sender: Attendee
    recipients: tuple[Attendee, ...]
    sent_at: datetime
    subject: str
    body: str
    source: SourceRef


@dataclass(frozen=True)
class Thread:
    id: str
    subject: str
    messages: tuple[Message, ...]
    source: SourceRef


@dataclass(frozen=True)
class Event:
    id: str
    calendar_id: str
    title: str
    start: datetime
    end: datetime
    timezone: str
    attendees: tuple[Attendee, ...]
    source: SourceRef
    recurrence: str | None = None
    visibility: str = "private"


@dataclass(frozen=True)
class TranscriptSegment:
    id: str
    start_seconds: float
    end_seconds: float
    text: str
    speaker_label: str | None
    speaker_confidence: float | None
    source: SourceRef


class ProposalState(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


@dataclass(frozen=True)
class Decision:
    id: str
    statement: Statement
    state: ProposalState = ProposalState.PROPOSED


@dataclass(frozen=True)
class ActionItem:
    id: str
    statement: Statement
    owner: str | None = None
    due_at: datetime | None = None
    state: ProposalState = ProposalState.PROPOSED


@dataclass(frozen=True)
class MeetingArtifact:
    id: str
    recording_source: str
    consent_status: str
    retention_policy: str
    segments: tuple[TranscriptSegment, ...]
    uploaded_at: datetime


@dataclass(frozen=True)
class DraftPreview:
    id: str
    operation: str
    sender: str
    recipients: tuple[str, ...]
    affected_calendar: str | None
    timezone: str
    subject: str
    body_summary: str
    attendee_effects: tuple[str, ...]
    reversible: bool
    expected_versions: dict[str, str | None] = field(default_factory=dict)
    ambiguous_date: bool = False
    recurring_scope: str | None = None
