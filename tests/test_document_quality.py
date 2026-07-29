import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

from ops.docs.quality import validate_content, validate_path
from ops.docs import service


class DocumentQualityTests(unittest.TestCase):
    def test_clean_document_passes_with_deterministic_digest(self):
        content = "# Operating standard\n\n## Scope\n\nControlled local operation.\n"
        first = validate_content(content)
        second = validate_content(content)
        self.assertEqual(first["status"], "pass")
        self.assertEqual(first["report_sha256"], second["report_sha256"])
        self.assertEqual(first["source_sha256"], second["source_sha256"])

    def test_drafting_residue_and_empty_link_block_release(self):
        report = validate_content("# Draft\n\n## Work\n\n[TBD - owner] and [source]().\n")
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            {item["rule_id"] for item in report["findings"]},
            {"DOC-COMPLETE-001", "DOC-LINK-001"},
        )

    def test_merge_conflict_markers_block_release_without_exposing_content(self):
        confidential = "internal resolution details"
        report = validate_content(
            "# Draft\n\n## Work\n\n<<<<<<< HEAD\n"
            f"{confidential}\n=======\nother version\n>>>>>>> topic\n"
        )
        self.assertEqual(report["status"], "fail")
        self.assertIn("DOC-COMPLETE-002", {item["rule_id"] for item in report["findings"]})
        self.assertNotIn(confidential, str(report))

    def test_security_finding_does_not_echo_matched_value(self):
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        report = validate_content(f"# Draft\n\n## Value\n\n{secret}\n")
        self.assertEqual(report["counts"]["critical"], 1)
        self.assertNotIn(secret, str(report))

    def test_heading_hierarchy_is_checked(self):
        report = validate_content("# Title\n\n### Skipped\n\nBody\n")
        self.assertEqual(report["status"], "fail")
        self.assertIn("DOC-A11Y-001", {item["rule_id"] for item in report["findings"]})

    def test_validate_path_adds_audit_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "document.md"
            path.write_text("# Title\n\n## Scope\n\nBody.\n", encoding="utf-8")
            report = validate_path(path)
        self.assertEqual(report["status"], "pass")
        self.assertIn("validated_at", report)

    def test_finalization_retains_report_for_exact_final_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            documents = root / "documents"
            exports = documents / "exports"
            artifacts = exports / ".artifacts"
            artifacts.mkdir(parents=True)
            with (
                mock.patch.object(service, "_DB_PATH", root / "quality.sqlite3"),
                mock.patch.object(service, "DOCS_DIR", documents),
                mock.patch.object(service, "EXPORTS_DIR", exports),
                mock.patch.object(service, "ARTIFACTS_DIR", artifacts),
            ):
                drafted = service.draft_document(
                    "meeting_memo",
                    "Quality evidence",
                    json.dumps(
                        {
                            "Attendees": "PJ and owner",
                            "Context": "Release quality",
                            "Discussion": "Controls passed.",
                            "Decisions": "Retain evidence.",
                            "Action Items": "Publish.",
                        }
                    ),
                )
                finalized = service.finalize_document(drafted["doc_id"])
                with service._db() as connection:
                    row = connection.execute(
                        "SELECT source_sha256, status FROM docops_quality_reports "
                        "WHERE doc_id=? ORDER BY created_at DESC",
                        (drafted["doc_id"],),
                    ).fetchall()
        self.assertEqual(finalized["status"], "final")
        self.assertIn((finalized["sha256"], "pass"), row)


if __name__ == "__main__":
    unittest.main()
