import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from ops.docs import quality


class DocumentQualityTests(unittest.TestCase):
    def test_manifest_has_exact_phase_zero_denominator_and_complete_ownership(self):
        manifest = json.loads(quality.MANIFEST_PATH.read_text())
        self.assertEqual(len(manifest["documents"]), 15)
        required = {"path", "owner", "class", "profile", "lifecycle", "disposition", "sha256"}
        for document in manifest["documents"]:
            self.assertTrue(required.issubset(document))
            self.assertTrue(all(document[field] for field in required))

    def test_report_is_deterministic_hash_bound_and_content_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "safe.md"
            secret_phrase = "private board discussion phrase"
            source.write_text(f"# Title\n\n## Sources\n\n{secret_phrase}\n")
            first = quality.validate_document(source, profile="runtime", today=date(2026, 7, 29))
            second = quality.validate_document(source, profile="runtime", today=date(2026, 7, 29))
            self.assertEqual(first, second)
            self.assertEqual(
                first["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest()
            )
            self.assertNotIn(secret_phrase, json.dumps(first))
            with mock.patch.object(quality, "ROOT", Path(temp)):
                first = quality.validate_document(source, profile="runtime")
            report_path = quality.persist_report(first, Path(temp) / "reports")
            self.assertEqual(json.loads(report_path.read_text()), first)

    def test_seeded_blocker_critical_and_broken_link_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "bad.md"
            source.write_text("plain [TBD] password=abcdefghijklmnop [missing](nope.md)")
            with mock.patch.object(quality, "ROOT", Path(temp)):
                report = quality.validate_document(source, profile="runtime")
            rules = {finding["rule_id"] for finding in report["findings"]}
            self.assertEqual(report["status"], "fail")
            self.assertTrue(
                {"DOC-STRUCT-001", "DOC-PLACEHOLDER-001", "DOC-SEC-001", "DOC-LINK-001"} <= rules
            )

    def test_manifest_audit_is_non_mutating_and_uses_manifest_as_denominator(self):
        before = {
            item["path"]: quality.sha256_path(quality.ROOT / item["path"])
            for item in json.loads(quality.MANIFEST_PATH.read_text())["documents"]
        }
        result = quality.audit_manifest(today=date(2026, 7, 29))
        after = {path: quality.sha256_path(quality.ROOT / path) for path in before}
        self.assertEqual(before, after)
        self.assertEqual(result["documents"], 15)
        self.assertEqual(result["passing"] + result["failing"], 15)


if __name__ == "__main__":
    unittest.main()
