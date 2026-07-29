import sqlite3
import tempfile
import unittest
from pathlib import Path

from ops.conversations.handoff import HandoffStore


class HandoffStoreTests(unittest.TestCase):
    def test_token_is_hashed_scoped_authorized_and_single_use(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "handoff.db"
            store = HandoffStore(path)
            token = store.issue("owner-a", "session-12345678", now=100, ttl_seconds=30)
            with sqlite3.connect(path) as conn:
                stored = conn.execute("SELECT token_hash FROM conversation_handoffs").fetchone()[0]
            self.assertNotEqual(stored, token)
            self.assertIsNone(store.redeem(token, "owner-b", authorized=True, now=101))
            self.assertIsNone(store.redeem(token, "owner-a", authorized=False, now=101))
            self.assertEqual(
                store.redeem(token, "owner-a", authorized=True, now=101).conversation_id,
                "session-12345678",
            )
            self.assertIsNone(store.redeem(token, "owner-a", authorized=True, now=102))

    def test_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HandoffStore(Path(directory) / "handoff.db")
            token = store.issue("owner", "conversation", now=100, ttl_seconds=1)
            self.assertIsNone(store.redeem(token, "owner", authorized=True, now=102))
