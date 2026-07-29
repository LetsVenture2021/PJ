import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.skills import website_learning


class _Response:
    status_code = 200
    encoding = "utf-8"
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def iter_content(self, _size):
        yield b"""<html><head><title>Acme Growth</title>
        <meta name='description' content='Turn strategy into measurable results.'></head>
        <body><h1>Make your next outcome happen</h1><h2>Client results</h2>
        <img src='result.png' alt='Revenue chart'><a href='/book'>Book a consultation</a>
        <form></form></body></html>"""


class _Provider:
    request_errors = (RuntimeError,)

    def get(self, *_args, **_kwargs):
        return _Response()


class WebsiteLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            website_learning, "_DB_PATH", Path(self.temp.name) / "learning.sqlite3"
        )
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    @patch.object(website_learning, "_public_destination", return_value=True)
    def test_extracts_and_persists_cross_discipline_patterns(self, _public):
        result = website_learning.learn_from_website(
            "https://example.com", http_provider=_Provider()
        )
        self.assertEqual(result["status"], "learned")
        self.assertTrue(result["persisted"])
        self.assertEqual(set(result["insights"]), {"design", "marketing", "consulting"})
        self.assertTrue(result["insights"]["marketing"][0]["observed"])
        self.assertEqual(result["trust"], "untrusted_external_evidence")

    def test_rejects_private_destination_before_fetch(self):
        result = website_learning.learn_from_website(
            "http://127.0.0.1/admin", http_provider=_Provider()
        )
        self.assertIn("non-public", result["error"])

    def test_rejects_unknown_focus_without_fetching(self):
        result = website_learning.learn_from_website("example.com", focus="sales")
        self.assertIn("focus must be", result["error"])

    @patch.object(website_learning, "_public_destination", return_value=True)
    def test_source_locator_does_not_return_query_secrets(self, _public):
        result = website_learning.learn_from_website(
            "https://example.com/landing?token=sensitive",
            persist=False,
            http_provider=_Provider(),
        )
        self.assertEqual(result["source"]["url"], "https://example.com/landing")
        self.assertNotIn("sensitive", str(result))


if __name__ == "__main__":
    unittest.main()
