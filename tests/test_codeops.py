"""Focused stdlib tests for governed CodeOps capabilities."""
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import codeops  # noqa: E402
import skillops  # noqa: E402
import skills  # noqa: E402


class CodeOpsRepositoryTestCase(unittest.TestCase):
    def setUp(self):
        self._scratch = tempfile.TemporaryDirectory(
            dir=BASE_DIR, prefix=".codeops-test-"
        )
        self.scratch = Path(self._scratch.name)
        self.repo = self.scratch / "repository"
        self.repo.mkdir()
        self._old_codeops_db = codeops._DB_PATH
        self._old_skillops_db = skillops._DB_PATH
        codeops._DB_PATH = self.scratch / "codeops-audit.sqlite3"
        skillops._DB_PATH = self.scratch / "skillops-telemetry.sqlite3"
        self._git("init", "-q")
        self._git("config", "user.email", "codeops-tests@example.invalid")
        self._git("config", "user.name", "CodeOps Tests")
        (self.repo / "tracked.txt").write_text("before\n", encoding="utf-8")
        self._git("add", "tracked.txt")
        self._git("commit", "-qm", "initial")

    def tearDown(self):
        codeops._DB_PATH = self._old_codeops_db
        skillops._DB_PATH = self._old_skillops_db
        self._scratch.cleanup()

    def _git(self, *arguments):
        return subprocess.run(
            ["git", *arguments],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )


class TestCodeOpsRegistry(unittest.TestCase):
    def test_every_codeops_schema_dispatches_and_names_are_unique(self):
        names = [schema["name"] for schema in codeops.CODEOPS_SCHEMAS]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            self.assertIn(name, codeops.CODEOPS_DISPATCH)
            self.assertIn(name, skills.DISPATCH_TABLE)
            self.assertTrue(any(
                schema.get("name") == name for schema in skills.TOOL_SCHEMAS
            ))

    def test_apply_edit_is_mandatory_approval_tool(self):
        self.assertEqual(
            skills._tool_policy_mode("apply_codeops_file_edit"), "approval"
        )


class TestCodeOpsKnowledge(unittest.TestCase):
    def test_all_eight_corpus_records_have_freshness_and_citations(self):
        expected = {
            "DOC-415", "DOC-43", "DOC-407", "DOC-405",
            "DOC-400", "DOC-393", "DOC-397", "DOC-399",
        }
        self.assertEqual(
            {record["doc_id"] for record in codeops.CODEOPS_RECORDS}, expected
        )
        for record in codeops.CODEOPS_RECORDS:
            self.assertEqual(record["source"]["corpus_version"], "1.0.0")
            self.assertEqual(record["source"]["corpus_build_date"], "2026-07-28")
            self.assertTrue(record["source"]["requires_current_docs_check"])
            self.assertEqual(len(record["source"]["content_sha256"]), 64)
            self.assertTrue(record["citation"]["url"].startswith("https://"))
            self.assertTrue(record["workflow"])
            self.assertTrue(record["safety_controls"])
            self.assertIn("official", record["historical_model_caveat"].lower())

    def test_doc_id_alias_title_and_topic_lookup(self):
        by_id = codeops.get_codeops_guidance(
            action="inspect", doc_id="doc-405"
        )
        self.assertEqual(by_id["records"][0]["doc_id"], "DOC-405")

        by_alias = codeops.get_codeops_guidance(
            action="inspect", query="codex ci"
        )
        self.assertEqual(by_alias["records"][0]["doc_id"], "DOC-407")

        by_title = codeops.get_codeops_guidance(
            action="inspect", query="Codex IDE extension"
        )
        self.assertEqual(by_title["records"][0]["doc_id"], "DOC-43")

        by_topic = codeops.get_codeops_guidance(
            action="inspect", topic="accessibility"
        )
        self.assertEqual(by_topic["records"][0]["doc_id"], "DOC-397")

    def test_historical_names_are_not_rewritten(self):
        record = codeops.get_codeops_guidance(
            action="inspect", doc_id="DOC-400"
        )["records"][0]
        self.assertEqual(record["title"], "GPT-5.1-Codex-Max System Card")
        self.assertIn("must not be silently translated",
                      record["historical_model_caveat"])


