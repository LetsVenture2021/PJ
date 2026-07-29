from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from ops.connectors import ConnectorError, ConnectorRecord
from ops.productivity.daily_brief import DailyBriefJob
from ops.productivity.ingestion import ingest_transcript
from ops.productivity.models import ActionItem, ProposalState, SourceRef, Statement
from ops.productivity.service import ProductivityService, normalize_time


NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


class MemoryConnector:
    def __init__(self, records=()):
        self.data = {record.record_id: record for record in records}
        self.executions = 0

    def records(self, *, kinds, since, until):
        return [
            r
            for r in self.data.values()
            if r.kind in kinds and since <= r.source_timestamp <= until
        ]

    def get(self, record_id):
        return self.data[record_id]

    def execute(self, operation, payload, *, idempotency_key):
        self.executions += 1
        return ConnectorRecord("memory", "sent", operation, NOW, payload)


class FailingConnector(MemoryConnector):
    def records(self, **kwargs):
        raise OSError("provider secret that must be normalized")


class ProductivityTests(unittest.TestCase):
    def test_evidence_is_retained_by_read_workflows(self):
        record = ConnectorRecord(
            "mail", "m1", "message", NOW, {"thread_id": "t1", "body": "I will send it by Friday"}
        )
        service = ProductivityService({"mail": MemoryConnector([record])})
        statements = service.thread_summary("t1", NOW - timedelta(days=1), NOW)
        self.assertEqual(statements[0].source.record_id, "m1")
        self.assertEqual(statements[0].source.source_timestamp, NOW)
        proposals = service.commitment_proposals(statements)
        self.assertEqual(proposals[0].state, ProposalState.PROPOSED)
        self.assertEqual(service.confirm(proposals[0]).state, ProposalState.CONFIRMED)

    def test_ambiguous_timezone_and_date_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "explicit UTC offset"):
            normalize_time(datetime(2026, 11, 1, 1, 30), "America/New_York")
        service = ProductivityService({})
        with self.assertRaisesRegex(ValueError, "clarification"):
            service.create_draft("draft_event", {}, ambiguous_date=True)

    def test_recurring_scope_required(self):
        with self.assertRaisesRegex(ValueError, "explicit scope"):
            ProductivityService({}).create_draft("change_event", {"recurrence": "weekly"})

    def test_preview_approval_stale_state_and_duplicate_send(self):
        current = ConnectorRecord("calendar", "e1", "event", NOW, {}, etag="v1")
        connector = MemoryConnector([current])
        service = ProductivityService({"calendar": connector})
        preview = service.create_draft(
            "send_invitation",
            {
                "sender": "owner@example.test",
                "recipients": ["guest@example.test"],
                "calendar_id": "work",
                "timezone": "UTC",
                "subject": "Review",
                "body_summary": "Invitation",
                "attendee_effects": ["adds guest"],
                "reversible": True,
                "expected_versions": {"e1": "v1"},
            },
        )
        self.assertEqual(preview.affected_calendar, "work")
        with self.assertRaises(PermissionError):
            service.execute_draft(
                preview.id, approved=False, executor=connector, idempotency_key="key"
            )
        first = service.execute_draft(
            preview.id, approved=True, executor=connector, idempotency_key="key"
        )
        self.assertIs(
            first,
            service.execute_draft(
                preview.id, approved=True, executor=connector, idempotency_key="key"
            ),
        )
        self.assertEqual(connector.executions, 1)
        stale = service.create_draft("change_event", {"expected_versions": {"e1": "old"}})
        with self.assertRaisesRegex(RuntimeError, "external state"):
            service.execute_draft(
                stale.id, approved=True, executor=connector, idempotency_key="other"
            )

    def test_consent_and_uncertain_speaker_evidence(self):
        with self.assertRaises(PermissionError):
            ingest_transcript(
                artifact_id="a",
                connector="upload",
                source_timestamp=NOW,
                segments=[],
                consent_status="unknown",
                recording_source="uploaded audio",
                retention_policy="30 days",
            )
        artifact = ingest_transcript(
            artifact_id="a",
            connector="upload",
            source_timestamp=NOW,
            segments=[
                {
                    "start_seconds": 1,
                    "end_seconds": 2,
                    "text": "Decision: ship",
                    "speaker_label": "Speaker 1",
                    "speaker_confidence": 0.4,
                }
            ],
            consent_status="granted",
            recording_source="uploaded transcript",
            retention_policy="30 days",
        )
        self.assertEqual(artifact.segments[0].speaker_confidence, 0.4)
        self.assertEqual(artifact.segments[0].source.segment_id, "0")

    def test_daily_brief_caps_confirmed_items_and_feedback_has_no_prompt(self):
        source = SourceRef("mail", "m1", NOW)
        items = [
            ActionItem(
                str(i),
                Statement("commitment", source),
                due_at=NOW + timedelta(days=i),
                state=ProposalState.CONFIRMED,
            )
            for i in range(4)
        ]
        job = DailyBriefJob(max_items=2)
        self.assertEqual([x.id for x in job.run(items, now=NOW)], ["0", "1"])
        self.assertEqual(
            job.dismissal_feedback("0", "not_relevant"),
            {"item_id": "0", "reason_code": "not_relevant"},
        )

    def test_connector_failures_are_normalized(self):
        with self.assertRaisesRegex(ConnectorError, "approved source unavailable"):
            ProductivityService({"bad": FailingConnector()}).daily_agenda(NOW, NOW)


if __name__ == "__main__":
    unittest.main()
