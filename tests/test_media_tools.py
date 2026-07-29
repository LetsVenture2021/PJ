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


class TestOfficeExtraction(unittest.TestCase):
    def test_docx_and_pptx_text_extraction(self):
        import tempfile
        import zipfile

        from ops.docs.extraction import extract_preview
        from ops.docs.formats import classify

        ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        doc_xml = f"<w:document {ns}><w:body><w:p><w:r><w:t>Quarterly outlook strong</w:t></w:r></w:p></w:body></w:document>"
        pns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        slide_xml = f'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" {pns}><p:cSld><a:t>Roadmap slide title</a:t></p:cSld></p:sld>'

        with tempfile.TemporaryDirectory() as temp:
            docx = Path(temp) / "memo.docx"
            with zipfile.ZipFile(docx, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("word/document.xml", doc_xml)
            classification = classify("memo.docx", docx.read_bytes()[:8], docx.stat().st_size)
            self.assertEqual(classification.spec.handling, "extract")
            self.assertIn("Quarterly outlook strong", extract_preview(docx, classification))

            pptx = Path(temp) / "deck.pptx"
            with zipfile.ZipFile(pptx, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("ppt/slides/slide1.xml", slide_xml)
            classification = classify("deck.pptx", pptx.read_bytes()[:8], pptx.stat().st_size)
            self.assertIn("Roadmap slide title", extract_preview(pptx, classification))

    def test_spreadsheet_formulas_are_listed(self):
        import tempfile

        from openpyxl import Workbook

        from ops.docs.extraction import extract_preview
        from ops.docs.formats import classify

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "calc.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet["A1"] = 10
            sheet["A2"] = "=A1*2"
            workbook.save(path)
            classification = classify("calc.xlsx", path.read_bytes()[:8], path.stat().st_size)
            preview = extract_preview(path, classification)
            self.assertIn("Formulas", preview)
            self.assertIn("=A1*2", preview)


class TestCitationMarkerStripping(unittest.TestCase):
    def test_raw_citation_glyphs_are_removed(self):
        from ops.realtime.orchestration import strip_citation_markers

        text = "Remote work is allowed.citeturn0file1L8-L13 More text."
        self.assertEqual(strip_citation_markers(text), "Remote work is allowed. More text.")
        self.assertEqual(strip_citation_markers("plain text"), "plain text")
        self.assertIsNone(strip_citation_markers(None))


class TestContainerAutoPersist(unittest.TestCase):
    def test_container_ids_are_collected_and_deduped(self):
        from unittest.mock import patch as _patch

        from ops.docs import container_artifacts

        calls = []

        def fake_fetch(container_id):
            calls.append(container_id)
            return {
                "status": "retrieved",
                "documents": [
                    {"saved_path": f"uploads/x/{container_id}.png", "upload_id": "UPL-x", "size": 1}
                ],
            }

        response = {
            "output": [
                {"type": "code_interpreter_call", "container_id": "cntr_aaa11111"},
                {"type": "code_interpreter_call", "container_id": "cntr_aaa11111"},
                {"type": "shell_call", "environment": {"container_id": "cntr_bbb22222"}},
                {"type": "message"},
            ]
        }
        with _patch.object(container_artifacts, "fetch_container_artifacts", fake_fetch):
            records = container_artifacts.persist_response_containers(response)
        self.assertEqual(calls, ["cntr_aaa11111", "cntr_bbb22222"])
        self.assertEqual(len(records), 2)


class TestDeepResearch(unittest.TestCase):
    def test_start_validates_prompt_and_get_validates_id(self):
        from ops.shared.research import get_deep_research, start_deep_research

        self.assertIn("error", start_deep_research("too short"))
        self.assertIn("error", get_deep_research("bogus"))

    def test_completed_run_persists_report(self):
        import tempfile
        from types import SimpleNamespace

        from ops.docs import uploads as document_uploads
        from ops.shared.research import get_deep_research

        class FakeResponses:
            def retrieve(self, response_id):
                return SimpleNamespace(status="completed", output_text="# Report\n\nFindings here.")

        fake_client = SimpleNamespace(responses=FakeResponses())
        with tempfile.TemporaryDirectory() as temp:
            import docops

            old_db, old_dir = docops._DB_PATH, document_uploads.UPLOADS_DIR
            docops._DB_PATH = Path(temp) / "test.sqlite3"
            document_uploads.UPLOADS_DIR = Path(temp) / "uploads"
            document_uploads.UPLOADS_DIR.mkdir()
            try:
                result = get_deep_research("resp_" + "a" * 20, client=fake_client)
                self.assertEqual(result["status"], "completed")
                self.assertIn("Findings here", result["report"])
                self.assertIn("saved_report", result)
            finally:
                docops._DB_PATH = old_db
                document_uploads.UPLOADS_DIR = old_dir


class TestSemanticMemory(unittest.TestCase):
    def _fake_client(self):
        from types import SimpleNamespace

        def create(model, input, dimensions):
            data = []
            for text in input:
                seed = sum(ord(c) for c in text[:50])
                vec = [((seed * (i + 3)) % 97) / 97.0 for i in range(8)]
                data.append(SimpleNamespace(embedding=vec))
            return SimpleNamespace(data=data)

        return SimpleNamespace(embeddings=SimpleNamespace(create=create))

    def test_search_validates_and_ranks_with_cache(self):
        import sqlite3
        import tempfile

        from ops.shared.semantic_memory import semantic_search_memory

        self.assertIn("error", semantic_search_memory(""))
        self.assertIn("error", semantic_search_memory("x", kinds="bogus"))

        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "mem.sqlite3"
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE notes (id TEXT, topic TEXT, content TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO notes VALUES ('n1', 'lending', 'decided lender terms: 2 points', CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO notes VALUES ('n2', 'travel', 'flight to austin booked', CURRENT_TIMESTAMP)"
            )
            conn.commit()
            result = semantic_search_memory(
                "lender decision", kinds="notes", client=self._fake_client(), db_path=db
            )
            self.assertEqual(result["count"], 2)
            self.assertEqual({m["ref"] for m in result["matches"]}, {"n1", "n2"})
            cached = (
                sqlite3.connect(db).execute("SELECT COUNT(*) FROM semantic_vectors").fetchone()[0]
            )
            self.assertEqual(cached, 2)


class TestPJMcpServer(unittest.TestCase):
    def test_protocol_and_unknown_ids(self):
        import pj_mcp_server

        init = pj_mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init["result"]["serverInfo"]["name"], "pj-knowledge")
        tools = pj_mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(
            [t["name"] for t in tools["result"]["tools"]], ["search", "fetch", "list_open_tasks"]
        )
        self.assertIsNone(
            pj_mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )
        self.assertIn("error", pj_mcp_server._fetch("bogus:zzz"))
        missing = pj_mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})
        self.assertEqual(missing["error"]["code"], -32601)


