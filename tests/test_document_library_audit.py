import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import audit_document_library


class DocumentLibraryAuditTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
