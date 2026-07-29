import hashlib
import json
import socket
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import docops
from ops.docs import uploads as document_uploads


class TestUploadProcessing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.old_docops = {
            "_DB_PATH": docops._DB_PATH,
            "DOCS_DIR": docops.DOCS_DIR,
            "EXPORTS_DIR": docops.EXPORTS_DIR,
            "ARTIFACTS_DIR": docops.ARTIFACTS_DIR,
        }
        self.old_uploads_dir = document_uploads.UPLOADS_DIR
        self.old_derived_dir = document_uploads.DERIVED_DIR

        docops._DB_PATH = root / "test.sqlite3"
        docops.DOCS_DIR = root / "documents"
        docops.EXPORTS_DIR = docops.DOCS_DIR / "exports"
        docops.ARTIFACTS_DIR = docops.EXPORTS_DIR / ".artifacts"
        document_uploads.UPLOADS_DIR = docops.DOCS_DIR / "uploads"
        document_uploads.DERIVED_DIR = document_uploads.UPLOADS_DIR / ".derived"
        document_uploads.UPLOADS_DIR.mkdir(parents=True)
        document_uploads.DERIVED_DIR.mkdir(parents=True)
        docops.EXPORTS_DIR.mkdir(parents=True)
        docops.ARTIFACTS_DIR.mkdir(parents=True)

    def tearDown(self):
        for name, value in self.old_docops.items():
            setattr(docops, name, value)
        document_uploads.UPLOADS_DIR = self.old_uploads_dir
        document_uploads.DERIVED_DIR = self.old_derived_dir
        self.temp_dir.cleanup()

    def _managed_file(
        self,
        upload_id: str,
        relative_path: str,
        payload: bytes,
        *,
        session_id: str = "session-upload-123",
    ) -> tuple[Path, str]:
        path = document_uploads.UPLOADS_DIR / session_id / upload_id / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path, session_id

    def _register(
        self,
        upload_id: str,
        relative_path: str,
        payload: bytes,
        mime: str,
        *,
        session_id: str = "session-upload-123",
    ) -> dict:
        path, session_id = self._managed_file(
            upload_id, relative_path, payload, session_id=session_id
        )
        sha = hashlib.sha256(payload).hexdigest()
        return document_uploads.register_uploaded_documents(
            upload_id,
            session_id,
            [
                {
                    "saved_path": f"uploads/{session_id}/{upload_id}/{relative_path}",
                    "path": path,
                    "name": Path(relative_path).name,
                    "mime": mime,
                    "size": len(payload),
                    "sha256": sha,
                }
            ],
        )

    def test_legacy_backfill_migrates_to_canonical_tables(self):
        payload = b"legacy text"
        path, session_id = self._managed_file(
            "UPL-11111111111111111111111111111111", "legacy.txt", payload
        )
        sha = hashlib.sha256(payload).hexdigest()
        conn = sqlite3.connect(docops._DB_PATH)
        try:
            conn.execute(
                """CREATE TABLE docops_uploads (
                    upload_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (upload_id, relative_path)
                )"""
            )
            conn.execute(
                "INSERT INTO docops_uploads "
                "(upload_id, session_id, relative_path, name, path, mime_type, byte_size, sha256) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    "UPL-11111111111111111111111111111111",
                    session_id,
                    f"uploads/{session_id}/UPL-11111111111111111111111111111111/legacy.txt",
                    "legacy.txt",
                    str(path),
                    "text/plain",
                    len(payload),
                    sha,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        listed = document_uploads.list_uploaded_documents(session_id=session_id)
        self.assertEqual(listed["count"], 1)

        with document_uploads._db() as db:
            doc_count = db.execute("SELECT COUNT(*) FROM docops_upload_documents").fetchone()[0]
            inst_count = db.execute("SELECT COUNT(*) FROM docops_upload_instances").fetchone()[0]
            job_count = db.execute("SELECT COUNT(*) FROM docops_upload_jobs").fetchone()[0]
        self.assertEqual(doc_count, 1)
        self.assertEqual(inst_count, 1)
        self.assertEqual(job_count, 1)

    def test_register_dedupes_by_hash_and_enqueues_once(self):
        first = self._register(
            "UPL-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "one.txt", b"same", "text/plain"
        )
        second = self._register(
            "UPL-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "nested/two.txt", b"same", "text/plain"
        )

        first_doc = first["documents"][0]
        second_doc = second["documents"][0]
        self.assertEqual(first_doc["document_id"], second_doc["document_id"])
        self.assertFalse(first_doc["reused"])
        self.assertTrue(second_doc["reused"])

        jobs = document_uploads.list_processing_jobs(limit=10)
        self.assertEqual(jobs["count"], 1)

    def test_processor_writes_derived_outputs_atomically(self):
        registered = self._register(
            "UPL-cccccccccccccccccccccccccccccccc",
            "brief.md",
            b"# Brief\n\nUpload body for processing.\n",
            "text/markdown",
        )
        document_id = registered["documents"][0]["document_id"]

        result = document_uploads.run_upload_processor_once("worker-test")
        self.assertEqual(result["completed"], 1)
        derived = (
            document_uploads.DERIVED_DIR / document_id / document_uploads.CURRENT_EXTRACTOR_VERSION
        )
        self.assertTrue((derived / "normalized.md").is_file())
        self.assertTrue((derived / "normalized.txt").is_file())
        metadata = json.loads((derived / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["status"], "complete")
        self.assertEqual(metadata["extractor_version"], document_uploads.CURRENT_EXTRACTOR_VERSION)

    def test_claim_atomicity_and_stale_lease_recovery(self):
        self._register("UPL-dddddddddddddddddddddddddddddddd", "lease.txt", b"lease", "text/plain")
        first = document_uploads.claim_processing_job("worker-a", lease_seconds=1)
        self.assertIsNotNone(first)
        second = document_uploads.claim_processing_job("worker-b")
        self.assertIsNone(second)

        with document_uploads._db() as conn:
            conn.execute(
                "UPDATE docops_upload_jobs SET status='running', lease_expires_at='1970-01-01 00:00:00' "
                "WHERE job_id=?",
                (first["job_id"],),
            )
        reclaimed = document_uploads.claim_processing_job("worker-c")
        self.assertIsNotNone(reclaimed)
        self.assertEqual(reclaimed["job_id"], first["job_id"])
        self.assertGreaterEqual(reclaimed["attempts"], 2)

    def test_retry_and_terminal_failure_for_malformed_pdf(self):
        self._register(
            "UPL-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            "bad.pdf",
            b"%PDF-1.4\nnot real",
            "application/pdf",
        )
        with document_uploads._db() as conn:
            conn.execute("UPDATE docops_upload_jobs SET max_attempts=2")

        first = document_uploads.process_next_upload_job("worker-r")
        self.assertIsNotNone(first)
        self.assertIn(first["status"], {"retry", "failed"})
        with document_uploads._db() as conn:
            conn.execute("UPDATE docops_upload_jobs SET next_attempt_at='1970-01-01 00:00:00'")
        second = document_uploads.process_next_upload_job("worker-r")
        self.assertIsNotNone(second)
        self.assertEqual(second["status"], "failed")

    def test_libreoffice_unavailable_sets_actionable_status(self):
        self._register(
            "UPL-ffffffffffffffffffffffffffffffff",
            "legacy.doc",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1dummy",
            "application/msword",
        )
        with patch.object(document_uploads.shutil, "which", return_value=None):
            result = document_uploads.process_next_upload_job("worker-lo")
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "failed")
        with document_uploads._db() as conn:
            extraction = conn.execute(
                "SELECT status, warnings_json FROM docops_upload_extractions LIMIT 1"
            ).fetchone()
        self.assertEqual(extraction["status"], "unavailable")
        warnings = json.loads(extraction["warnings_json"])
        self.assertIn("LibreOffice is unavailable", warnings[0])

    def test_libreoffice_timeout_records_error_status(self):
        self._register(
            "UPL-12121212121212121212121212121212",
            "timeout.doc",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1dummy",
            "application/msword",
        )
        with document_uploads._db() as conn:
            conn.execute("UPDATE docops_upload_jobs SET max_attempts=1")
        with (
            patch.object(document_uploads.shutil, "which", return_value="/usr/bin/libreoffice"),
            patch.object(
                document_uploads.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd=["libreoffice"], timeout=20),
            ),
        ):
            result = document_uploads.process_next_upload_job("worker-lo-timeout")
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "failed")
        with document_uploads._db() as conn:
            extraction = conn.execute(
                "SELECT status, error_detail FROM docops_upload_extractions LIMIT 1"
            ).fetchone()
        self.assertEqual(extraction["status"], "error")
        self.assertIn("timed out", extraction["error_detail"])

    def test_processing_does_not_open_network_connections(self):
        self._register(
            "UPL-34343434343434343434343434343434", "safe.txt", b"local only", "text/plain"
        )
        with patch.object(
            socket.socket, "connect", side_effect=AssertionError("network call blocked")
        ):
            result = document_uploads.run_upload_processor_once("worker-network")
        self.assertEqual(result["completed"], 1)

    def test_retry_failed_processing_job_is_idempotent(self):
        registered = self._register(
            "UPL-56565656565656565656565656565656",
            "retry.txt",
            b"retry me",
            "text/plain",
            session_id="upload_anon_56565656",
        )
        document_id = registered["documents"][0]["document_id"]
        with document_uploads._db() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS chat_sessions (id TEXT PRIMARY KEY, title TEXT, channel TEXT)"
            )
            conn.execute(
                "INSERT INTO chat_sessions (id, title, channel) VALUES (?,?,?)",
                ("session-real-123", "session", "web"),
            )
        link = document_uploads.link_upload_session_to_chat_session(
            chat_session_id="session-real-123",
            source_session_id=registered["documents"][0]["session_id"],
            limit=20,
        )
        self.assertGreaterEqual(link["linked"], 1)
        with document_uploads._db() as conn:
            conn.execute(
                "UPDATE docops_upload_jobs SET status='failed', attempts=max_attempts, last_error='fail' "
                "WHERE document_id=?",
                (document_id,),
            )
        first = document_uploads.retry_uploaded_document_processing(
            session_id="session-real-123",
            document_id=document_id,
        )
        self.assertTrue(first["retried"])
        self.assertEqual(first["queue_state"], "queued")
        second = document_uploads.retry_uploaded_document_processing(
            session_id="session-real-123",
            document_id=document_id,
        )
        self.assertFalse(second["retried"])
        self.assertEqual(second["queue_state"], "queued")


if __name__ == "__main__":
    unittest.main()
