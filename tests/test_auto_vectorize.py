import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops.docs.auto_vectorize import _ledger_seen


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


if __name__ == "__main__":
    unittest.main()
