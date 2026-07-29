import json
import tempfile
import unittest
from pathlib import Path

from ops.shared.process_mining import analyze_jsonl, event_from_record


class ProcessMiningTests(unittest.TestCase):
    def test_rejects_unknown_message_and_missing_case(self):
        base = {"timestamp": "2026-07-29T10:00:00Z", "request_id": "request-1"}
        self.assertIsNone(event_from_record({**base, "message": "user supplied text"}))
        self.assertIsNone(
            event_from_record(
                {"timestamp": "2026-07-29T10:00:00Z", "message": "tool.execution.started"}
            )
        )

    def test_discovers_variants_failures_latency_and_replay(self):
        records = [
            self._event("call-1", "tool.execution.started", 0),
            self._event("call-1", "tool.execution.completed", 1, duration_ms=2500),
            self._event("call-2", "tool.execution.started", 2),
            self._event("call-2", "tool.execution.failed", 3, duration_ms=3000),
            self._event("call-3", "tool.execution.started", 4),
            self._event("call-3", "tool.execution.failed", 5, duration_ms=4000),
            self._event("call-4", "tool.execution.started", 6),
            self._event("call-4", "tool.execution.failed", 7, duration_ms=3500),
            self._event("call-5", "tool.execution.replayed", 8, duration_ms=2),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pj.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in records) + "\nnot-json\n")
            report = analyze_jsonl(path)

        self.assertEqual(report["case_count"], 5)
        self.assertEqual(report["event_count"], 9)
        self.assertEqual(report["input"]["malformed"], 1)
        failed = next(
            row for row in report["activities"] if row["activity"] == "tool:export_document"
        )
        self.assertEqual(failed["failure_rate"], 0.6)
        self.assertEqual(failed["p95_duration_ms"], 4000.0)
        signals = {item["signal"] for item in report["recommendations"]}
        self.assertIn("failure_rate", signals)
        self.assertIn("p95_duration_ms", signals)
        self.assertIn("replay_count", signals)

    def test_ignores_payload_fields(self):
        event = event_from_record(
            {
                **self._event("call-1", "tool.execution.completed", 0),
                "prompt": "sensitive prompt",
                "arguments": {"secret": "value"},
                "result": "sensitive result",
            }
        )
        self.assertIsNotNone(event)
        self.assertFalse(hasattr(event, "prompt"))
        self.assertFalse(hasattr(event, "arguments"))
        self.assertFalse(hasattr(event, "result"))

    def test_normalizes_route_identifiers(self):
        event = event_from_record(
            {
                "timestamp": "2026-07-29T10:00:00Z",
                "message": "http.request.completed",
                "request_id": "request-1",
                "http_path": "/responses/sessions/private-session/turns",
                "http_status": 200,
            }
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.feature, "route:/responses/sessions/:id/turns")

    @staticmethod
    def _event(case_id, message, second, **extra):
        return {
            "timestamp": f"2026-07-29T10:00:{second:02d}Z",
            "message": message,
            "tool_call_id": case_id,
            "tool_name": "export_document",
            **extra,
        }


if __name__ == "__main__":
    unittest.main()
