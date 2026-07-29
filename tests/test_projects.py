import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from ops.projects import migrations
from ops.projects import service


class ProjectServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "old.sqlite3"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE chat_sessions (id TEXT PRIMARY KEY, title TEXT)")

    def tearDown(self):
        self.temp.cleanup()

    def test_old_database_and_repeated_migration(self):
        migrations.run(self.db)
        migrations.run(self.db)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM project_schema_versions").fetchone()[0], 1
            )
            self.assertEqual(conn.execute("SELECT title FROM chat_sessions").fetchall(), [])

    def test_archive_restore_and_legacy_conversation(self):
        project = service.create_project("owner", "Alpha", db_path=self.db)
        self.assertEqual(
            service.archive_project(project["id"], "owner", db_path=self.db)["status"],
            "archived",
        )
        self.assertEqual(
            service.restore_project(project["id"], "owner", db_path=self.db)["status"],
            "active",
        )
        self.assertEqual(service.list_records(project["id"], "conversations", db_path=self.db), [])

    def test_scoped_retrieval_isolation(self):
        first = service.create_project("owner", "One", db_path=self.db)
        second = service.create_project("owner", "Two", db_path=self.db)
        now = service._now()
        with service._connect(self.db) as conn:
            conn.execute(
                "INSERT INTO project_memory_links VALUES (?,?,?,?,?,?,?,?)",
                (service._id(), first["id"], "notes", "one", 0, "active", now, now),
            )
            conn.execute(
                "INSERT INTO project_memory_links VALUES (?,?,?,?,?,?,?,?)",
                (service._id(), second["id"], "notes", "two", 1, "active", now, now),
            )
        self.assertEqual(
            service.scoped_references(project_id=first["id"], db_path=self.db)[0]["ref_id"],
            "one",
        )
        self.assertEqual(
            service.scoped_references(
                "owner_global_approved_memory", owner_id="owner", db_path=self.db
            )[0]["ref_id"],
            "two",
        )

    def _bundle(self, manifest, files=None):
        path = self.root / "bundle.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for name, content in (files or {}).items():
                archive.writestr(name, content)
        return path

    def test_import_rejects_corruption_traversal_and_hash_mismatch(self):
        bad = self.root / "bad.zip"
        bad.write_bytes(b"not zip")
        with self.assertRaises(zipfile.BadZipFile):
            service.import_project(bad, owner_id="owner", artifact_root=self.root, db_path=self.db)
        base = {
            "schema_version": 1,
            "records": {"projects": [{"id": "old", "name": "Imported"}]},
            "artifacts": [],
        }
        traversal = dict(
            base,
            artifacts=[{"status": "included", "relative_path": "../secret", "sha256": "x"}],
        )
        with self.assertRaises(service.ProjectError):
            service.import_project(
                self._bundle(traversal),
                owner_id="owner",
                artifact_root=self.root,
                db_path=self.db,
            )
        mismatch = dict(
            base,
            artifacts=[
                {
                    "status": "included",
                    "relative_path": "safe.txt",
                    "sha256": "0" * 64,
                }
            ],
        )
        with self.assertRaises(service.ProjectError):
            service.import_project(
                self._bundle(mismatch, {"artifacts/safe.txt": b"safe"}),
                owner_id="owner",
                artifact_root=self.root,
                db_path=self.db,
            )

    def test_missing_artifact_exports_as_missing(self):
        project = service.create_project("owner", "Alpha", db_path=self.db)
        service.link_artifact(
            project["id"],
            "response_artifact",
            "ART-1",
            relative_path="gone.txt",
            db_path=self.db,
        )
        bundle = service.export_project(
            project["id"],
            self.root / "out.zip",
            artifact_root=self.root,
            db_path=self.db,
        )
        with zipfile.ZipFile(bundle) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["artifacts"][0]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
