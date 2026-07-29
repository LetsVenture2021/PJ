import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops.docs.auto_vectorize import _ledger_seen, vectorize_document_export


class TestVectorIngestLedger(unittest.TestCase):
    def test_ledger_dedupes_by_content_and_store(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "ledger.sqlite3"
            self.assertFalse(_ledger_seen(db, "a" * 64, "vs_1"))
            _ledger_seen(db, "a" * 64, "vs_1", record=True)
            self.assertTrue(_ledger_seen(db, "a" * 64, "vs_1"))
            self.assertFalse(_ledger_seen(db, "a" * 64, "vs_2"))
            self.assertFalse(_ledger_seen(db, "b" * 64, "vs_1"))
            _ledger_seen(db, "a" * 64, "vs_1", record=True)  # idempotent
            count = (
                sqlite3.connect(db)
                .execute("SELECT COUNT(*) FROM docops_vector_ingest_ledger")
                .fetchone()[0]
            )
            self.assertEqual(count, 1)

    def test_vectorized_markdown_is_sent_to_every_configured_store(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "ledger.sqlite3"
            client = MagicMock()
            client.files.create.side_effect = [
                SimpleNamespace(id="file_1"),
                SimpleNamespace(id="file_2"),
            ]
            document = {"content": "# User deliverable\n\nVector-ready text.", "version": 3}
            with (
                patch("ops.docs.service.get_document", return_value=document),
                patch("ops.docs.service._DB_PATH", db),
                patch(
                    "ops.realtime.orchestration.load_config",
                    return_value={"vector_store_ids": ["vs_1", "vs_2"]},
                ),
                patch("openai.OpenAI", return_value=client),
                patch("ops.docs.auto_vectorize._near_duplicate", return_value=False),
            ):
                self.assertTrue(vectorize_document_export("DOC-1", 3))

            self.assertEqual(client.files.create.call_count, 2)
            stores = [call.args[0] for call in client.vector_stores.files.create.call_args_list]
            self.assertEqual(stores, ["vs_1", "vs_2"])
            for call in client.files.create.call_args_list:
                uploaded = call.kwargs["file"]
                self.assertEqual(uploaded.name, "DOC-1_v3.md")
                self.assertEqual(uploaded.getvalue(), document["content"].encode())


if __name__ == "__main__":
    unittest.main()
