import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from ops.docs import governance


class DocumentGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.document = self.root / "brief.md"
        self.document.write_text("A current capability.[^CLM-CAP-1]\n", encoding="utf-8")
        self.sources = self.root / "registry.json"
        self.sources.write_text(
            json.dumps(
                {
                    "sources": [
                        {
                            "source_id": "SRC-1",
                            "expiry_review_date": "2026-01-31",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def _sidecar(self, impact="current_capability"):
        self.document.with_suffix(".claims.json").write_text(
            json.dumps(
                {
                    "review_date": "2026-12-31",
                    "claims": [
                        {
                            "claim_id": "CLM-CAP-1",
                            "source_ids": ["SRC-1"],
                            "impact": impact,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_earliest_source_expiry_blocks_high_impact_claim(self):
        self._sidecar()
        with patch.object(governance, "SOURCES_FILE", self.sources):
            result = governance.evaluate_document(self.document, today=date(2026, 2, 1))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["fresh_until"], "2026-01-31")
        self.assertIn("expired current_capability claim CLM-CAP-1", result["blockers"])

    def test_expired_low_impact_evidence_is_not_export_blocker(self):
        self._sidecar("historical")
        with patch.object(governance, "SOURCES_FILE", self.sources):
            result = governance.evaluate_document(self.document, today=date(2026, 2, 1))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["freshness"], "expired")

    def test_change_dependency_mapping(self):
        mapping = self.root / "dependencies.json"
        mapping.write_text(
            json.dumps(
                {
                    "dependencies": [
                        {
                            "path_patterns": ["runtime_config\\.py"],
                            "documents": ["docs/runbook.md"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with patch.object(governance, "DEPENDENCIES_FILE", mapping):
            self.assertEqual(
                governance.documents_for_changes(["runtime_config.py"]), ["docs/runbook.md"]
            )

    def test_provider_check_is_optional(self):
        self.assertEqual(
            governance.provider_fact_check(self.document),
            {"status": "skipped", "reason": "no ResponsesProvider configured"},
        )


if __name__ == "__main__":
    unittest.main()
