import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from jsonschema import validate

from ops.docs.quality import QualityConfig, report_schema, validate_document
from ops.docs.quality.service import (
    QualityGateError,
    approve_report,
    assert_report_current,
    report_is_approved,
)


class DocumentQualityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def report(self, text, **kwargs):
        source = self.root / "source.md"
        if isinstance(text, bytes):
            source.write_bytes(text)
        else:
            source.write_text(text, encoding="utf-8")
        return validate_document(source, **kwargs)

    def test_positive_unicode_rtl_emoji_and_combining_text(self):
        report = self.report("# عنوان 😀\n\nنص عربي café cafe\u0301\n")
        self.assertFalse(report.failed)
        self.assertEqual(report.findings, [])

    def test_missing_title_and_heading_boundaries(self):
        missing = self.report("body")
        self.assertIn("DOC-STRUCT-001", {item.rule_id for item in missing.findings})
        boundary = self.report("# T\n\n#### Allowed\n")
        self.assertNotIn("DOC-STRUCT-002", {item.rule_id for item in boundary.findings})
        too_deep = self.report("# T\n\n##### Deep\n")
        self.assertIn("DOC-STRUCT-002", {item.rule_id for item in too_deep.findings})
        jump = self.report("# T\n\n### Jump\n")
        self.assertIn("DOC-STRUCT-003", {item.rule_id for item in jump.findings})

    def test_table_row_and_column_boundaries(self):
        config = QualityConfig(max_table_rows=3, max_table_columns=2)
        accepted = self.report("# T\n\n|a|b|\n|--|--|\n|1|2|\n", config=config)
        self.assertNotIn("DOC-TABLE-001", {item.rule_id for item in accepted.findings})
        rejected = self.report("# T\n\n|a|b|c|\n|--|--|--|\n", config=config)
        self.assertIn("DOC-TABLE-001", {item.rule_id for item in rejected.findings})

    def test_long_tokens_and_nested_markdown_are_deterministic(self):
        text = "# T\n\n> - **bold _nested_**\n\n" + "x" * 81
        first = self.report(text)
        second = self.report(text)
        self.assertEqual(first.digest, second.digest)
        self.assertIn("DOC-READ-001", {item.rule_id for item in first.findings})

    def test_malformed_urls_and_formula_prefixes(self):
        report = self.report("# T\n\nhttps://bad..example/path\n\n=HYPERLINK(A1)\n")
        rules = {item.rule_id for item in report.findings}
        self.assertIn("DOC-LINK-001", rules)
        self.assertIn("DOC-SEC-001", rules)

    def test_sensitive_findings_never_contain_raw_value(self):
        secret = "sk-supersecretvalue123456"
        report = self.report(f"# T\n\napi_key={secret}\n")
        serialized = report.to_json()
        self.assertIn("DOC-SEC-002", serialized)
        self.assertNotIn(secret, serialized)

    def test_git_conflict_markers_block_manifest_validation_without_raw_content(self):
        confidential = "internal resolution details"
        report = self.report(f"# T\n\n<<<<< ours\n{confidential}\n")
        serialized = report.to_json()
        self.assertIn("DOC-COMPLETE-002", {item.rule_id for item in report.findings})
        self.assertTrue(report.failed)
        self.assertNotIn(confidential, serialized)

    def test_git_diff3_marker_blocks_manifest_validation(self):
        report = self.report("# T\n\n||||| base\n")
        self.assertIn("DOC-COMPLETE-002", {item.rule_id for item in report.findings})

    def test_equals_divider_is_not_a_manifest_conflict_marker(self):
        report = self.report("# T\n\n=======\n")
        self.assertNotIn("DOC-COMPLETE-002", {item.rule_id for item in report.findings})

    def test_malformed_utf8_is_a_sanitized_blocker(self):
        report = self.report(b"# title\n\xffsecret")
        self.assertEqual(report.findings[0].rule_id, "DOC-INPUT-001")
        self.assertNotIn("secret", report.to_json())

    def test_schema_ordering_staleness_and_config_mismatch(self):
        source = self.root / "source.md"
        source.write_text("# T\n", encoding="utf-8")
        report = validate_document(source)
        validate(report.as_dict(), report_schema())
        self.assertEqual(report.as_dict()["findings"], [])
        assert_report_current(report, source)
        source.write_text("# Changed\n", encoding="utf-8")
        with self.assertRaises(QualityGateError):
            assert_report_current(report, source)
        with self.assertRaises(QualityGateError):
            assert_report_current(report, source, config=QualityConfig(max_heading_depth=3))

    def test_waivers_expire_at_end_of_their_date(self):
        waiver = [{"rule_id": "DOC-CONTENT-001", "expires": "2030-01-02"}]
        active = self.report("# T\nTODO\n", waivers=waiver, today=date(2030, 1, 2))
        expired = self.report("# T\nTODO\n", waivers=waiver, today=date(2030, 1, 3))
        self.assertFalse(active.failed)
        self.assertTrue(expired.failed)

    def test_approval_is_bound_to_content_and_report(self):
        report = self.report("# T\n")
        connection = sqlite3.connect(self.root / "quality.sqlite3")
        approve_report(connection, report)
        self.assertTrue(report_is_approved(connection, report))
        changed = self.report("# Changed\n")
        self.assertFalse(report_is_approved(connection, changed))
        connection.close()

    def test_golden_normalized_report(self):
        fixture_root = Path(__file__).parent / "fixtures"
        source = fixture_root / "sources" / "brief.md"
        actual = validate_document(source).as_dict()
        expected = json.loads((fixture_root / "reports" / "brief.json").read_text())
        for value in (actual, expected):
            value.pop("source")
        self.assertEqual(actual, expected)
