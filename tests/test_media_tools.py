"""Offline unit tests for container artifacts, vision, codex, and image persistence."""

import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops.docs.container_artifacts import fetch_container_artifacts
from ops.images.persist_generated import persist_generated_images
from ops.images.vision import analyze_uploaded_image
from ops.shared.codexops import run_codex_task


class TestContainerArtifacts(unittest.TestCase):
    def test_rejects_malformed_container_id(self):
        for bad in ("", "nope", "cntr_!!", "file-abc"):
            self.assertIn("error", fetch_container_artifacts(bad))


class TestVision(unittest.TestCase):
    def test_unknown_upload_returns_registry_error(self):
        result = analyze_uploaded_image("UPL-" + "0" * 32)
        self.assertIn("error", result)

    def test_non_raster_extension_is_rejected(self):
        with patch(
            "ops.docs.uploads.get_uploaded_document",
            return_value={"saved_path": "uploads/s/UPL-x/report.pdf", "name": "report.pdf"},
        ):
            result = analyze_uploaded_image("UPL-" + "0" * 32)
        self.assertIn("not a supported image format", result["error"])

    def test_multi_file_upload_lists_paths(self):
        with patch(
            "ops.docs.uploads.get_uploaded_document",
            return_value={
                "documents": [
                    {"saved_path": "uploads/s/UPL-x/a.png"},
                    {"saved_path": "uploads/s/UPL-x/b.png"},
                ]
            },
        ):
            result = analyze_uploaded_image("UPL-" + "0" * 32)
        self.assertIn("saved_path", result["error"])
        self.assertEqual(len(result["documents"]), 2)


class TestCodex(unittest.TestCase):
    def test_empty_prompt_and_bad_sandbox_are_rejected(self):
        self.assertIn("error", run_codex_task(""))
        self.assertIn("error", run_codex_task("review this", sandbox="danger-full-access"))


class TestPersistGeneratedImages(unittest.TestCase):
    def test_no_image_items_is_a_noop(self):
        self.assertEqual(persist_generated_images({"output": []}), [])
        self.assertEqual(persist_generated_images({"output": [{"type": "message"}]}), [])

    def test_invalid_base64_is_skipped_without_error(self):
        response = {"output": [{"type": "image_generation_call", "result": "!!not-b64!!"}]}
        self.assertEqual(persist_generated_images(response), [])

    def test_valid_image_is_registered(self):
        import tempfile

        from ops.docs import uploads as document_uploads

        with tempfile.TemporaryDirectory() as temp:
            import docops

            old_db, old_dir = docops._DB_PATH, document_uploads.UPLOADS_DIR
            docops._DB_PATH = Path(temp) / "test.sqlite3"
            document_uploads.UPLOADS_DIR = Path(temp) / "uploads"
            document_uploads.UPLOADS_DIR.mkdir()
            try:
                response = {
                    "output": [
                        {
                            "type": "image_generation_call",
                            "result": base64.b64encode(b"\x89PNGfakebytes").decode(),
                            "revised_prompt": "a test",
                        }
                    ]
                }
                records = persist_generated_images(response)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["revised_prompt"], "a test")
                saved = document_uploads.UPLOADS_DIR / Path(
                    *Path(records[0]["saved_path"]).parts[1:]
                )
                self.assertTrue(saved.is_file())
            finally:
                docops._DB_PATH = old_db
                document_uploads.UPLOADS_DIR = old_dir


if __name__ == "__main__":
    unittest.main()


class TestSpreadsheetExtraction(unittest.TestCase):
    def test_xlsm_classifies_as_spreadsheet_and_previews_values(self):
        import tempfile

        from openpyxl import Workbook

        from ops.docs.extraction import extract_preview
        from ops.docs.formats import classify

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.xlsm"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Underwriting"
            sheet.append(["Metric", "Value"])
            sheet.append(["LTV", 0.72])
            workbook.save(path)

            head = path.read_bytes()[:8]
            classification = classify("model.xlsm", head, path.stat().st_size)
            self.assertEqual(classification.spec.family.value, "spreadsheet")
            self.assertEqual(classification.spec.handling, "extract")

            preview = extract_preview(path, classification)
            self.assertIn("Underwriting", preview)
            self.assertIn("LTV", preview)
            self.assertIn("macros not executed", preview)


class TestCodexArtifacts(unittest.TestCase):
    def test_empty_prompt_rejected(self):
        from ops.shared.codexops import codex_generate_artifact

        self.assertIn("error", codex_generate_artifact(""))

    def test_workspace_outputs_register_as_uploads(self):
        import tempfile

        from ops.docs import uploads as document_uploads
        from ops.shared.codexops import _register_workspace_outputs

        with tempfile.TemporaryDirectory() as temp:
            import docops

            old_db, old_dir = docops._DB_PATH, document_uploads.UPLOADS_DIR
            docops._DB_PATH = Path(temp) / "test.sqlite3"
            document_uploads.UPLOADS_DIR = Path(temp) / "uploads"
            document_uploads.UPLOADS_DIR.mkdir()
            workspace = Path(temp) / "scratch"
            workspace.mkdir()
            (workspace / "diagram.svg").write_text("<svg/>")
            (workspace / ".hidden").write_text("skip me")
            try:
                docs = _register_workspace_outputs(workspace, "codexgen_test1")
                self.assertEqual(len(docs), 1)
                self.assertEqual(docs[0]["name"], "diagram.svg")
            finally:
                docops._DB_PATH = old_db
                document_uploads.UPLOADS_DIR = old_dir
