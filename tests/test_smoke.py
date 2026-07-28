"""
Smoke tests for PJ's skill system (stdlib unittest, no extra deps).

Run with:
    venv/bin/python -m unittest discover tests -v

All module-level _DB_PATH globals are redirected to a temp database so
tests never touch the real pj_data.sqlite3.
"""
import json
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Redirect every module's DB to a throwaway file BEFORE any skill runs.
_TMP_DB = Path(tempfile.mkstemp(suffix=".sqlite3")[1])

import skills, skillops, docops, chiefops, chatlog, codeops  # noqa: E402
import realtime_server  # noqa: E402

for _mod in (skills, skillops, docops, chiefops, chatlog, codeops):
    _mod._DB_PATH = _TMP_DB


class TestToolRegistry(unittest.TestCase):
    def test_every_function_schema_has_dispatch_entry(self):
        for schema in skills.TOOL_SCHEMAS:
            if schema.get("type") == "function":
                self.assertIn(schema["name"], skills.DISPATCH_TABLE,
                              f"{schema['name']} missing from DISPATCH_TABLE")

    def test_schemas_are_wellformed(self):
        for schema in skills.TOOL_SCHEMAS:
            if schema.get("type") != "function":
                continue
            self.assertIn("name", schema)
            self.assertIn("parameters", schema)
            params = schema["parameters"]
            self.assertEqual(params.get("type"), "object")
            self.assertIsInstance(params.get("properties", {}), dict)
            # required fields must exist in properties
            for req in params.get("required", []):
                self.assertIn(req, params.get("properties", {}),
                              f"{schema['name']}: required '{req}' not in properties")

    def test_no_duplicate_tool_names(self):
        names = [s["name"] for s in skills.TOOL_SCHEMAS
                 if s.get("type") == "function"]
        self.assertEqual(len(names), len(set(names)),
                         "duplicate tool names in TOOL_SCHEMAS")


class TestDispatch(unittest.TestCase):
    def test_unknown_skill_returns_error(self):
        result = skills.dispatch("no_such_skill", {})
        self.assertIn("error", result)

    def test_skill_exception_is_caught(self):
        # bad argument type should be caught, not raised
        result = skills.dispatch("get_current_time", {"timezone": "Not/AZone"})
        self.assertIn("error", result)

    def test_get_current_time(self):
        result = skills.dispatch("get_current_time", {})
        self.assertIn("iso8601", result)

    def test_task_lifecycle(self):
        added = skills.dispatch("add_task", {"title": "smoke test task",
                                             "priority": "P1"})
        self.assertEqual(added["status"], "logged")
        listed = skills.dispatch("list_tasks", {"status": "open"})
        self.assertTrue(any(t["id"] == added["task_id"]
                            for t in listed["tasks"]))
        done = skills.dispatch("complete_task", {"task_id": added["task_id"]})
        self.assertEqual(done["status"], "done")

    def test_note_roundtrip(self):
        skills.dispatch("save_note", {"topic": "smoketest",
                                      "content": "unique-marker-xyz"})
        found = skills.dispatch("search_notes", {"query": "unique-marker-xyz"})
        self.assertGreaterEqual(found["count"], 1)


class TestGeneratedSkills(unittest.TestCase):
    def test_generated_skills_load_cleanly(self):
        gen_schemas, gen_dispatch = skillops.load_generated_skills()
        for schema in gen_schemas:
            self.assertIn(schema["name"], gen_dispatch)
            self.assertEqual(schema["parameters"].get("type"), "object")

    def test_generated_skill_dispatch_does_not_crash(self):
        _, gen_dispatch = skillops.load_generated_skills()
        for name in gen_dispatch:
            result = skills.dispatch(name, {})
            self.assertIsInstance(result, dict,
                                  f"{name} returned non-dict")


