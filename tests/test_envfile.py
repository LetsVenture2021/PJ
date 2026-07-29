import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops.shared import envfile


class TestEnvPlaceholders(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp.name) / ".env"
        self.patch = patch.object(envfile, "ENV_PATH", self.env_path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_creates_empty_placeholder_for_allowlisted_name(self):
        result = envfile.create_env_placeholder("HF_TOKEN")

        self.assertEqual(result["status"], "created")
        content = self.env_path.read_text(encoding="utf-8")
        self.assertIn("HF_TOKEN=\n", content)
        self.assertEqual(self.env_path.stat().st_mode & 0o777, 0o600)

    def test_existing_variable_is_reported_without_reading_value(self):
        self.env_path.write_text("HF_TOKEN=super-secret\n", encoding="utf-8")
        result = envfile.create_env_placeholder("HF_TOKEN")

        self.assertEqual(result["status"], "exists")
        self.assertNotIn("super-secret", str(result))

    def test_non_allowlisted_and_malformed_names_are_rejected(self):
        self.assertIn("error", envfile.create_env_placeholder("PATH"))
        self.assertIn("error", envfile.create_env_placeholder("lowercase"))
        self.assertIn("error", envfile.create_env_placeholder("A; rm -rf /"))
        self.assertFalse(self.env_path.exists())

    def test_open_env_file_never_returns_contents(self):
        self.env_path.write_text("HF_TOKEN=super-secret\n", encoding="utf-8")
        with patch.object(envfile.subprocess, "run") as run:
            result = envfile.open_env_file()

        run.assert_called_once()
        self.assertEqual(result["status"], "opened")
        self.assertNotIn("super-secret", str(result))

    def test_open_env_file_requires_existing_file(self):
        self.assertIn("error", envfile.open_env_file())

    def test_tools_are_approval_gated(self):
        import json

        policy = json.loads(
            (Path(__file__).resolve().parent.parent / "tool_policy.json").read_text()
        )
        self.assertEqual(policy["tools"]["create_env_placeholder"], "approval")
        self.assertEqual(policy["tools"]["open_env_file"], "approval")


if __name__ == "__main__":
    unittest.main()