class TestSiteDeploy(unittest.TestCase):
    def test_validation_and_protected_projects(self):
        from ops.shared.siteops import deploy_generated_site

        self.assertIn("error", deploy_generated_site("UPL-x", "Bad Name!"))
        self.assertIn("protected", deploy_generated_site("UPL-x", "pj-assistant-web")["error"])
        self.assertIn(
            "no registered upload",
            deploy_generated_site("UPL-" + "0" * 32, "pj-test-site")["error"],
        )


class TestDelegationContext(unittest.TestCase):
    def test_context_includes_turns_and_uploads(self):
        from unittest.mock import patch as _patch

        import chatlog
        from ops.docs import uploads as document_uploads
        from ops.realtime.orchestration import _delegation_context

        with (
            _patch.object(
                chatlog,
                "session_detail",
                return_value={
                    "messages": [
                        {"role": "user", "content": "analyze the SOW"},
                        {"role": "assistant", "content": "budget is $172,500 for 5,120 sqft"},
                    ]
                },
            ),
            _patch.object(
                document_uploads,
                "list_uploaded_documents",
                return_value={"documents": [{"name": "SOW.xlsx", "upload_id": "UPL-" + "a" * 32}]},
            ),
        ):
            context = _delegation_context("session_x_12345678")
        self.assertIn("172,500", context)
        self.assertIn("SOW.xlsx", context)
        self.assertIn("CONVERSATION CONTEXT", context)
        self.assertEqual(_delegation_context(None), "")


class TestPdfExtraction(unittest.TestCase):
    def test_pdf_text_is_extracted(self):
        import tempfile

        from pypdf import PdfWriter

        from ops.docs.extraction import extract_preview
        from ops.docs.formats import classify

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "psa.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            writer.write(path)
            classification = classify("psa.pdf", path.read_bytes()[:8], path.stat().st_size)
            self.assertEqual(classification.spec.handling, "extract")
            preview = extract_preview(path, classification)
            self.assertIn("PDF", preview)
            self.assertIn("no extractable text layer", preview)