class TestToolBridgeAuth(unittest.TestCase):
    def setUp(self):
        self.client = realtime_server.app.test_client()

    def test_tool_bridge_fails_closed_when_token_is_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.get("/tool-schemas")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"]["code"],
                         "bridge_auth_not_configured")

    def test_tool_bridge_rejects_invalid_token(self):
        with patch.dict(os.environ, {"PJ_TOOL_BRIDGE_TOKEN": "expected"}, clear=True):
            response = self.client.get(
                "/tool-schemas",
                headers={"Authorization": "Bearer wrong"},
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"],
                         "bridge_auth_required")

    def test_tool_bridge_rejects_non_ascii_token_with_typed_error(self):
        with patch.dict(os.environ, {"PJ_TOOL_BRIDGE_TOKEN": "expected"}, clear=True):
            response = self.client.get(
                "/tool-schemas",
                headers={"Authorization": "Bearer inválido"},
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"],
                         "bridge_auth_required")

    def test_tool_bridge_accepts_valid_token(self):
        with patch.dict(os.environ, {"PJ_TOOL_BRIDGE_TOKEN": "expected"}, clear=True):
            response = self.client.get(
                "/tool-schemas",
                headers={"Authorization": "Bearer expected"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])


class TestDocOpsTemplateImport(unittest.TestCase):
    def test_import_knowledge_pack_registers_aliases(self):
        knowledge_pack = """\
---ITEM_START
item_id: ITEM-ALPHA-001
canonical_title: Customer Success Weekly Brief
template_name: customer_success_weekly
description: Weekly operating brief for customer success.
required_sections:
- Purpose
- Highlights
- Next Actions
optional_sections:
- Risks
---ITEM_END
"""
        imported = docops.import_doc_templates_from_knowledge_pack_text(
            knowledge_pack,
            overwrite_existing=True,
            include_provisional=True,
            dry_run=False,
        )
        self.assertIn(imported["status"], ("imported", "dry_run_complete"))
        self.assertGreaterEqual(
            imported["templates_created"] + imported["templates_updated"], 1
        )

        sections = {
            "Purpose": "Summarize the operating week.",
            "Highlights": "Retention improved by 4%.",
            "Next Actions": "Expand QBR prep automation.",
            "Risks": "None",
        }
        created_paths = []
        try:
            drafted_by_title = docops.draft_document(
                "Customer Success Weekly Brief",
                "CS Weekly",
                json.dumps(sections),
            )
            self.assertIn("doc_id", drafted_by_title)
            created_paths.append(drafted_by_title.get("path"))

            drafted_by_item_id = docops.draft_document(
                "ITEM-ALPHA-001",
                "CS Weekly by Item ID",
                json.dumps(sections),
            )
            self.assertIn("doc_id", drafted_by_item_id)
            created_paths.append(drafted_by_item_id.get("path"))
        finally:
            for path in created_paths:
                if path:
                    Path(path).unlink(missing_ok=True)

    def test_real_pack_format_uses_yaml_purpose_structure_and_marker_alias(self):
        knowledge_pack = """\
---ITEM_START: DOC-900---

```yaml
canonical_title: “Portfolio Operating Review”
template_name: "portfolio_operating_review"
corpus_status: "provisional_instructional_spec"
```

### Portfolio Operating Review

**Purpose:** Create the monthly decision-ready portfolio review.

**Recommended structure**
1. Portfolio snapshot
2. Decisions required
3. Risks and next actions

**Drafting procedure**
1. Validate the source data.

---ITEM_END: DOC-900---
"""
        excluded = docops.import_doc_templates_from_knowledge_pack_text(
            knowledge_pack,
            overwrite_existing=True,
            include_provisional=False,
            dry_run=True,
        )
        self.assertEqual(excluded["items_skipped_provisional"], 1)

        imported = docops.import_doc_templates_from_knowledge_pack_text(
            knowledge_pack,
            overwrite_existing=True,
            include_provisional=True,
            dry_run=False,
        )
        self.assertEqual(imported["items_total"], 1)
        self.assertEqual(imported["items_skipped_invalid"], 0)
        self.assertEqual(
            imported["imports"][0]["required_sections"],
            ["Portfolio snapshot", "Decisions required", "Risks and next actions"],
        )
        templates = docops.list_doc_templates()["templates"]
        template = next(
            row for row in templates if row["name"] == "portfolio_operating_review"
        )
        self.assertEqual(
            template["description"],
            "Create the monthly decision-ready portfolio review.",
        )

        sections = {
            "Portfolio snapshot": "Performance remains on plan.",
            "Decisions required": "Approve the next deployment.",
            "Risks and next actions": "Monitor concentration risk.",
        }
        created_paths = []
        try:
            for reference in ("Portfolio Operating Review", "DOC-900"):
                drafted = docops.draft_document(
                    reference,
                    f"Draft via {reference}",
                    json.dumps(sections),
                )
                self.assertIn("doc_id", drafted)
                created_paths.append(drafted.get("path"))
        finally:
            for path in created_paths:
                if path:
                    Path(path).unlink(missing_ok=True)

    def test_json_item_format_remains_supported(self):
        payload = json.dumps({
            "item_id": "JSON-1",
            "canonical_title": "JSON Template",
            "template_name": "json_template",
            "description": "JSON compatibility.",
            "required_sections": ["Summary"],
        })
        result = docops.import_doc_templates_from_knowledge_pack_text(
            f"---ITEM_START\n{payload}\n---ITEM_END",
            overwrite_existing=True,
            include_provisional=True,
            dry_run=True,
        )
        self.assertEqual(result["items_total"], 1)
        self.assertEqual(result["items_skipped_invalid"], 0)
        self.assertEqual(result["imports"][0]["required_sections"], ["Summary"])

    def test_incomplete_item_markers_are_invalid(self):
        incomplete = """\
source_record_count: 2
---ITEM_START: COMPLETE-1---
item_id: COMPLETE-1
canonical_title: Complete Item
required_sections:
- Summary
---ITEM_END: COMPLETE-1---
---ITEM_START: TRUNCATED-2---
item_id: TRUNCATED-2
"""
        result = docops.import_doc_templates_from_knowledge_pack_text(
            incomplete,
            overwrite_existing=True,
            include_provisional=True,
            dry_run=True,
        )
        self.assertGreater(result["items_skipped_invalid"], 0)
        self.assertTrue(any(
            "incomplete or unmatched ITEM markers" in error
            for error in result["errors"]
        ))

    def test_only_truncated_item_start_is_invalid(self):
        result = docops.import_doc_templates_from_knowledge_pack_text(
            "---ITEM_START: TRUNCATED-ONLY---\nitem_id: TRUNCATED-ONLY\n",
            overwrite_existing=True,
            include_provisional=True,
            dry_run=True,
        )
        self.assertEqual(result["items_total"], 0)
        self.assertGreater(result["items_skipped_invalid"], 0)
        self.assertTrue(result["errors"])

    def test_mismatched_marker_ids_are_invalid(self):
        mismatched = """\
---ITEM_START: START-ID---
item_id: START-ID
canonical_title: Mismatched Markers
required_sections:
- Summary
---ITEM_END: OTHER-ID---
"""
        result = docops.import_doc_templates_from_knowledge_pack_text(
            mismatched,
            overwrite_existing=True,
            include_provisional=True,
            dry_run=True,
        )
        self.assertGreater(result["items_skipped_invalid"], 0)
        self.assertTrue(any(
            "marker IDs do not match" in error for error in result["errors"]
        ))

    def test_full_pack_dry_run_when_attachment_exists(self):
        default_path = (
            "/Users/matthewbernal/.copilot/workspaces/"
            "78c81e10-227a-4773-868d-0e0ad54e5888/attachments/"
            "c71b4316-e0c8-4751-9965-4b8e2670b402-"
            "Pryceless_Document_Generation_Knowledge_Pack_v0.1.md"
        )
        pack_path = Path(os.getenv("PJ_KNOWLEDGE_PACK_PATH", default_path))
        if not pack_path.exists():
            self.skipTest("full knowledge-pack attachment is not available")
        result = docops.import_doc_templates_from_knowledge_pack_text(
            pack_path.read_text(),
            overwrite_existing=True,
            include_provisional=True,
            dry_run=True,
        )
        self.assertEqual(result["items_total"], 437)
        self.assertEqual(
            result["templates_created"] + result["templates_updated"],
            437,
        )
        self.assertEqual(result["items_skipped_invalid"], 0)