class TestCodeOpsRepositoryTools(CodeOpsRepositoryTestCase):
    def test_task_contract_uses_required_workflow(self):
        contract = codeops.create_codeops_task_contract(
            str(self.repo),
            "Add a bounded feature",
            acceptance_criteria="Tests pass",
        )
        self.assertEqual(contract["default_access"], "read_only")
        self.assertEqual(
            [phase["phase"] for phase in contract["workflow"]],
            list(codeops.TASK_WORKFLOW),
        )
        self.assertTrue(
            next(p for p in contract["workflow"] if p["phase"] == "release")[
                "approval_required"
            ]
        )

    def test_inspection_search_and_traversal_rejection(self):
        status = codeops.inspect_codeops_repository(str(self.repo))
        self.assertTrue(status["clean"])
        found = codeops.search_codeops_repository(
            str(self.repo), "before", file_glob="*.txt"
        )
        self.assertEqual(found["matches"][0]["path"], "tracked.txt")

        traversal = skills.dispatch("search_codeops_repository", {
            "repository": str(self.repo),
            "query": "anything",
            "path": "../",
        })
        self.assertIn("error", traversal)
        self.assertIn("traversal", traversal["error"])

    def test_symlink_search_and_edit_are_rejected(self):
        external = self.scratch / "external.txt"
        external.write_text("outside\n", encoding="utf-8")
        (self.repo / "linked.txt").symlink_to(external)

        searched = skills.dispatch("search_codeops_repository", {
            "repository": str(self.repo),
            "query": "outside",
            "path": "linked.txt",
        })
        self.assertIn("symlink", searched["error"])
        prepared = skills.dispatch("prepare_codeops_file_edit", {
            "repository": str(self.repo),
            "path": "linked.txt",
            "content": "changed\n",
        })
        self.assertIn("symlink", prepared["error"])
        self.assertEqual(external.read_text(encoding="utf-8"), "outside\n")

    def test_sensitive_path_edit_is_rejected(self):
        result = codeops.prepare_codeops_file_edit(
            str(self.repo), ".env", "API_KEY=not-real\n"
        )
        self.assertIn("sensitive", result["error"])

    def test_validation_discovery_allowlisting_and_execution(self):
        tests = self.repo / "tests"
        tests.mkdir()
        (tests / "test_ok.py").write_text(
            "import unittest\n\n"
            "class TestOK(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        discovered = codeops.discover_codeops_validation(str(self.repo))
        self.assertIn(
            "python-unittest",
            {item["id"] for item in discovered["validations"]},
        )
        rejected = codeops.run_codeops_validation(
            str(self.repo), "python -c 'print(1)'"
        )
        self.assertIn("allowlist", rejected["error"])
        completed = codeops.run_codeops_validation(
            str(self.repo), "python-unittest", timeout_seconds=30
        )
        self.assertEqual(completed["status"], "passed")
        self.assertTrue(completed["allowlisted"])

    def test_mutation_requires_gate_and_matching_token_then_audits(self):
        target = self.repo / "tracked.txt"
        before = target.read_text(encoding="utf-8")
        digest = hashlib.sha256(before.encode()).hexdigest()
        prepared = skills.dispatch("prepare_codeops_file_edit", {
            "repository": str(self.repo),
            "path": "tracked.txt",
            "content": "after\n",
            "expected_sha256": digest,
        })
        self.assertEqual(prepared["status"], "pending_approval")
        self.assertEqual(target.read_text(encoding="utf-8"), before)

        denied = skills.dispatch("apply_codeops_file_edit", {
            "repository": str(self.repo),
            "path": "tracked.txt",
            "content": "after\n",
            "approval_token": prepared["approval_token"],
        })
        self.assertIn("requires explicit approval", denied["error"])
        self.assertEqual(target.read_text(encoding="utf-8"), before)

        applied = skills.dispatch("apply_codeops_file_edit", {
            "repository": str(self.repo),
            "path": "tracked.txt",
            "content": "after\n",
            "approval_token": prepared["approval_token"],
            "_approved": True,
        })
        self.assertEqual(applied["status"], "applied")
        self.assertTrue(applied["approval_verified"])
        self.assertTrue(applied["audit_id"].startswith("coa-"))
        self.assertEqual(target.read_text(encoding="utf-8"), "after\n")

        with sqlite3.connect(codeops._DB_PATH) as conn:
            audit = conn.execute(
                "SELECT outcome, relative_path FROM codeops_edit_audit "
                "WHERE audit_id=?",
                (applied["audit_id"],),
            ).fetchone()
        self.assertEqual(audit, ("applied", "tracked.txt"))

    def test_review_reports_changed_file_and_diff_without_mutation(self):
        target = self.repo / "tracked.txt"
        target.write_text("after\n", encoding="utf-8")
        before_review = target.read_bytes()
        review = codeops.review_codeops_changes(str(self.repo))
        self.assertTrue(review["read_only"])
        self.assertIn("tracked.txt", review["changed_files"])
        self.assertIn("+after", review["diff"])
        self.assertEqual(target.read_bytes(), before_review)


if __name__ == "__main__":
    unittest.main()
