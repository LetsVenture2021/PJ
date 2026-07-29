import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ops.docs.quality_profiles import QUALITY_PROFILES, get_quality_profile


ROOT = Path(__file__).resolve().parents[1]


class DocumentGovernanceTests(unittest.TestCase):
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
            if path.is_file()
            and path.name != "library-manifest.json"
            and "exports" not in path.parts
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