class TestVectorStoreSync(unittest.TestCase):
    def setUp(self):
        self.lock_path = Path(tempfile.mkdtemp()) / "vector-sync.lock"
        self.cache_path = self.lock_path.parent / "vector-source-cache"
        self.pack_text = """\
---ITEM_START: SYNC-ITEM-1---
item_id: SYNC-ITEM-1
canonical_title: Sync Test Template
template_name: sync_test_template
description: Used to test durable vector synchronization.
required_sections:
- Summary
---ITEM_END: SYNC-ITEM-1---
"""
        self.entry = {
            "id": "vs-file-sync-1",
            "file_id": "file-sync-1",
            "created_at": 100,
            "attributes": {"version": "1"},
        }
        self.metadata = {
            "id": "file-sync-1",
            "filename": "sync-pack.md",
            "bytes": len(self.pack_text),
            "created_at": 100,
            "purpose": "assistants_output",
        }

    def _run_sync(self, **kwargs):
        with (
            mock.patch.object(skillops, "_SYNC_LOCK_PATH", self.lock_path),
            mock.patch.object(
                skillops, "_VECTOR_SOURCE_CACHE_DIR", self.cache_path
            ),
            mock.patch.object(
                skillops, "_require_vector_store_id", return_value="vs-test"
            ),
            mock.patch.object(
                skillops, "_require_openai_api_key", return_value="test-key"
            ),
            mock.patch.object(
                skillops,
                "_list_vector_store_files",
                return_value=[dict(self.entry)],
            ),
            mock.patch.object(
                skillops,
                "_list_openai_file_metadata",
                return_value={
                    str(self.entry["file_id"]): dict(self.metadata)
                },
            ),
            mock.patch.object(
                skillops,
                "_read_openai_file_content",
                return_value=(self.pack_text, False),
            ),
        ):
            return skillops.sync_vector_store(**kwargs)

    def test_sync_skips_unchanged_and_reprocesses_changed_content(self):
        first = self._run_sync()
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["files_processed"], 1)
        self.assertEqual(first["files_failed"], 0)

        second = self._run_sync()
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["files_processed"], 0)
        self.assertEqual(second["files_skipped_unchanged"], 1)

        self.entry["attributes"] = {"version": "2"}
        self.metadata["bytes"] += 1
        self.pack_text = self.pack_text.replace(
            "Used to test durable vector synchronization.",
            "Changed content is reprocessed by durable vector synchronization.",
        )
        changed = self._run_sync()
        self.assertEqual(changed["status"], "completed")
        self.assertEqual(changed["files_processed"], 1)
        self.assertEqual(changed["templates_updated"], 1)

    def test_failed_complete_read_is_audited_but_not_synchronized(self):
        entry = {
            "id": "vs-file-sync-failure",
            "file_id": "file-sync-failure",
            "created_at": 100,
        }
        metadata = {
            "filename": "oversized.md",
            "bytes": skillops.DEFAULT_MAX_CHARS_PER_FILE + 1,
            "created_at": 100,
        }
        with (
            mock.patch.object(skillops, "_SYNC_LOCK_PATH", self.lock_path),
            mock.patch.object(
                skillops, "_VECTOR_SOURCE_CACHE_DIR", self.cache_path
            ),
            mock.patch.object(
                skillops, "_require_vector_store_id", return_value="vs-test"
            ),
            mock.patch.object(
                skillops, "_require_openai_api_key", return_value="test-key"
            ),
            mock.patch.object(
                skillops, "_list_vector_store_files", return_value=[entry]
            ),
            mock.patch.object(
                skillops,
                "_list_openai_file_metadata",
                return_value={"file-sync-failure": metadata},
            ),
            mock.patch.object(
                skillops,
                "_read_openai_file_content",
                side_effect=ValueError("complete-file safety limit exceeded"),
            ),
        ):
            result = skillops.sync_vector_store()
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(result["files_failed"], 1)
        status = skillops.get_vector_sync_status(limit=10)
        file_state = next(
            row for row in status["files"]
            if row["file_id"] == "file-sync-failure"
        )
        self.assertIsNone(file_state["synchronized_at"])
        self.assertEqual(file_state["last_attempt_status"], "failed")

    def test_non_downloadable_file_is_classified_without_repeated_failure(self):
        self.metadata["purpose"] = "assistants"
        first = self._run_sync()
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["files_processed"], 0)
        self.assertEqual(first["files_skipped_unavailable"], 1)
        self.assertEqual(first["files_failed"], 0)

        status = skillops.get_vector_sync_status(limit=10)
        file_state = next(
            row for row in status["files"]
            if row["file_id"] == "file-sync-1"
        )
        self.assertIsNone(file_state["synchronized_at"])
        self.assertEqual(
            file_state["last_attempt_status"],
            "skipped_content_unavailable",
        )

        second = self._run_sync()
        self.assertEqual(second["files_skipped_unchanged"], 1)
        self.assertEqual(second["files_skipped_unavailable"], 0)

    def test_verified_cache_makes_input_file_synchronizable(self):
        self.metadata["purpose"] = "user_data"
        with mock.patch.object(
            skillops, "_VECTOR_SOURCE_CACHE_DIR", self.cache_path
        ):
            cached = skillops.cache_vector_source(
                "file-sync-1",
                self.pack_text.encode("utf-8"),
                filename=self.metadata["filename"],
            )
        self.entry["attributes"]["source_sha256"] = cached["source_sha256"]
        result = self._run_sync()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["files_processed"], 1)
        self.assertEqual(
            result["file_reports"][0]["content_source"],
            "verified_local_cache",
        )

    def test_adding_verified_cache_invalidates_unavailable_state(self):
        self.metadata["purpose"] = "user_data"
        first = self._run_sync()
        self.assertEqual(first["files_skipped_unavailable"], 1)
        with mock.patch.object(
            skillops, "_VECTOR_SOURCE_CACHE_DIR", self.cache_path
        ):
            skillops.cache_vector_source(
                "file-sync-1",
                self.pack_text.encode("utf-8"),
                filename=self.metadata["filename"],
            )
        second = self._run_sync()
        self.assertEqual(second["files_processed"], 1)
        self.assertEqual(
            second["file_reports"][0]["content_source"],
            "verified_local_cache",
        )

    def test_sync_report_details_are_bounded_and_prioritize_actions(self):
        unchanged = [
            {"file_id": f"file-{index}", "status": "skipped_unchanged"}
            for index in range(skillops.MAX_SYNC_REPORT_DETAILS + 25)
        ]
        failed = {"file_id": "file-failed", "status": "failed"}
        details = skillops._bounded_sync_details(
            {"files_seen": len(unchanged) + 1},
            [*unchanged, failed],
            ["failure"],
        )
        self.assertEqual(
            len(details["files"]), skillops.MAX_SYNC_REPORT_DETAILS
        )
        self.assertIn(failed, details["files"])
        self.assertEqual(details["file_reports_omitted"], 26)

    def test_sync_imports_codeops_guidance_corpus(self):
        self.entry = {
            "id": "vs-file-codeops",
            "file_id": "file-codeops",
            "created_at": 300,
        }
        self.pack_text = """\
---ITEM_START: DOC-SYNC---
```yaml
item_id: "DOC-SYNC"
source_page_url: "https://example.test/codeops"
canonical_title: "Synced CodeOps Guide"
tool_family: "CodeOps"
surface: "CLI"
version_scope: "historical"
corpus_status: "training_ready_current_docs_override"
requires_current_docs_check: true
content_sha256: "sync-hash"
```
**What this item teaches:** Safe synchronized coding guidance.
#### Appropriate tasks
- inspect code
#### Recommended operating workflow
1. Inspect.
#### Safety and governance controls
- Use least privilege.
#### Prompt contract
State the objective.
#### Output contract
Return evidence.
#### Evaluation checklist
- [ ] Evidence exists.
#### Current authoritative sources
- https://example.test/current
---ITEM_END: DOC-SYNC---
"""
        self.metadata = {
            "id": "file-codeops",
            "filename": "codeops-corpus.md",
            "bytes": len(self.pack_text.encode("utf-8")),
            "created_at": 300,
            "purpose": "user_data",
        }
        with mock.patch.object(
            skillops, "_VECTOR_SOURCE_CACHE_DIR", self.cache_path
        ):
            cached = skillops.cache_vector_source(
                "file-codeops",
                self.pack_text.encode("utf-8"),
                filename=self.metadata["filename"],
            )
        self.entry["attributes"] = {
            "source_sha256": cached["source_sha256"]
        }
        result = self._run_sync()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["files_processed"], 1)
        self.assertEqual(result["guidance_records_imported"], 1)
        guide_ids = {
            guide["item_id"]
            for guide in codeops.list_codeops_guides(limit=100)["guides"]
        }
        self.assertIn("DOC-SYNC", guide_ids)

    def test_complete_reader_rejects_oversized_file_without_returning_prefix(self):
        class FakeResponse:
            status_code = 200
            closed = False
            headers = {}

            def iter_content(self, chunk_size):
                del chunk_size
                yield b"12345"
                yield b"67890"

            def close(self):
                self.closed = True

        response = FakeResponse()
        with mock.patch.object(
            skillops, "_request_with_retry", return_value=response
        ):
            with self.assertRaisesRegex(ValueError, "no partial content"):
                skillops._read_openai_file_content("file-large", "key", 7)
        self.assertTrue(response.closed)

    def test_complete_reader_rejects_early_eof(self):
        class FakeResponse:
            headers = {"Content-Length": "10"}

            def iter_content(self, chunk_size):
                del chunk_size
                yield b"abc"

            def close(self):
                pass

        with mock.patch.object(
            skillops, "_request_with_retry", return_value=FakeResponse()
        ):
            with self.assertRaisesRegex(RuntimeError, "incomplete file read"):
                skillops._read_openai_file_content("file-short", "key", 100)

    def test_sync_deduplication_includes_import_policy(self):
        self.entry = {
            "id": "vs-file-policy",
            "file_id": "file-policy",
            "created_at": 200,
        }
        self.pack_text = """\
---ITEM_START: POLICY-1---
item_id: POLICY-1
canonical_title: Policy Sensitive Template
template_name: policy_sensitive_template
corpus_status: provisional_instructional_spec
required_sections:
- Summary
---ITEM_END: POLICY-1---
"""
        self.metadata = {
            "id": "file-policy",
            "filename": "policy-pack.md",
            "bytes": len(self.pack_text),
            "created_at": 200,
        }
        excluded = self._run_sync(include_provisional=False)
        self.assertEqual(excluded["files_processed"], 1)
        self.assertEqual(excluded["items_skipped_provisional"], 1)

        included = self._run_sync(include_provisional=True)
        self.assertEqual(included["files_processed"], 1)
        self.assertEqual(included["templates_created"], 1)

    def test_cross_process_lock_prevents_overlapping_sync(self):
        with mock.patch.object(skillops, "_SYNC_LOCK_PATH", self.lock_path):
            with skillops._vector_sync_lock() as acquired:
                self.assertTrue(acquired)
                with mock.patch.object(
                    skillops,
                    "_require_vector_store_id",
                    return_value="vs-test",
                ):
                    result = skillops.sync_vector_store()
        self.assertEqual(result["status"], "locked")


class TestConfig(unittest.TestCase):
    def test_config_and_mcp_json_valid(self):
        with open(BASE_DIR / "config.json") as f:
            cfg = json.load(f)
        for key in ("name", "model", "instructions_file"):
            self.assertIn(key, cfg)
        self.assertTrue((BASE_DIR / cfg["instructions_file"]).exists())
        with open(BASE_DIR / "mcp_servers.json") as f:
            servers = json.load(f)
        for s in servers:
            self.assertIn("label", s)
            self.assertIn("url", s)

    def test_vector_sync_launchagent_template_is_secret_free_and_valid(self):
        template_path = BASE_DIR / "scripts" / "com.pj.vector-store-sync.plist"
        with template_path.open("rb") as handle:
            template = plistlib.load(handle)
        self.assertEqual(template["Label"], "com.pj.vector-store-sync")
        self.assertIn("__PJ_ROOT__", template["WorkingDirectory"])
        self.assertIn("__PJ_PYTHON__", template["ProgramArguments"])
        serialized = template_path.read_text()
        self.assertNotIn("OPENAI_API_KEY", serialized)
        self.assertNotIn("sk-", serialized)


if __name__ == "__main__":
    unittest.main()
