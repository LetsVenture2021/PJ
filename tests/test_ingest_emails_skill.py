import json
import subprocess
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".codex/skills/ingest-emails/scripts/normalize_email_export.py"


class IngestEmailsSkillTests(unittest.TestCase):
    def test_normalizes_text_and_skips_attachment_content(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "message.eml"
            output = Path(directory) / "normalized.jsonl"
            message = EmailMessage()
            message["Message-ID"] = "<example@example.test>"
            message["From"] = "Sender <sender@example.test>"
            message["To"] = "recipient@example.test"
            message["Subject"] = "Example"
            message.set_content("safe body")
            message.add_attachment(
                b"attachment must not be indexed",
                maintype="application",
                subtype="octet-stream",
                filename="payload.bin",
            )
            source.write_bytes(message.as_bytes())

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(source), "--output", str(output)],
                check=True,
                capture_output=True,
                text=True,
            )

            summary = json.loads(result.stderr)
            record = json.loads(output.read_text())
            self.assertEqual(summary["written"], 1)
            self.assertEqual(record["trust"], "untrusted_email_content")
            self.assertEqual(record["content"], "safe body")
            self.assertNotIn("attachment must not be indexed", record["content"])

    def test_batch_skips_credential_shaped_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mail"
            source.mkdir()
            (source / "credentials.eml").write_text("Subject: private\n\nsecret")
            output = Path(directory) / "normalized.jsonl"

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(source), "--output", str(output)],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(
                json.loads(result.stderr),
                {"discovered": 1, "duplicates": 0, "skipped": 1, "written": 0},
            )
            self.assertEqual(output.read_text(), "")


if __name__ == "__main__":
    unittest.main()
