import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from ops.docs import governance
from ops.docs.quality_profiles import QUALITY_PROFILES, get_quality_profile


ROOT = Path(__file__).resolve().parents[1]


class DocumentGovernanceBehaviorTests(unittest.TestCase):
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


class DocumentGovernanceInventoryTests(unittest.TestCase):
    def test_profiles_have_controls_and_bounded_thresholds(self):
        self.assertEqual(
            set(QUALITY_PROFILES),
            {"business", "operational", "technical", "audit", "corpus", "structured-data"},
        )
        for profile in QUALITY_PROFILES.values():
            self.assertTrue(profile.required_controls)
            self.assertTrue(all(0 <= value <= 1 for value in profile.thresholds.values()))
        with self.assertRaises(ValueError):
            get_quality_profile("unknown")

    def test_manifest_inventory_paths_and_digests(self):
        manifest = json.loads((ROOT / "documents/library-manifest.json").read_text())
        records = {record["path"]: record for record in manifest["documents"]}
        expected = {
            path.relative_to(ROOT).as_posix()
            for folder in (ROOT / "documents", ROOT / "docs")
            for path in folder.rglob("*")
            if path.is_file() and path.name != "library-manifest.json" and "exports" not in path.parts
        }
        self.assertEqual(set(records), expected)
        for relative, record in records.items():
            import hashlib

            self.assertEqual(
                record["content_sha256"], hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            )

    def test_bootstrap_defaults_to_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "unused.sqlite3"
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/bootstrap_document_manifest.py",
                    "--database",
                    str(database),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("DRY RUN", result.stdout)
            self.assertFalse(database.exists())


if __name__ == "__main__":
    unittest.main()
