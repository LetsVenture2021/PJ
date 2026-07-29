import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import docops
import realtime_server
from ops.docs import uploads as document_uploads


class TestDocumentUploads(unittest.TestCase):
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
        docops._DB_PATH = root / "test.sqlite3"
        docops.DOCS_DIR = root / "documents"
        document_uploads.UPLOADS_DIR = docops.DOCS_DIR / "uploads"
        docops.EXPORTS_DIR = docops.DOCS_DIR / "exports"
        docops.ARTIFACTS_DIR = docops.EXPORTS_DIR / ".artifacts"
        document_uploads.UPLOADS_DIR.mkdir(parents=True)
        docops.EXPORTS_DIR.mkdir()
        docops.ARTIFACTS_DIR.mkdir()

        self.env = patch.dict(
            os.environ,
            {"PJ_TOOL_BRIDGE_TOKEN": "bridge-secret"},
            clear=False,
        )
        self.env.start()
        self.old_upload_config = {
            "MAX_UPLOAD_FILE_BYTES": realtime_server.app.config["MAX_UPLOAD_FILE_BYTES"],
            "MAX_UPLOAD_TOTAL_BYTES": realtime_server.app.config["MAX_UPLOAD_TOTAL_BYTES"],
            "UPLOAD_SCANNER": realtime_server.app.config["UPLOAD_SCANNER"],
        }
        realtime_server.app.config.update(
            TESTING=True,
            MAX_UPLOAD_FILE_BYTES=1024,
            MAX_UPLOAD_TOTAL_BYTES=4096,
            UPLOAD_SCANNER=None,
        )
        self.client = realtime_server.app.test_client()
        self.auth = {
            "Authorization": "Bearer bridge-secret",
            "x-pj-session-id": "session-test-123",
        }

    def tearDown(self):
        realtime_server.app.config.update(self.old_upload_config)
        self.env.stop()
        for name, value in self.old_docops.items():
            setattr(docops, name, value)
        document_uploads.UPLOADS_DIR = self.old_uploads_dir
        self.temp_dir.cleanup()

    def _upload(self, endpoint, files, paths=None):
        data = {
            "session_id": "session-test-123",
            "files": [(io.BytesIO(content), name, mime) for name, content, mime in files],
        }
        if paths is not None:
            data["paths"] = paths
        return self.client.post(
            endpoint,
            data=data,
            headers=self.auth,
            content_type="multipart/form-data",
        )

    def _persisted_files(self):
        return [path for path in document_uploads.UPLOADS_DIR.rglob("*") if path.is_file()]

    def test_single_file_upload_is_persisted_and_registered(self):
        response = self._upload(
            "/upload/files",
            [("brief.md", b"# Brief\n\nUploaded source.", "text/markdown")],
            ["brief.md"],
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["count"], 1)
        metadata = payload["files"][0]
        self.assertEqual(metadata["name"], "brief.md")
        self.assertEqual(metadata["size"], 25)
        self.assertEqual(metadata["mime"], "text/markdown")
        self.assertTrue((docops.DOCS_DIR / metadata["saved_path"]).is_file())

        registered = document_uploads.list_uploaded_documents(session_id="session-test-123")
        self.assertEqual(registered["count"], 1)
        preview = document_uploads.get_uploaded_document(payload["upload_id"])
        self.assertIn("Uploaded source.", preview["content"])

    def test_multiple_file_upload(self):
        response = self._upload(
            "/upload/files",
            [
                ("one.txt", b"one", "text/plain"),
                ("two.json", b'{"two": 2}', "application/json"),
            ],
            ["one.txt", "two.json"],
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["count"], 2)
        self.assertEqual({item["name"] for item in payload["files"]}, {"one.txt", "two.json"})
        self.assertEqual(len(self._persisted_files()), 2)

    def test_folder_upload_preserves_nested_paths(self):
        response = self._upload(
            "/upload/folder",
            [
                ("readme.md", b"# Project", "text/markdown"),
                ("data.csv", b"id,value\n1,a\n", "text/csv"),
            ],
            ["project/readme.md", "project/nested/data.csv"],
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        saved_paths = {item["saved_path"] for item in payload["files"]}
        self.assertIn(
            f"uploads/session-test-123/{payload['upload_id']}/project/nested/data.csv",
            saved_paths,
        )
        for saved_path in saved_paths:
            self.assertTrue((docops.DOCS_DIR / saved_path).is_file())

    def test_disallowed_file_type_is_rejected(self):
        response = self._upload(
            "/upload/files",
            [("payload.sh", b"#!/bin/sh\necho unsafe\n", "application/x-sh")],
            ["payload.sh"],
        )

        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.get_json()["error"]["code"], "disallowed_file_type")
        self.assertEqual(self._persisted_files(), [])

    def test_disguised_binary_document_is_rejected(self):
        response = self._upload(
            "/upload/files",
            [("fake.pdf", b"not actually a pdf", "application/octet-stream")],
            ["fake.pdf"],
        )

        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_document_content")
        self.assertEqual(self._persisted_files(), [])

    def test_binary_content_after_text_prefix_is_rejected(self):
        realtime_server.app.config.update(
            MAX_UPLOAD_FILE_BYTES=8192,
            MAX_UPLOAD_TOTAL_BYTES=8192,
        )
        response = self._upload(
            "/upload/files",
            [("binary.txt", b"a" * 4096 + b"\x00hidden", "text/plain")],
            ["binary.txt"],
        )

        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_text_content")
        self.assertEqual(self._persisted_files(), [])

    def test_oversized_file_and_total_are_rejected(self):
        realtime_server.app.config.update(
            MAX_UPLOAD_FILE_BYTES=4,
            MAX_UPLOAD_TOTAL_BYTES=100,
        )
        per_file = self._upload(
            "/upload/files",
            [("large.txt", b"12345", "text/plain")],
            ["large.txt"],
        )
        self.assertEqual(per_file.status_code, 413)
        self.assertEqual(per_file.get_json()["error"]["code"], "file_too_large")

        realtime_server.app.config.update(
            MAX_UPLOAD_FILE_BYTES=100,
            MAX_UPLOAD_TOTAL_BYTES=5,
        )
        total = self._upload(
            "/upload/files",
            [
                ("one.txt", b"123", "text/plain"),
                ("two.txt", b"456", "text/plain"),
            ],
            ["one.txt", "two.txt"],
        )
        self.assertEqual(total.status_code, 413)
        self.assertEqual(total.get_json()["error"]["code"], "upload_too_large")
        self.assertEqual(self._persisted_files(), [])

    def test_path_traversal_is_rejected(self):
        response = self._upload(
            "/upload/folder",
            [("secret.txt", b"nope", "text/plain")],
            ["project/../../secret.txt"],
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "unsafe_upload_path")
        self.assertEqual(self._persisted_files(), [])

    def test_scanner_hook_can_reject_an_upload(self):
        realtime_server.app.config["UPLOAD_SCANNER"] = lambda path, metadata: {
            "ok": False,
            "detail": "test signature",
        }
        response = self._upload(
            "/upload/files",
            [("scan.txt", b"scan me", "text/plain")],
            ["scan.txt"],
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error"]["code"], "upload_scan_rejected")
        self.assertEqual(self._persisted_files(), [])

    def test_registry_failure_removes_persisted_files(self):
        with patch.object(
            document_uploads,
            "register_uploaded_documents",
            side_effect=sqlite3.OperationalError("registry unavailable"),
        ):
            response = self._upload(
                "/upload/files",
                [("orphan.txt", b"do not retain", "text/plain")],
                ["orphan.txt"],
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"]["code"], "upload_failed")
        self.assertEqual(self._persisted_files(), [])

    def test_framework_request_limit_rejects_large_multipart_body(self):
        realtime_server.app.config.update(
            MAX_UPLOAD_FILE_BYTES=2 * 1024 * 1024,
            MAX_UPLOAD_TOTAL_BYTES=5,
        )
        response = self._upload(
            "/upload/files",
            [("large.txt", b"x" * (1024 * 1024 + 32), "text/plain")],
            ["large.txt"],
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["error"]["code"], "upload_too_large")
        self.assertEqual(self._persisted_files(), [])

    def test_early_rejection_emits_upload_audit_event(self):
        with patch.object(realtime_server, "_upload_audit") as audit:
            response = self.client.post(
                "/upload/files",
                data={"session_id": "session-test-123"},
                headers=self.auth,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 400)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[0], "upload.rejected")
        self.assertEqual(audit.call_args.kwargs["error_code"], "missing_upload_files")


if __name__ == "__main__":
    unittest.main()
