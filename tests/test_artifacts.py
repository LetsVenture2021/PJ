import hashlib
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from PIL import Image

from ops.artifacts import ArtifactError, ArtifactFacade, OutcomeRecord, RevisionRequest
from ops.artifacts.service import register_revision_router


class ArtifactFacadeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.facade = ArtifactFacade(self.root / "test.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def register(self, name="one.md", content=b"hello", **links):
        path = self.root / name
        path.write_bytes(content)
        return self.facade.register(path=path, domain="docs", source_version=name, **links)

    def test_legacy_file_is_facaded_without_moving_or_copying(self):
        artifact = self.register()
        self.assertEqual(artifact.content_hash, hashlib.sha256(b"hello").hexdigest())
        self.assertEqual((self.root / "one.md").read_bytes(), b"hello")

    def test_hash_mismatch_missing_tombstone_and_authorization(self):
        artifact = self.register(session_id="session-one")
        with self.assertRaises(ArtifactError):
            self.facade.get(artifact.artifact_id, project_id=None, session_id="session-two")
        (self.root / "one.md").write_text("changed")
        with self.assertRaisesRegex(ArtifactError, "integrity"):
            self.facade.verified_path(
                artifact.artifact_id, project_id=None, session_id="session-one"
            )
        (self.root / "one.md").unlink()
        with self.assertRaisesRegex(ArtifactError, "unavailable"):
            self.facade.verified_path(
                artifact.artifact_id, project_id=None, session_id="session-one"
            )
        second = self.register("two.md", b"two")
        self.facade.tombstone(second.artifact_id, project_id=None, session_id=None)
        with self.assertRaisesRegex(ArtifactError, "tombstoned"):
            self.facade.verified_path(second.artifact_id, project_id=None, session_id=None)

    def test_text_compare_never_changes_prior(self):
        one = self.register()
        two = self.register("two.md", b"hello\nworld")
        result = self.facade.compare(
            one.artifact_id, two.artifact_id, project_id=None, session_id=None
        )
        self.assertEqual(result["kind"], "text_diff")
        self.assertEqual((self.root / "one.md").read_bytes(), b"hello")

    def test_image_preview_and_validation(self):
        path = self.root / "pixel.png"
        Image.new("RGB", (12, 8), "red").save(path)
        artifact = self.facade.register(path=path, domain="images", source_version="1")
        result = self.facade.preview(artifact.artifact_id, project_id=None, session_id=None)
        self.assertEqual(result["preview"]["width"], 12)
        self.assertEqual(
            self.facade.validate(
                artifact.artifact_id, project_id=None, session_id=None
            ).verification_status,
            "verified",
        )

    def test_spreadsheet_and_presentation_compare_adapters(self):
        def package(name, member, text):
            path = self.root / name
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(member, f"<root><v>{text}</v></root>")
            return path

        x1 = package("a.xlsx", "xl/worksheets/sheet1.xml", "=A1+1")
        x2 = package("b.xlsx", "xl/worksheets/sheet1.xml", "=A1+2")
        a = self.facade.register(path=x1, domain="docs", source_version="sheet-1")
        b = self.facade.register(path=x2, domain="docs", source_version="sheet-2")
        self.assertEqual(
            self.facade.compare(a.artifact_id, b.artifact_id, project_id=None, session_id=None)[
                "kind"
            ],
            "cell_formula_diff",
        )
        p1 = package("a.pptx", "ppt/slides/slide1.xml", "First")
        p2 = package("b.pptx", "ppt/slides/slide1.xml", "Second")
        a = self.facade.register(path=p1, domain="presentations", source_version="deck-1")
        b = self.facade.register(path=p2, domain="presentations", source_version="deck-2")
        self.assertTrue(
            self.facade.compare(a.artifact_id, b.artifact_id, project_id=None, session_id=None)[
                "thumbnail_changed"
            ]
        )

    def test_targeted_revision_routes_and_is_idempotent(self):
        original = self.register()
        calls = []

        def router(request, descriptor):
            calls.append(request)
            path = self.root / "revised.md"
            path.write_text("revised")
            return self.facade.register(
                path=path,
                domain="docs",
                source_version="2",
                lineage_parents=(descriptor.artifact_id,),
            ).artifact_id

        register_revision_router("docs", router)
        request = RevisionRequest(
            original.artifact_id, "one.md", "Summary", "Clarify", ("links",), "key-1"
        )
        first = self.facade.revise(request, project_id=None, session_id=None)
        second = self.facade.revise(request, project_id=None, session_id=None)
        self.assertEqual(first.artifact_id, second.artifact_id)
        self.assertEqual(len(calls), 1)
        with self.assertRaisesRegex(ArtifactError, "another revision"):
            self.facade.revise(
                replace(request, instruction="Different"), project_id=None, session_id=None
            )

    def test_outcome_schema_excludes_prompt_payloads(self):
        outcome = OutcomeRecord("OUT-1", "Complete", created_at="now", session_id="session-one")
        self.facade.record_outcome(outcome)
        stored = self.facade.get_outcome("OUT-1", project_id=None, session_id="session-one")
        self.assertNotIn("prompt", stored)
        self.assertNotIn("tool_payload", stored)


if __name__ == "__main__":
    unittest.main()
