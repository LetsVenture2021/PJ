import json
import subprocess
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".codex/skills/ingest-emails/scripts/normalize_email_export.py"


def _write_message(path: Path, body: str, message_id: str = "<example@example.test>") -> None:
    message = EmailMessage()
    message["Message-ID"] = message_id
    message["From"] = "Sender <sender@example.test>"
    message["To"] = "recipient@example.test"
    message["Subject"] = "Example"
    message.set_content(body)
    path.write_bytes(message.as_bytes())


def _run_normalizer(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


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

            result = _run_normalizer(str(source), "--output", str(output))

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stderr)
            record = json.loads(output.read_text())
            self.assertEqual(summary["counts"]["written"], 1)
            self.assertEqual(summary["skipped_sources"], [])
            self.assertEqual(record["trust"], "untrusted_email_content")
            self.assertEqual(record["content"], "safe body")
            self.assertNotIn("attachment must not be indexed", record["content"])

    def test_batch_skips_credential_shaped_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mail"
            source.mkdir()
            (source / "credentials.eml").write_text("Subject: private\n\nsecret")
            output = Path(directory) / "normalized.jsonl"

            result = _run_normalizer(str(source), "--output", str(output))

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stderr)
            self.assertEqual(
                summary["counts"],
                {"discovered": 1, "duplicates": 0, "skipped": 1, "written": 0},
            )
            self.assertEqual(
                summary["skipped_sources"][0]["reason"], "credential-shaped filename refused"
            )
            self.assertEqual(output.read_text(), "")

    def test_rejects_output_inside_source_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mail"
            source.mkdir()
            _write_message(source / "message.eml", "safe body")
            output = source / "normalized.jsonl"

            result = _run_normalizer(str(source), "--output", str(output))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("outside the source directory", result.stderr)
            self.assertFalse(output.exists())

    def test_rejects_unsupported_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "message.txt"
            source.write_text("not an email export")
            output = Path(directory) / "normalized.jsonl"

            result = _run_normalizer(str(source), "--output", str(output))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(".eml or .mbox", result.stderr)
            self.assertFalse(output.exists())

    def test_message_limit_stops_without_marking_remaining_inputs_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mail"
            source.mkdir()
            _write_message(source / "one.eml", "first", "<one@example.test>")
            _write_message(source / "two.eml", "second", "<two@example.test>")
            output = Path(directory) / "normalized.jsonl"

            result = _run_normalizer(
                str(source),
                "--output",
                str(output),
                "--max-messages",
                "1",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stderr)
            self.assertEqual(summary["counts"]["written"], 1)
            self.assertEqual(summary["counts"]["skipped"], 0)
            self.assertTrue(summary["limit_reached"])
            self.assertEqual(len(output.read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
