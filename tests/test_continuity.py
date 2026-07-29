from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ops.shared.continuity import (
    EncryptedSync,
    IntegrityError,
    Migration,
    MigrationLedger,
    compare_release,
    create_backup,
    dashboard_snapshot,
    make_change_record,
    restore_backup,
    sign_manifest,
    verify_backup,
    verify_manifest,
)


class ContinuityTests(unittest.TestCase):
    def test_manifest_signature_and_mismatch_detection(self):
        key = Ed25519PrivateKey.generate()
        manifest = sign_manifest({"git_commit": "abc", "worker_hash": "123"}, key)
        verify_manifest(manifest, key.public_key())
        corrupt = dict(manifest)
        corrupt["worker_hash"] = "bad"
        with self.assertRaises(IntegrityError):
            verify_manifest(corrupt, key.public_key())
        self.assertEqual(compare_release(manifest, {"git_commit": "abc"}), ["worker_hash"])

    def test_all_entity_conflict_rules_are_explicit(self):
        expected = {
            "artifact": "coexist",
            "comment": "append",
            "preference": "explicit_version_conflict",
            "document": "branch",
            "approval": "never_merge",
            "external_action_receipt": "immutable",
        }
        for entity, rule in expected.items():
            record = make_change_record(
                device_id="device",
                scope="tenant/owner",
                local_sequence=1,
                entity_type=entity,
                entity_id="entity",
                entity_version=1,
                operation_id=f"op-{entity}",
                content=entity.encode(),
            )
            self.assertEqual(record["conflict"]["rule"], rule)

    def test_migration_backup_checksum_future_and_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, backup = root / "db.sqlite", root / "backup.sqlite"
            sqlite3.connect(database).close()
            backup.write_bytes(database.read_bytes() or b"backup")
            ledger = MigrationLedger([Migration(1, "CREATE TABLE project(id INTEGER);")])
            ledger.apply(database, backup)
            ledger.apply(database, backup)
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE pj_schema_ledger SET checksum='bad'")
                connection.commit()
            with self.assertRaises(IntegrityError):
                ledger.apply(database, backup)

            interrupted = root / "interrupted.sqlite"
            sqlite3.connect(interrupted).close()
            broken = MigrationLedger([Migration(1, "CREATE TABLE partial(id); INVALID SQL;")])
            with self.assertRaises(sqlite3.Error):
                broken.apply(interrupted, backup)
            with sqlite3.connect(interrupted) as connection:
                names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
            self.assertNotIn("partial", names)

            future = root / "future.sqlite"
            with sqlite3.connect(future) as connection:
                connection.execute(
                    "CREATE TABLE pj_schema_ledger(version INTEGER PRIMARY KEY, checksum TEXT, applied_at INTEGER)"
                )
                connection.execute("INSERT INTO pj_schema_ledger VALUES(2, 'x', 0)")
            with self.assertRaises(IntegrityError):
                ledger.apply(future, backup)

    def test_encrypted_sync_replay_revocation_rotation_and_concurrent_edits(self):
        sync = EncryptedSync()
        key1, key2 = bytes(range(32)), bytes(reversed(range(32)))
        record = make_change_record(
            device_id="device-a",
            scope="tenant/owner",
            local_sequence=1,
            entity_type="document",
            entity_id="doc",
            entity_version=2,
            operation_id="op-1",
            content=b"branch-a",
            conflict_of="v1",
        )
        envelope = sync.seal(record, b"branch-a", "rotated-key", key2)
        self.assertEqual(sync.open(envelope, {"old-key": key1, "rotated-key": key2}), b"branch-a")
        with self.assertRaises(IntegrityError):
            sync.open(envelope, {"rotated-key": key2})
        stale = make_change_record(
            device_id="device-b",
            scope="tenant/owner",
            local_sequence=1,
            entity_type="preference",
            entity_id="theme",
            entity_version=2,
            operation_id="op-2",
            content=b"dark",
            conflict_of="v1",
        )
        self.assertEqual(stale["conflict"]["rule"], "explicit_version_conflict")
        revoked = sync.seal(stale, b"dark", "old-key", key1)
        sync.revoke("device-b")
        with self.assertRaises(IntegrityError):
            sync.open(revoked, {"old-key": key1})

    def test_backup_incomplete_missing_artifact_and_isolated_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, artifact = root / "db.sqlite", root / "report.txt"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE data(value TEXT)")
            artifact.write_text("artifact")
            backup = root / "backup"
            create_backup(database, [artifact], backup)
            active = root / "active"
            active.mkdir()
            (active / "old").write_text("old")
            isolated = restore_backup(backup, active, lambda path: verify_backup(path) is not None)
            self.assertTrue(isolated.is_dir())
            self.assertTrue((root / "active.rollback" / "old").is_file())
            (backup / "artifacts" / "report.txt").unlink()
            with self.assertRaises(IntegrityError):
                verify_backup(backup)
            manifest_path = backup / "backup-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["complete"] = False
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(IntegrityError):
                verify_backup(backup)

    def test_dashboard_bounds_reason_codes(self):
        result = dashboard_snapshot(connector_health={"ok": False, "reason_code": "secret payload"})
        self.assertEqual(result["connector_health"]["reason_code"], "CONNECTOR_UNHEALTHY")
        self.assertNotIn("secret payload", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
