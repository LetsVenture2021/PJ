import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import docops


class TestDocOpsReviews(unittest.TestCase):
    def setUp(self):
        self.original_paths = (
            docops._DB_PATH,
            docops.DOCS_DIR,
            docops.EXPORTS_DIR,
            docops.ARTIFACTS_DIR,
        )
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        docops._DB_PATH = root / "docops.sqlite3"
        docops.DOCS_DIR = root / "documents"
        docops.EXPORTS_DIR = docops.DOCS_DIR / "exports"
        docops.ARTIFACTS_DIR = docops.EXPORTS_DIR / ".artifacts"
        docops.DOCS_DIR.mkdir()
        docops.EXPORTS_DIR.mkdir()
        docops.ARTIFACTS_DIR.mkdir()

    def tearDown(self):
        (
            docops._DB_PATH,
            docops.DOCS_DIR,
            docops.EXPORTS_DIR,
            docops.ARTIFACTS_DIR,
        ) = self.original_paths
        self.tempdir.cleanup()

    def _draft(self):
        return docops.draft_document(
            "meeting_memo",
            "Controlled decision",
            json.dumps(
                {
                    "Attendees": "Operations",
                    "Context": "A material control change.",
                    "Discussion": "Risks were assessed.",
                    "Decisions": "Adopt the control.",
                    "Action Items": "Implement it.",
                }
            ),
        )

    def test_high_risk_matrix_and_policy_invalidation(self):
        drafted = self._draft()
        doc_id = drafted["doc_id"]
        self.assertEqual(
            docops.set_document_governance(doc_id, "high", "subject-author")["status"],
            "configured",
        )
        finalized = docops.finalize_document(doc_id)
        digest = hashlib.sha256(b"quality report").hexdigest()
        report = docops.record_quality_report(doc_id, 1, digest, True)
        self.assertEqual(report["source_sha256"], finalized["sha256"])

        docops.register_reviewer("subject-author", ["author", "accountable_approver"])
        docops.register_reviewer("subject-domain", ["domain_reviewer"])
        docops.record_review_decision(
            doc_id, 1, "subject-author", "accountable_approver", "approve", digest
        )
        self.assertFalse(docops.get_document(doc_id)["approval"]["audience_ready"])
        docops.record_review_decision(
            doc_id, 1, "subject-domain", "domain_reviewer", "approve", digest
        )
        approved = docops.get_document(doc_id)["approval"]
        self.assertTrue(approved["audience_ready"])

        docops.set_document_governance(
            doc_id, "high", "subject-author", governing_policy_version="2"
        )
        self.assertFalse(docops.get_document(doc_id)["approval"]["audience_ready"])

    def test_presentation_requires_visual_preview_inspection(self):
        drafted = docops.draft_presentation(
            "Visual review",
            "Leaders",
            json.dumps([{"layout": "title", "title": "Decision", "subtitle": "Approve the plan."}]),
        )
        docops.set_document_governance(
            drafted["doc_id"], "normal", "author-1", presentation_heavy=True
        )
        finalized = docops.finalize_document(drafted["doc_id"])
        digest = hashlib.sha256(b"presentation quality").hexdigest()
        report = docops.record_quality_report(drafted["doc_id"], 1, digest, True)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(report["visual_preview_required"])
        self.assertFalse(finalized["artifact"]["audience_ready"])

    def test_document_metadata_never_exposes_paths(self):
        drafted = self._draft()
        rendered = json.dumps(
            {
                "get": docops.get_document(drafted["doc_id"]),
                "list": docops.list_documents(),
                "artifact": docops.resolve_export_artifact(drafted["artifact"]["artifact_id"]),
            }
        )
        self.assertNotIn(str(docops.DOCS_DIR), rendered)


if __name__ == "__main__":
    unittest.main()
