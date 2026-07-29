"""Evidence-first workflows built exclusively on normalized connectors."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ops.connectors import Connector, ConnectorError, DraftExecutor

from .models import ActionItem, Decision, DraftPreview, ProposalState, SourceRef, Statement


SENSITIVE_OPERATIONS = {
    "send_mail",
    "send_invitation",
    "change_event",
    "change_attendees",
    "publish_commitment",
}


def normalize_time(value: datetime, timezone: str) -> datetime:
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown time zone: {timezone}") from exc
    if value.tzinfo is None:
        raise ValueError("ambiguous local date/time requires an explicit UTC offset")
    return value.astimezone(zone)


class ProductivityService:
    def __init__(self, connectors: dict[str, Connector]) -> None:
        self._connectors = connectors
        self._drafts: dict[str, tuple[DraftPreview, dict[str, object], str]] = {}
        self._executed: dict[str, object] = {}

    def _records(self, kinds: set[str], since: datetime, until: datetime):
        output = []
        for connector in self._connectors.values():
            try:
                output.extend(connector.records(kinds=kinds, since=since, until=until))
            except Exception as exc:
                raise ConnectorError("approved source unavailable") from exc
        return sorted(output, key=lambda item: item.source_timestamp)

    def daily_agenda(self, since: datetime, until: datetime) -> list[Statement]:
        return [
            Statement(
                str(r.payload.get("title", "Untitled event")),
                SourceRef(r.connector, r.record_id, r.source_timestamp),
            )
            for r in self._records({"event"}, since, until)
        ]

    def meeting_brief(self, event_id: str, *, now: datetime) -> list[Statement]:
        event = next(
            (c.get(event_id) for c in self._connectors.values() if _can_get(c, event_id)), None
        )
        if event is None:
            raise KeyError(event_id)
        statements = [
            Statement(
                str(event.payload.get("title", "Meeting")),
                SourceRef(event.connector, event.record_id, event.source_timestamp),
            )
        ]
        thread_ids = set(event.payload.get("thread_ids", []))
        for record in self._records({"message"}, event.source_timestamp, now):
            if record.payload.get("thread_id") in thread_ids:
                statements.append(
                    Statement(
                        str(record.payload.get("summary", record.payload.get("body", ""))),
                        SourceRef(record.connector, record.record_id, record.source_timestamp),
                    )
                )
        return statements

    def thread_summary(self, thread_id: str, since: datetime, until: datetime) -> list[Statement]:
        return [
            Statement(
                str(r.payload.get("summary", r.payload.get("body", ""))),
                SourceRef(r.connector, r.record_id, r.source_timestamp),
            )
            for r in self._records({"message"}, since, until)
            if r.payload.get("thread_id") == thread_id
        ]

    def follow_up_suggestions(
        self, thread_id: str, since: datetime, until: datetime
    ) -> list[Statement]:
        return [
            replace(s, text=f"Follow up: {s.text}")
            for s in self.thread_summary(thread_id, since, until)
            if s.text
        ]

    def commitment_proposals(self, statements: list[Statement]) -> list[ActionItem]:
        return [
            ActionItem(uuid.uuid4().hex, statement)
            for statement in statements
            if any(word in statement.text.lower() for word in ("will ", "by ", "agree", "action:"))
        ]

    def decision_proposals(self, statements: list[Statement]) -> list[Decision]:
        return [
            Decision(uuid.uuid4().hex, statement)
            for statement in statements
            if any(word in statement.text.lower() for word in ("decided", "decision:", "agreed"))
        ]

    @staticmethod
    def confirm(proposal: ActionItem | Decision):
        return replace(proposal, state=ProposalState.CONFIRMED)

    def conflicts(
        self, start: datetime, end: datetime, timezone: str, calendar_ids: set[str]
    ) -> list[Statement]:
        start, end = normalize_time(start, timezone), normalize_time(end, timezone)
        conflicts = []
        for r in self._records({"event"}, start.astimezone(UTC), end.astimezone(UTC)):
            if r.payload.get("calendar_id") not in calendar_ids:
                continue
            other_start = datetime.fromisoformat(str(r.payload["start"]))
            other_end = datetime.fromisoformat(str(r.payload["end"]))
            if other_start < end and other_end > start:
                conflicts.append(
                    Statement(
                        str(r.payload.get("title", "Busy")),
                        SourceRef(r.connector, r.record_id, r.source_timestamp),
                    )
                )
        return conflicts

    def create_draft(
        self, operation: str, payload: dict[str, object], *, ambiguous_date: bool = False
    ) -> DraftPreview:
        if operation not in SENSITIVE_OPERATIONS | {"draft_mail", "draft_event"}:
            raise ValueError("unsupported productivity operation")
        recurrence = payload.get("recurrence")
        scope = payload.get("recurring_scope")
        if recurrence and scope not in {"this", "following", "series"}:
            raise ValueError("recurring event changes require an explicit scope")
        if ambiguous_date:
            raise ValueError("ambiguous date requires owner clarification")
        draft_id = uuid.uuid4().hex
        preview = DraftPreview(
            draft_id,
            operation,
            str(payload.get("sender", "")),
            tuple(str(x) for x in payload.get("recipients", ())),
            str(payload["calendar_id"]) if payload.get("calendar_id") else None,
            str(payload.get("timezone", "UTC")),
            str(payload.get("subject", "")),
            str(payload.get("body_summary", "")),
            tuple(str(x) for x in payload.get("attendee_effects", ())),
            bool(payload.get("reversible", False)),
            dict(payload.get("expected_versions", {})),
            recurring_scope=str(scope) if scope else None,
        )
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        self._drafts[draft_id] = (preview, payload, digest)
        return preview

    def execute_draft(
        self, draft_id: str, *, approved: bool, executor: DraftExecutor, idempotency_key: str
    ):
        if not approved:
            raise PermissionError("explicit owner approval is required")
        if idempotency_key in self._executed:
            return self._executed[idempotency_key]
        preview, payload, _ = self._drafts[draft_id]
        for record_id, expected in preview.expected_versions.items():
            if executor.get(record_id).etag != expected:
                raise RuntimeError("external state changed after preview")
        result = executor.execute(preview.operation, payload, idempotency_key=idempotency_key)
        self._executed[idempotency_key] = result
        return result


def _can_get(connector: Connector, record_id: str) -> bool:
    try:
        connector.get(record_id)
    except (KeyError, ConnectorError):
        return False
    return True


def within_quiet_hours(now: datetime, start: time, end: time) -> bool:
    local = now.timetz().replace(tzinfo=None)
    return start <= local < end if start < end else local >= start or local < end
