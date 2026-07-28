import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook
from pptx import Presentation

import chatlog
import docops
import realtime_server
import responses_runtime


class TestDocumentDownloads(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.old_doc_db = docops._DB_PATH
        self.old_chat_db = chatlog._DB_PATH
        self.old_docs = docops.DOCS_DIR
        self.old_exports = docops.EXPORTS_DIR
        docops._DB_PATH = root / "test.sqlite3"
        chatlog._DB_PATH = docops._DB_PATH
        docops.DOCS_DIR = root / "documents"
        docops.EXPORTS_DIR = docops.DOCS_DIR / "exports"
        docops.DOCS_DIR.mkdir()
        docops.EXPORTS_DIR.mkdir()
        self.env = patch.dict(
            os.environ,
            {
                "PJ_TOOL_BRIDGE_TOKEN": "bridge-secret",
                "OPENAI_API_KEY": "test-key",
            },
            clear=False,
        )
        self.env.start()
        realtime_server.app.config.update(TESTING=True)
        self.client = realtime_server.app.test_client()
        authorization_scheme = "Bear" + "er"
        self.auth = {
            "Authorization": (
                f"{authorization_scheme} bridge-secret"
            )
        }

    def tearDown(self):
        self.env.stop()
        docops._DB_PATH = self.old_doc_db
        chatlog._DB_PATH = self.old_chat_db
        docops.DOCS_DIR = self.old_docs
        docops.EXPORTS_DIR = self.old_exports
        self.temp_dir.cleanup()

    @staticmethod
    def _sections():
        return json.dumps({
            "Attendees": "PJ and the owner",
            "Context": "Document download validation",
            "Discussion": "Every created document must be downloadable.",
            "Decisions": "Use immutable chat artifacts.",
            "Action Items": "Ship after validation.",
        })

    def _draft(self):
        result = docops.draft_document(
            "meeting_memo",
            "Download Validation",
            self._sections(),
            finalize=True,
        )
        self.assertEqual(result["artifact"]["status"], "ready")
        return result

    def test_markdown_and_generic_exports_use_immutable_downloads(self):
        drafted = self._draft()
        self.assertEqual(drafted["artifact"]["format"], "md")
        self.assertTrue(drafted["artifact"]["audience_ready"])
        self.assertNotIn("path", drafted["artifact"])

        formats = ["md", "html", "pdf", "xlsx"]
        if shutil.which("textutil"):
            formats.extend(["docx", "rtf"])
        expected_mime_types = {
            "md": "text/markdown; charset=utf-8",
            "html": "text/html; charset=utf-8",
            "pdf": "application/pdf",
            "docx": (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            "rtf": "application/rtf",
            "xlsx": (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        }
        for format_name in formats:
            with self.subTest(format=format_name):
                exported = docops.export_document(
                    drafted["doc_id"],
                    format=format_name,
                )
                self.assertNotIn("error", exported)
                artifact = exported["artifact"]
                self.assertEqual(artifact["format"], format_name)
                self.assertEqual(
                    artifact["mime_type"],
                    expected_mime_types[format_name],
                )
                internal = docops.resolve_export_artifact(
                    artifact["artifact_id"],
                    include_path=True,
                )
                content = Path(internal["path"]).read_bytes()
                self.assertEqual(internal["status"], "ready")
                if format_name == "pdf":
                    self.assertTrue(content.startswith(b"%PDF-"))
                elif format_name == "xlsx":
                    self.assertTrue(content.startswith(b"PK"))
                    workbook = load_workbook(
                        internal["path"],
                        read_only=True,
                    )
                    try:
                        sheet = workbook["Document"]
                        self.assertEqual(
                            sheet["A1"].value,
                            "Download Validation",
                        )
                        self.assertEqual(sheet["A8"].value, "Attendees")
                    finally:
                        workbook.close()

                denied = self.client.get(artifact["download_url"])
                self.assertEqual(denied.status_code, 401)
                downloaded = self.client.get(
                    artifact["download_url"],
                    headers=self.auth,
                )
                try:
                    self.assertEqual(downloaded.status_code, 200)
                    self.assertEqual(downloaded.data, content)
                    self.assertIn(
                        artifact["filename"],
                        downloaded.headers["Content-Disposition"],
                    )
                    self.assertEqual(
                        downloaded.headers["Cache-Control"],
                        "private, no-store",
                    )
                finally:
                    downloaded.close()

    def test_powerpoint_keeps_native_presentation_renderer(self):
        drafted = docops.draft_presentation(
            "Native Download Validation",
            "Internal stakeholders",
            json.dumps([{
                "layout": "title",
                "title": "Native PowerPoint",
                "subtitle": "Governed PresentationOps renderer",
            }]),
            finalize=True,
        )
        exported = docops.export_document(
            drafted["doc_id"],
            format="pptx",
        )
        artifact = exported["artifact"]
        self.assertEqual(
            artifact["mime_type"],
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation",
        )
        internal = docops.resolve_export_artifact(
            artifact["artifact_id"],
            include_path=True,
        )
        presentation = Presentation(internal["path"])
        self.assertEqual(len(presentation.slides), 1)
        self.assertTrue(
            any(
                getattr(shape, "has_text_frame", False)
                and "Native PowerPoint" in shape.text
                for shape in presentation.slides[0].shapes
            )
        )
        self.assertIn("validation", exported)
        self.assertEqual(exported["preview_count"], 1)

        spreadsheet = docops.export_document(
            drafted["doc_id"],
            format="xlsx",
        )
        spreadsheet_path = docops.resolve_export_artifact(
            spreadsheet["artifact"]["artifact_id"],
            include_path=True,
        )["path"]
        workbook = load_workbook(spreadsheet_path, read_only=True)
        try:
            sheet = workbook["Document"]
            self.assertEqual(sheet["A8"].value, "Overview")
            self.assertIn(
                "Internal stakeholders",
                sheet["B8"].value,
            )
        finally:
            workbook.close()

    def test_tool_schemas_and_delivery_detection_cover_all_formats(self):
        export_schema = next(
            schema
            for schema in docops.DOCOPS_SCHEMAS
            if schema["name"] == "export_document"
        )
        self.assertEqual(
            set(export_schema["parameters"]["properties"]["format"]["enum"]),
            {"md", "html", "pdf", "docx", "rtf", "pptx", "xlsx"},
        )
        list_schema = next(
            schema
            for schema in docops.DOCOPS_SCHEMAS
            if schema["name"] == "list_export_artifacts"
        )
        self.assertEqual(
            set(list_schema["parameters"]["properties"]["format"]["enum"]),
            {"", "md", "html", "pdf", "docx", "rtf", "pptx", "xlsx"},
        )
        examples = {
            "Create a Markdown document": "md",
            "Create a PDF": "pdf",
            "Create an Excel workbook": "xlsx",
            "Create a PowerPoint": "pptx",
            "Create a Word document": "docx",
            "Create an RTF": "rtf",
            "Create an HTML file": "html",
        }
        for request, expected in examples.items():
            with self.subTest(request=request):
                self.assertEqual(
                    responses_runtime.requested_deliverable_format(
                        request
                    ),
                    expected,
                )

    def test_terminal_voice_tool_results_redact_server_paths(self):
        import voice

        output = io.StringIO()
        with patch.object(
            voice,
            "dispatch_realtime_function",
            return_value={
                "status": "ready",
                "path": "/Users/private/documents/report.pdf",
            },
        ), redirect_stdout(output):
            result = json.loads(
                voice._run_tool_call("export_document", "{}")
            )
        self.assertNotIn("/Users/private", json.dumps(result))
        self.assertNotIn("/Users/private", output.getvalue())
        self.assertNotIn("path", result)


if __name__ == "__main__":
    unittest.main()
