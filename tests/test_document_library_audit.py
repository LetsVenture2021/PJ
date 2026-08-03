import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import audit_document_library, bootstrap_document_manifest


class DocumentLibraryAuditTests(unittest.TestCase):
    def _versioned_record(self, relative_path: str, digest: str) -> dict:
        return {
            "schema_version": "1.0.0",
            "stable_id": "DOC-SAMPLE",
            "path": relative_path,
            "class": "technical",
            "owner": "PJ maintainers",
            "status": "approved",
            "classification": "internal",
            "source_of_truth": True,
            "content_sha256": digest,
            "last_reviewed_at": None,
            "next_review_at": None,
            "quality_profile": "technical",
            "supersedes": [],
            "superseded_by": [],
            "derived_from": [],
            "supports": [],
            "references": [],
            "generated_artifacts": [],
        }

    def test_audit_detects_digest_mismatch_without_exposing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            source = root / "docs" / "sample.md"
            source.write_text("# Sample\n\n## Scope\n\nConfidential prose.\n")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "documents": [
                            {
                                "document_id": "DOC-sample",
                                "path": "docs/sample.md",
                                "class": "technical",
                                "owner": "PJ maintainers",
                                "audience": "internal",
                                "classification": "internal",
                                "status": "approved",
                                "sha256": "0" * 64,
                            }
                        ],
                    }
                )
            )
            with mock.patch.object(audit_document_library, "ROOT", root):
                result = audit_document_library.audit(manifest)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["findings"][0]["error"], "sha256_mismatch")
        self.assertNotIn("Confidential prose", str(result))

    def test_audit_accepts_clean_catalog_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            source = root / "docs" / "sample.md"
            source.write_text("# Sample\n\n## Scope\n\nControlled operation.\n")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "documents": [
                            {
                                "document_id": "DOC-sample",
                                "path": "docs/sample.md",
                                "class": "technical",
                                "owner": "PJ maintainers",
                                "audience": "internal",
                                "classification": "internal",
                                "status": "approved",
                                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                            }
                        ],
                    }
                )
            )
            with mock.patch.object(audit_document_library, "ROOT", root):
                result = audit_document_library.audit(manifest)
        self.assertEqual(result["status"], "pass")

    def test_mixed_manifest_validates_versioned_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            legacy_source = root / "docs" / "legacy.md"
            legacy_source.write_text("# Legacy\n\n## Scope\n\nControlled operation.\n")
            versioned_source = root / "docs" / "versioned.md"
            versioned_source.write_text("# Versioned\n\n## Scope\n\nControlled operation.\n")
            versioned = self._versioned_record(
                "docs/versioned.md", hashlib.sha256(versioned_source.read_bytes()).hexdigest()
            )
            del versioned["owner"]
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "documents": [
                            {
                                "document_id": "DOC-legacy",
                                "path": "docs/legacy.md",
                                "class": "technical",
                                "owner": "PJ maintainers",
                                "classification": "internal",
                                "status": "approved",
                                "sha256": hashlib.sha256(legacy_source.read_bytes()).hexdigest(),
                            },
                            versioned,
                        ],
                    }
                )
            )
            with mock.patch.object(audit_document_library, "ROOT", root):
                result = audit_document_library.audit(manifest)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("owner" in error for error in result["schema_errors"]))

    def test_legacy_catalog_still_runs_content_quality_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            source = root / "docs" / "legacy.md"
            source.write_text("# Legacy\n\n## Scope\n\nTODO: replace draft residue.\n")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "documents": [
                            {
                                "document_id": "DOC-legacy",
                                "path": "docs/legacy.md",
                                "class": "technical",
                                "owner": "PJ maintainers",
                                "classification": "internal",
                                "status": "approved",
                                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                            }
                        ],
                    }
                )
            )
            with mock.patch.object(audit_document_library, "ROOT", root):
                result = audit_document_library.audit(manifest)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["findings"][0]["error"], "quality_gate_failed")

    def test_manifest_envelope_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            source = root / "docs" / "sample.md"
            source.write_text("# Sample\n\n## Scope\n\nControlled operation.\n")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "generated_at": 123,
                        "documents": [
                            self._versioned_record(
                                "docs/sample.md", hashlib.sha256(source.read_bytes()).hexdigest()
                            )
                        ],
                    }
                )
            )
            with mock.patch.object(audit_document_library, "ROOT", root):
                result = audit_document_library.audit(manifest)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(
            any("generated_at" in error or "123" in error for error in result["schema_errors"])
        )

    def test_review_timestamps_require_calendar_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            source = root / "docs" / "sample.md"
            source.write_text("# Sample\n\n## Scope\n\nControlled operation.\n")
            record = self._versioned_record(
                "docs/sample.md", hashlib.sha256(source.read_bytes()).hexdigest()
            )
            record["last_reviewed_at"] = "2026-02-31T00:00:00Z"
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": "1.0.0", "documents": [record]}))
            with mock.patch.object(audit_document_library, "ROOT", root):
                result = audit_document_library.audit(manifest)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("date-time" in error for error in result["schema_errors"]))

    def test_malformed_record_schema_versions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            source = root / "docs" / "sample.md"
            source.write_text("# Sample\n\n## Scope\n\nControlled operation.\n")
            record = self._versioned_record(
                "docs/sample.md", hashlib.sha256(source.read_bytes()).hexdigest()
            )
            record["schema_version"] = "1.0.0-alpha..1"
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": "1.0.0", "documents": [record]}))
            with mock.patch.object(audit_document_library, "ROOT", root):
                result = audit_document_library.audit(manifest)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(result["schema_errors"])

    def test_bootstrap_rejects_future_record_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            source = root / "docs" / "sample.md"
            source.write_text("# Sample\n\n## Scope\n\nControlled operation.\n")
            record = self._versioned_record(
                "docs/sample.md", hashlib.sha256(source.read_bytes()).hexdigest()
            )
            record["schema_version"] = "2.0.0"
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": "1.0.0", "documents": [record]}))
            with (
                mock.patch.object(bootstrap_document_manifest, "ROOT", root),
                self.assertRaisesRegex(ValueError, "unsupported document metadata schema_version"),
            ):
                bootstrap_document_manifest.propose_records(manifest)

    def test_bootstrap_emits_current_record_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            source = root / "docs" / "sample.md"
            source.write_text("# Sample\n\n## Scope\n\nControlled operation.\n")
            record = self._versioned_record(
                "docs/sample.md", hashlib.sha256(source.read_bytes()).hexdigest()
            )
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": "1.0.0", "documents": [record]}))
            with mock.patch.object(bootstrap_document_manifest, "ROOT", root):
                records = bootstrap_document_manifest.propose_records(manifest)
        self.assertEqual(records[0]["schema_version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
