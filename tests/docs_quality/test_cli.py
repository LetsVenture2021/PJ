import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from ops.docs.quality import cli


class DocumentQualityCliTests(unittest.TestCase):
    def test_failing_manifest_prints_sanitized_rule_details(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            documents = root / "documents"
            documents.mkdir()
            source = documents / "brief.md"
            source.write_text("TODO: replace before release\n", encoding="utf-8")
            manifest = documents / "library-manifest.json"
            manifest.write_text(
                json.dumps({"documents": [{"path": "documents/brief.md"}]}),
                encoding="utf-8",
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                status = cli.main(["check", "--manifest", str(manifest)])

        text = output.getvalue()
        self.assertEqual(status, 1)
        self.assertIn("FAIL", text)
        self.assertIn("DOC-STRUCT-001 major line 0:0", text)
        self.assertIn("DOC-CONTENT-001 major line 1:1", text)
        self.assertNotIn("replace before release", text)


if __name__ == "__main__":
    unittest.main()
