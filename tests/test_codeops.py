import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import codeops  # noqa: E402
import skills  # noqa: E402


class CodeOpsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.db = Path(self.temp.name) / "codeops.sqlite3"
        self.old_db = codeops._DB_PATH
        codeops._DB_PATH = self.db
        self.old_roots = os.environ.get("PJ_CODEOPS_ALLOWED_ROOTS")
        os.environ["PJ_CODEOPS_ALLOWED_ROOTS"] = str(self.root)

    def tearDown(self):
        codeops._DB_PATH = self.old_db
        if self.old_roots is None:
            os.environ.pop("PJ_CODEOPS_ALLOWED_ROOTS", None)
        else:
            os.environ["PJ_CODEOPS_ALLOWED_ROOTS"] = self.old_roots
        self.temp.cleanup()

    def _task(self, checks=None):
        return codeops.create_codeops_task(
            objective="Verify the governed tools",
            repo_root=str(self.root),
            branch="test",
            scope=["tests"],
            acceptance_criteria=["checks pass"],
            required_checks=checks or [],
        )

    def test_traversal_outside_and_secret_paths_are_rejected(self):
        (self.root / "safe.txt").write_text("safe")
        (self.root / ".env").write_text("TOKEN=not-returned")
        with self.assertRaisesRegex(ValueError, "traversal"):
            codeops.read_codeops_file(str(self.root), "../outside.txt")
        with self.assertRaisesRegex(ValueError, "secret"):
            codeops.read_codeops_file(str(self.root), ".env")
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("outside")
        (self.root / "escape.txt").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "escapes"):
            codeops.read_codeops_file(str(self.root), "escape.txt")

    def test_read_and_search_output_caps(self):
        (self.root / "large.txt").write_text("needle " + ("x" * 60_000))
        read = codeops.read_codeops_file(
            str(self.root), "large.txt", max_chars=100
        )
        self.assertLessEqual(len(read["content"].encode()), 100)
        self.assertTrue(read["truncated"])
        for number in range(5):
            (self.root / f"match-{number}.txt").write_text("needle\n")
        found = codeops.search_codeops_repository(
            str(self.root), "needle", max_results=2
        )
        self.assertEqual(found["count"], 2)
        self.assertTrue(found["result_limit_reached"])

    def test_subprocess_output_hard_cap(self):
        result = codeops._run(
            [
                sys.executable,
                "-c",
                "import os; os.write(1, b'x' * 1100000)",
            ],
            self.root,
            timeout=30,
        )
        self.assertTrue(result["output_limit_exceeded"])
        self.assertLessEqual(
            len(result["output"].encode("utf-8")),
            codeops._OUTPUT_CAP,
        )

    def test_command_allowlisting_and_validation_audit(self):
        tests_dir = self.root / "tests"
        tests_dir.mkdir()
        original = self.root / "original-only.txt"
        original.write_text("must not be readable from validation")
        (tests_dir / "test_ok.py").write_text(
            "import unittest\n"
            "from pathlib import Path\n\n"
            f"ORIGINAL = Path({str(original)!r})\n\n"
            "class TestOK(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        Path('snapshot-only.txt').write_text('isolated')\n"
            "        with self.assertRaises(PermissionError):\n"
            "            ORIGINAL.read_text()\n"
            "        self.assertTrue(True)\n"
        )
        task = self._task(["tests"])
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            codeops.run_codeops_validation(task["task_id"], "rm -rf")
        codeops.approve_codeops_task(task["task_id"], "test approval")
        if not Path("/usr/bin/sandbox-exec").is_file():
            with self.assertRaisesRegex(RuntimeError, "sandbox"):
                codeops.run_codeops_validation(
                    task["task_id"], "tests", timeout_seconds=30
                )
            return
        result = codeops.run_codeops_validation(
            task["task_id"], "tests", timeout_seconds=30
        )
        self.assertEqual(result["status"], "passed", result)
        self.assertIsInstance(result["command"], list)
        self.assertFalse((self.root / "snapshot-only.txt").exists())
        fetched = codeops.get_codeops_task(task["task_id"], include_audit=True)
        self.assertTrue(any(
            event["action"] == "deterministic_validation"
            for event in fetched["audit_events"]
        ))
        codeops.record_codeops_completion(
            task["task_id"], "validated completion"
        )
        with self.assertRaisesRegex(ValueError, "terminal"):
            codeops.record_codeops_completion(
                task["task_id"], "failed closure", status="failed"
            )

    def test_completion_rejects_stale_validation(self):
        tests_dir = self.root / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_ok.py"
        test_file.write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n"
        )
        task = self._task(["tests"])
        codeops.approve_codeops_task(task["task_id"], "test approval")
        if not Path("/usr/bin/sandbox-exec").is_file():
            self.skipTest("sandbox-exec is required for execution validation")
        result = codeops.run_codeops_validation(task["task_id"], "tests", 30)
        self.assertEqual(result["status"], "passed")
        test_file.write_text(test_file.read_text() + "\n# changed\n")
        with self.assertRaisesRegex(ValueError, "lack passing persisted runs"):
            codeops.record_codeops_completion(
                task["task_id"], "should be rejected"
            )

    def test_check_discovery_rejects_symlinked_configuration(self):
        outside = Path(self.temp.name) / "outside-requirements.txt"
        outside.write_text("pytest\n")
        (self.root / "requirements.txt").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            codeops.inspect_codeops_repository(str(self.root))

    def test_builtin_corpus_has_eight_complete_guides(self):
        listed = codeops.list_codeops_guides()
        self.assertEqual(listed["count"], 8)
        for guide in listed["guides"]:
            self.assertTrue(guide["source_url"])
            self.assertTrue(guide["content_hash"])
            self.assertTrue(guide["appropriate_tasks"])
            self.assertTrue(guide["workflow"])
            self.assertTrue(guide["safety_controls"])
            self.assertTrue(guide["prompt_contract"])
            self.assertTrue(guide["output_contract"])
            self.assertTrue(guide["checklist"])
            self.assertTrue(guide["authoritative_sources"])
            self.assertTrue(guide["requires_current_docs_check"])
        retrieved = codeops.retrieve_codeops_guidance(
            "frontend user interface", allow_historical=True
        )
        self.assertTrue(retrieved["guides"][0]["current_docs_override_required"])
        self.assertFalse(
            retrieved["guides"][0]["production_configuration_allowed"]
        )

    def test_import_parser_preserves_metadata(self):
        corpus = """\
---ITEM_START: DOC-X---
```yaml
item_id: "DOC-X"
source_page_url: "https://example.test/source"
canonical_title: "Example Guide"
tool_family: "Example"
surface: "CLI"
version_scope: "historical"
corpus_status: "training_ready_current_docs_override"
requires_current_docs_check: true
content_sha256: "abc123"
```
**What this item teaches:** Safe examples.
#### Appropriate tasks
- inspect code
#### Recommended operating workflow
1. Inspect.
#### Safety and governance controls
- Least privilege.
#### Prompt contract
State the objective.
#### Output contract
Return evidence.
#### Evaluation checklist
- [ ] Evidence exists.
#### Current authoritative sources
- https://example.test/current
---ITEM_END: DOC-X---
"""
        result = codeops.import_codeops_guidance(
            corpus, historical_context_acknowledged=True
        )
        self.assertEqual(result["record_count"], 1)
        listed = codeops.list_codeops_guides(surface="CLI")
        guide = next(g for g in listed["guides"] if g["item_id"] == "DOC-X")
        self.assertEqual(guide["content_hash"], "abc123")
        self.assertEqual(guide["version_scope"], "historical")

    def test_task_lifecycle_and_completion_learning(self):
        task = self._task()
        self.assertEqual(task["approval_state"], "pending")
        codeops.approve_codeops_task(task["task_id"], "approved in test")
        completed = codeops.record_codeops_completion(
            task["task_id"],
            "Read-only workflow completed.",
            changed_files=[],
            learning_evidence=["Keep validation deterministic."],
        )
        self.assertEqual(completed["status"], "completed")
        self.assertFalse(completed["delegated_to_codex_cloud"])
        fetched = codeops.get_codeops_task(task["task_id"])
        self.assertEqual(fetched["status"], "completed")
        self.assertEqual(
            fetched["learning_evidence"],
            ["Keep validation deterministic."],
        )

    def test_schema_dispatch_and_policy_wiring(self):
        names = {schema["name"] for schema in codeops.CODEOPS_SCHEMAS}
        self.assertEqual(names, set(codeops.CODEOPS_DISPATCH))
        self.assertTrue(names.issubset(skills.DISPATCH_TABLE))
        task = self._task()
        blocked = skills.dispatch("approve_codeops_task", {
            "task_id": task["task_id"],
            "approval_evidence": "policy test",
        })
        self.assertIn("requires explicit approval", blocked["error"])
        untrusted = skills.dispatch("approve_codeops_task", {
            "task_id": task["task_id"],
            "approval_evidence": "policy test",
            "_approved": True,
        })
        self.assertIn("cannot be supplied", untrusted["error"])
        approved = skills.dispatch("approve_codeops_task", {
            "task_id": task["task_id"],
            "approval_evidence": "policy test",
        }, approval_granted=True)
        self.assertEqual(approved["approval_state"], "approved")
        original_policy = skills._TOOL_POLICY_PATH
        try:
            skills._TOOL_POLICY_PATH = Path(self.temp.name) / "missing.json"
            self.assertEqual(
                skills._tool_policy_mode("run_codeops_validation"),
                "approval",
            )
        finally:
            skills._TOOL_POLICY_PATH = original_policy

    def test_sandbox_profile_denies_writes_outside_private_roots(self):
        private_home = Path(self.temp.name) / "private-home"
        private_home.mkdir()
        with patch.object(Path, "is_file", return_value=True):
            prefix, backend = codeops._validation_prefix(
                self.root, private_home
            )
        self.assertEqual(backend, "sandbox-exec")
        profile = prefix[2]
        self.assertIn("(deny file-write* (require-not (require-any", profile)
        self.assertIn(
            f'(subpath "{self.root.resolve()}")', profile
        )
        self.assertIn(
            f'(subpath "{private_home.resolve()}")', profile
        )

    def test_git_evidence_is_bounded(self):
        subprocess.run(
            ["git", "init", "-q"], cwd=self.root, check=True,
            capture_output=True,
        )
        (self.root / "tracked.txt").write_text("initial\n")
        subprocess.run(
            ["git", "add", "tracked.txt"], cwd=self.root, check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
             "commit", "-qm", "initial"],
            cwd=self.root, check=True, capture_output=True,
        )
        (self.root / "tracked.txt").write_text("changed\n")
        subprocess.run(
            ["git", "add", "tracked.txt"], cwd=self.root, check=True,
            capture_output=True,
        )
        evidence = codeops.get_codeops_git_evidence(str(self.root), "diff")
        self.assertIn("changed", evidence["content"])
        self.assertEqual(len(evidence["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
