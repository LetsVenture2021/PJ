import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops.docs.quality import (
    calculate_scorecard,
    record_control_calibration,
    record_quality_incident,
    record_quality_run,
    regression_alerts,
)


class DocumentQualityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "quality.sqlite3"
        self.now = datetime(2026, 7, 29, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, *, when=None, controls=(10, 9), waivers=0, suppressions=0):
        when = when or self.now - timedelta(days=1)
        return record_quality_run(
            doc_id="DOC-1",
            version=1,
            profile="prod",
            source_hash="a" * 64,
            report_hash="b" * 64,
            validator_version="1.2.3",
            status="failed",
            started_at=when - timedelta(seconds=1),
            completed_at=when,
            controls_executed=controls[0],
            controls_passed=controls[1],
            finding_counts={"blocker": {"DOC.BLOCKER": 1}},
            validation_duration_ms=1000,
            artifact_format="pdf",
            artifact_byte_size=200,
            review_cycle_duration_ms=5000,
            revision_count=1,
            source_freshness_state="stale",
            approval_state="pending",
            waiver_count=waivers,
            waiver_expiry_state="active" if waivers else "none",
            suppression_count=suppressions,
            citation_controls=(2, 3),
            accessibility_controls=(4, 5),
            fidelity_controls=(1, 2),
            db_path=self.db,
        )

    def test_records_only_aggregate_metadata_and_is_idempotent(self):
        self.assertTrue(self.record())
        self.assertFalse(self.record())
        with sqlite3.connect(self.db) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(docops_quality_ledger)")}
            self.assertFalse(
                columns & {"prose", "prompt", "tool_arguments", "content", "matched_text"}
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM docops_quality_ledger").fetchone()[0], 1
            )

    def test_rejects_prose_findings_and_overlap_between_passes_and_waivers(self):
        with self.assertRaisesRegex(ValueError, "whitespace"):
            record_quality_run(
                doc_id="DOC-1",
                version=1,
                profile="prod",
                source_hash="a" * 64,
                report_hash="b" * 64,
                validator_version="1",
                status="failed",
                started_at=self.now,
                completed_at=self.now,
                controls_executed=1,
                controls_passed=0,
                finding_counts={"high": {"matched secret text": 1}},
                validation_duration_ms=1,
                artifact_format="md",
                artifact_byte_size=1,
                db_path=self.db,
            )
        with self.assertRaisesRegex(ValueError, "disjoint"):
            self.record(controls=(10, 9), waivers=2)

    def test_scorecard_has_denominators_baseline_policy_and_lagging_indicators(self):
        self.record()
        record_quality_incident(
            incident_id="INC-1",
            doc_id="DOC-1",
            version=1,
            incident_type="broken_link",
            occurred_at=self.now - timedelta(days=1),
            db_path=self.db,
        )
        card = calculate_scorecard(period_end=self.now, db_path=self.db)
        leading = card["indicators"]["leading"]
        self.assertEqual(leading["blocker_density_per_document"]["denominator"], 1)
        self.assertEqual(leading["citation_provenance_coverage"]["value"], 2 / 3)
        self.assertEqual(card["indicators"]["lagging"]["broken_link_incidents"], 1)
        self.assertFalse(card["baseline_complete"])
        self.assertIn("unresolved_blockers", card["target_policy"]["zero_tolerance_from_day_one"])

    def test_validator_skips_trigger_coverage_regression(self):
        self.record(when=self.now - timedelta(days=31))
        self.record(when=self.now - timedelta(days=1), controls=(0, 0))
        card = calculate_scorecard(period_end=self.now, db_path=self.db)
        alerts = regression_alerts(card, {"leading.blocker_density_per_document": 2.0})
        self.assertEqual(alerts[0]["metric"], "coverage.validator_execution_rate")

    def test_quarterly_calibration_uses_aggregate_counts(self):
        self.assertTrue(
            record_control_calibration(
                calibration_id="CAL-2026-Q3",
                seeded_defects=10,
                seeded_defects_detected=9,
                auto_passes_sampled=20,
                human_review_defects=1,
                validator_version="1.2.3",
                performed_at=self.now,
                db_path=self.db,
            )
        )
        with self.assertRaisesRegex(ValueError, "subsets"):
            record_control_calibration(
                calibration_id="bad",
                seeded_defects=1,
                seeded_defects_detected=2,
                auto_passes_sampled=1,
                human_review_defects=0,
                validator_version="1",
                db_path=self.db,
            )


if __name__ == "__main__":
    unittest.main()
