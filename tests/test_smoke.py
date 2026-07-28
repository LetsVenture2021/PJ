"""
Smoke tests for PJ's skill system (stdlib unittest, no extra deps).

Run with:
    venv/bin/python -m unittest discover tests -v

All module-level _DB_PATH globals are redirected to a temp database so
tests never touch the real pj_data.sqlite3.
"""
import hashlib
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
from scripts import vector_store_ingest  # noqa: E402

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

    def test_fenced_json_metadata_survives_later_yaml_example(self):
        payload = json.dumps({
            "item_id": "JSON-YAML-1",
            "canonical_title": "JSON With YAML Example",
            "template_name": "json_with_yaml_example",
            "required_sections": ["Summary"],
        })
        knowledge_pack = (
            f"---ITEM_START: JSON-YAML-1---\n"
            f"```json\n{payload}\n```\n"
            "Example:\n```yaml\nunrelated: true\n```\n"
            "---ITEM_END: JSON-YAML-1---\n"
        )
        result = docops.import_doc_templates_from_knowledge_pack_text(
            knowledge_pack,
            overwrite_existing=True,
            include_provisional=True,
            dry_run=True,
        )
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
        self.original_skillops_db_path = skillops._DB_PATH
        self.original_docops_db_path = docops._DB_PATH
        self.original_codeops_db_path = codeops._DB_PATH
        self.temp_db_path = Path(tempfile.mkstemp(suffix=".sqlite3")[1])
        skillops._DB_PATH = self.temp_db_path
        docops._DB_PATH = self.temp_db_path
        codeops._DB_PATH = self.temp_db_path
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

    def tearDown(self):
        skillops._DB_PATH = self.original_skillops_db_path
        docops._DB_PATH = self.original_docops_db_path
        codeops._DB_PATH = self.original_codeops_db_path
        self.temp_db_path.unlink(missing_ok=True)

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

    def _run_learn(self, **kwargs):
        with (
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
            return skillops.learn_from_vector_store(**kwargs)

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
source_record_id: "DOC-SYNC"
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
## Embedded source heading
This heading must not make the record a DocOps template.
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
        learned = self._run_learn(overwrite_existing=True)
        self.assertEqual(learned["status"], "completed")
        self.assertEqual(learned["guidance_records_imported"], 1)

    def test_sync_filters_provisional_codeops_and_reports_status(self):
        self.entry = {
            "id": "vs-file-provisional-codeops",
            "file_id": "file-provisional-codeops",
            "created_at": 301,
        }
        self.pack_text = """\
---ITEM_START: DOC-PROVISIONAL---
```yaml
item_id: "DOC-PROVISIONAL"
source_page_url: "https://example.test/provisional"
source_record_id: "DOC-PROVISIONAL"
canonical_title: "Provisional CodeOps Guide"
tool_family: "CodeOps"
surface: "CLI"
version_scope: "historical"
corpus_status: "provisional_instructional_spec"
requires_current_docs_check: true
content_sha256: "provisional-hash"
```
#### Prompt contract
State the objective.
#### Output contract
Return evidence.
---ITEM_END: DOC-PROVISIONAL---
"""
        self.metadata = {
            "id": "file-provisional-codeops",
            "filename": "provisional-codeops.md",
            "bytes": len(self.pack_text.encode("utf-8")),
            "created_at": 301,
        }
        result = self._run_sync(include_provisional=False)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["guidance_records_imported"], 0)
        self.assertEqual(result["items_skipped_provisional"], 1)
        guide_ids = {
            guide["item_id"]
            for guide in codeops.list_codeops_guides(limit=100)["guides"]
        }
        self.assertNotIn("DOC-PROVISIONAL", guide_ids)
        latest = skillops.get_vector_sync_status(limit=1)["runs"][0]
        self.assertEqual(latest["items_skipped_provisional"], 1)
        self.assertEqual(latest["items_skipped_invalid"], 0)

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

    def test_sync_deduplication_includes_importer_revision(self):
        current = skillops._sync_policy_hash(True, True)
        with mock.patch.object(
            skillops,
            "SYNC_IMPORTER_REVISION",
            "legacy-docops-codeops-router",
        ):
            previous = skillops._sync_policy_hash(True, True)
        self.assertNotEqual(current, previous)

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


class TestN8nCapabilityCorpus(unittest.TestCase):
    def setUp(self):
        self.original_skillops_db_path = skillops._DB_PATH
        self.original_docops_db_path = docops._DB_PATH
        self.original_codeops_db_path = codeops._DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_db_path = Path(self.temp_dir.name) / "n8n.sqlite3"
        skillops._DB_PATH = self.temp_db_path
        docops._DB_PATH = self.temp_db_path
        codeops._DB_PATH = self.temp_db_path
        self.lock_path = Path(self.temp_dir.name) / "n8n-sync.lock"
        self.cache_path = Path(self.temp_dir.name) / "vector-source-cache"

    def tearDown(self):
        skillops._DB_PATH = self.original_skillops_db_path
        docops._DB_PATH = self.original_docops_db_path
        codeops._DB_PATH = self.original_codeops_db_path
        self.temp_dir.cleanup()

    @staticmethod
    def _corpus(*, count=40, top5_passed=9):
        preamble = f"""\
# n8n Capability Training Corpus
corpus_type: n8n_capabilities
corpus_version: 1.0.0
record_count: {count}
canonical_pages_total: 40
canonical_pages_covered: 40
inaccessible_sources_total: 0
inaccessible_sources_dispositioned: 0
retrieval_cases_total: 10
retrieval_top5_passed: {top5_passed}
security_warning_cases_total: 5
security_warning_cases_passed: 5
invented_node_parameters: 0
credential_exposures: 0

"""
        items = []
        for index in range(1, count + 1):
            item_id = f"N8N-{index:03d}"
            items.append(f"""\
---ITEM_START: {item_id}---
```yaml
item_id: {item_id}
canonical_title: n8n Capability {index}
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/capability-{index}
source_record_id: SOURCE-{index:03d}
content_sha256: {index:064x}
taxonomy: [workflow, security]
```
**What this capability teaches:** Build governed n8n workflow {index}.
#### Task types
- Workflow design
- Operational review
#### Recommended operating workflow
1. Inspect current documentation.
2. Validate node parameters.
3. Request approval before activation.
#### Output contract
Return a validated workflow specification with evidence and rollback guidance.
#### Safety and governance controls
- Never expose credentials.
- Require approval before activation.
#### Cloud and self-hosted differences
- Verify feature availability for the selected deployment.
#### Version constraints
- Recheck node parameters against current documentation.
#### Validation checklist
- Validate every node and connection.
#### Failure modes
- Reject invented node parameters.
#### Current authoritative sources
- https://docs.n8n.io/capability-{index}
#### Freshness requirements
- Revalidate before deployment.
#### Approval policy
Human approval is required before workflow activation or credential changes.
---ITEM_END: {item_id}---
""")
        return preamble + "\n".join(items)

    @staticmethod
    def _receipt(corpus_text):
        parsed = skillops._parse_n8n_capability_corpus(corpus_text)
        metrics = {
            field: parsed["declared_evaluation"]["metrics"][field]
            for field in skillops._N8N_EVALUATION_FIELDS
        }
        return {
            "schema_version": "1",
            "evaluation_id": (
                "n8n-eval-"
                + hashlib.sha256(corpus_text.encode("utf-8")).hexdigest()[:12]
            ),
            "evaluated_at": "2026-07-28T00:00:00+00:00",
            "evaluator": "network-free-regression-suite",
            "evidence_sha256": "e" * 64,
            "corpus_sha256": hashlib.sha256(
                corpus_text.encode("utf-8")
            ).hexdigest(),
            "corpus_version": parsed["corpus_version"],
            "registry_sha256": skillops._n8n_records_sha256(
                parsed["records"]
            ),
            "metrics": metrics,
        }

    @staticmethod
    def _approval(corpus_text, receipt):
        return {
            "corpus_sha256": hashlib.sha256(
                corpus_text.encode("utf-8")
            ).hexdigest(),
            "corpus_version": "1.0.0",
            "evaluation_receipt_sha256": (
                skillops._n8n_receipt_sha256(receipt)
            ),
        }

    def _run_sync(self, corpus_text, **kwargs):
        receipt = self._receipt(corpus_text)
        approval = self._approval(corpus_text, receipt)
        entry = {
            "id": "vs-file-n8n",
            "file_id": "file-n8n",
            "created_at": 500,
            "attributes": {
                "corpus_type": skillops.N8N_CORPUS_TYPE,
                "version": "1.0.0",
            },
        }
        metadata = {
            "id": "file-n8n",
            "filename": "n8n-capability-corpus.md",
            "bytes": len(corpus_text.encode("utf-8")),
            "created_at": 500,
            "purpose": "assistants_output",
        }
        with (
            mock.patch.object(skillops, "_SYNC_LOCK_PATH", self.lock_path),
            mock.patch.object(
                skillops, "_VECTOR_SOURCE_CACHE_DIR", self.cache_path
            ),
            mock.patch.object(
                skillops, "_require_vector_store_id", return_value="vs-n8n"
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
                return_value={"file-n8n": metadata},
            ),
            mock.patch.object(
                skillops,
                "_read_openai_file_content",
                return_value=(corpus_text, False),
            ),
            mock.patch.object(
                skillops,
                "_load_n8n_evaluation_receipt_for_corpus",
                return_value=receipt,
            ),
            mock.patch.object(
                skillops,
                "_load_n8n_release_approval",
                return_value=approval,
            ),
        ):
            return skillops.sync_vector_store(
                include_provisional=False,
                **kwargs,
            )

    def test_valid_corpus_uses_shared_registry_and_release_gates(self):
        corpus = self._corpus()
        first = self._run_sync(corpus)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["capabilities_created"], 40)

        n8n = skillops.list_n8n_capabilities(limit=100)
        coding = skillops.list_coding_capabilities(limit=100)
        self.assertEqual(n8n["count"], 40)
        self.assertEqual(coding["count"], 0)
        self.assertEqual(n8n["capabilities"][0]["item_id"], "N8N-001")

        second = self._run_sync(corpus)
        self.assertEqual(second["files_skipped_unchanged"], 1)
        receipt = self._receipt(corpus)
        with mock.patch.object(
            skillops,
            "_load_n8n_release_approval",
            return_value=self._approval(corpus, receipt),
        ):
            status = skillops.get_n8n_corpus_status()
        self.assertTrue(status["production_ready"])
        self.assertEqual(status["census"]["by_status"], {"synchronized": 1})
        self.assertEqual(status["census"]["pending_count"], 0)

        sync_file = skillops.get_vector_sync_status(limit=1)["files"][0]
        self.assertEqual(sync_file["last_attempt_status"], "skipped_unchanged")
        self.assertEqual(sync_file["terminal_status"], "synchronized")

    def test_unapproved_corpus_and_self_reported_metrics_cannot_release(self):
        corpus = self._corpus()
        receipt = self._receipt(corpus)
        with mock.patch.object(
            skillops,
            "_load_n8n_release_approval",
            return_value={
                "corpus_sha256": "",
                "corpus_version": "",
                "evaluation_receipt_sha256": "",
            },
        ):
            preflight = skillops.preflight_n8n_corpus_text(corpus, receipt)
            imported = skillops.import_n8n_capability_corpus_text(
                corpus,
                source_file_id="file-unapproved",
                evaluation_receipt=receipt,
            )

        self.assertTrue(preflight["evaluation"]["passed"])
        self.assertFalse(preflight["evaluation"]["approved"])
        self.assertFalse(preflight["ingestion_ready"])
        self.assertEqual(imported["status"], "invalid")
        self.assertEqual(imported["capabilities_created"], 0)

    def test_structural_security_and_evaluation_failures_block_import(self):
        valid = self._corpus()
        invalid_corpora = {
            "missing output contract": valid.replace(
                "#### Output contract\n"
                "Return a validated workflow specification with evidence and "
                "rollback guidance.\n",
                "",
                1,
            ),
            "malformed source hash": valid.replace(f"{1:064x}", "NOT-A-HASH", 1),
            "credential material": valid.replace(
                "#### Approval policy",
                "api_key: abcdefghijklmnopqrstuvwxyz123456\n"
                "#### Approval policy",
                1,
            ),
            "source URL query": valid.replace(
                "https://docs.n8n.io/capability-1",
                "https://docs.n8n.io/capability-1?token=redacted",
                1,
            ),
            "low retrieval score": self._corpus(top5_passed=8),
            "inconsistent evaluation metrics": valid.replace(
                "canonical_pages_covered: 40",
                "canonical_pages_covered: 41",
                1,
            ),
        }
        for label, corpus in invalid_corpora.items():
            with self.subTest(label=label):
                receipt = self._receipt(corpus)
                with mock.patch.object(
                    skillops,
                    "_load_n8n_release_approval",
                    return_value=self._approval(corpus, receipt),
                ):
                    preflight = skillops.preflight_n8n_corpus_text(
                        corpus,
                        receipt,
                    )
                    self.assertFalse(preflight["ingestion_ready"])
                    result = skillops.import_n8n_capability_corpus_text(
                        corpus,
                        dry_run=False,
                        source_file_id=f"file-{label}",
                        evaluation_receipt=receipt,
                    )
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(result["capabilities_created"], 0)
        self.assertEqual(
            skillops.get_n8n_corpus_status(include_census=False)[
                "capability_count"
            ],
            0,
        )

    def test_authoritative_import_retires_records_missing_from_new_version(self):
        original = self._corpus()
        original_receipt = self._receipt(original)
        with mock.patch.object(
            skillops,
            "_load_n8n_release_approval",
            return_value=self._approval(original, original_receipt),
        ):
            imported = skillops.import_n8n_capability_corpus_text(
                original,
                dry_run=False,
                source_file_id="file-n8n-v1",
                evaluation_receipt=original_receipt,
            )
        self.assertEqual(imported["capabilities_created"], 40)

        revised = original.replace("N8N-040", "N8N-140")
        revised_receipt = self._receipt(revised)
        with mock.patch.object(
            skillops,
            "_load_n8n_release_approval",
            return_value=self._approval(revised, revised_receipt),
        ):
            updated = skillops.import_n8n_capability_corpus_text(
                revised,
                dry_run=False,
                source_file_id="file-n8n-v2",
                evaluation_receipt=revised_receipt,
            )
        self.assertEqual(updated["capabilities_created"], 1)
        self.assertEqual(updated["capabilities_retired"], 1)
        capabilities = skillops.list_n8n_capabilities(limit=100)
        ids = {item["item_id"] for item in capabilities["capabilities"]}
        self.assertEqual(capabilities["count"], 40)
        self.assertNotIn("N8N-040", ids)
        self.assertIn("N8N-140", ids)

    def test_changed_records_cannot_be_skipped_as_successful_import(self):
        original = self._corpus()
        original_receipt = self._receipt(original)
        with mock.patch.object(
            skillops,
            "_load_n8n_release_approval",
            return_value=self._approval(original, original_receipt),
        ):
            first = skillops.import_n8n_capability_corpus_text(
                original,
                source_file_id="file-original",
                evaluation_receipt=original_receipt,
            )
        self.assertEqual(first["status"], "imported")

        revised = original.replace(
            "canonical_title: n8n Capability 1",
            "canonical_title: Changed n8n Capability 1",
            1,
        )
        revised_receipt = self._receipt(revised)
        with mock.patch.object(
            skillops,
            "_load_n8n_release_approval",
            return_value=self._approval(revised, revised_receipt),
        ):
            skipped = skillops.import_n8n_capability_corpus_text(
                revised,
                overwrite_existing=False,
                source_file_id="file-revised",
                evaluation_receipt=revised_receipt,
            )
        self.assertEqual(skipped["status"], "invalid")
        self.assertEqual(skipped["capabilities_updated"], 0)
        current = skillops.list_n8n_capabilities(
            query="N8N-001",
            limit=1,
        )["capabilities"][0]
        self.assertEqual(current["canonical_title"], "n8n Capability 1")

    def test_empty_full_sync_invalidates_stale_census(self):
        corpus = self._corpus()
        self._run_sync(corpus)
        with (
            mock.patch.object(skillops, "_SYNC_LOCK_PATH", self.lock_path),
            mock.patch.object(
                skillops, "_require_vector_store_id", return_value="vs-n8n"
            ),
            mock.patch.object(
                skillops, "_require_openai_api_key", return_value="test-key"
            ),
            mock.patch.object(
                skillops, "_list_vector_store_files", return_value=[]
            ),
            mock.patch.object(
                skillops, "_list_openai_file_metadata", return_value={}
            ),
        ):
            empty = skillops.sync_vector_store()
        self.assertEqual(empty["status"], "completed")
        receipt = self._receipt(corpus)
        with mock.patch.object(
            skillops,
            "_load_n8n_release_approval",
            return_value=self._approval(corpus, receipt),
        ):
            status = skillops.get_n8n_corpus_status()
        self.assertFalse(status["production_ready"])
        self.assertEqual(status["census"]["by_status"], {"absent": 1})
        self.assertIn(
            "evaluated_source_synchronized",
            status["blocked_reasons"],
        )

    def test_limited_sync_cannot_establish_production_readiness(self):
        corpus = self._corpus()
        limited = self._run_sync(corpus, max_files=1)
        self.assertEqual(limited["status"], "completed")
        receipt = self._receipt(corpus)
        with mock.patch.object(
            skillops,
            "_load_n8n_release_approval",
            return_value=self._approval(corpus, receipt),
        ):
            status = skillops.get_n8n_corpus_status()

        self.assertFalse(status["production_ready"])
        self.assertEqual(status["latest_sync"]["status"], "no_recorded_sync")
        self.assertIn("synchronization_healthy", status["blocked_reasons"])

    def test_lock_contention_does_not_supersede_healthy_full_sync(self):
        corpus = self._corpus()
        self._run_sync(corpus)
        with mock.patch.object(
            skillops, "_SYNC_LOCK_PATH", self.lock_path
        ):
            with skillops._vector_sync_lock() as acquired:
                self.assertTrue(acquired)
                with mock.patch.object(
                    skillops,
                    "_require_vector_store_id",
                    return_value="vs-n8n",
                ):
                    locked = skillops.sync_vector_store()
        self.assertEqual(locked["status"], "locked")
        receipt = self._receipt(corpus)
        with mock.patch.object(
            skillops,
            "_load_n8n_release_approval",
            return_value=self._approval(corpus, receipt),
        ):
            status = skillops.get_n8n_corpus_status()
        self.assertTrue(status["production_ready"])
        self.assertEqual(status["latest_sync"]["status"], "completed")

    def test_invalid_sync_is_terminal_and_dry_run_is_mutation_free(self):
        invalid = self._corpus(top5_passed=8)
        dry_run = self._run_sync(invalid, dry_run=True)
        self.assertEqual(dry_run["status"], "partial_failed")
        self.assertEqual(
            skillops.get_n8n_corpus_status()["census"]["count"],
            0,
        )

        synchronized = self._run_sync(invalid)
        self.assertEqual(synchronized["status"], "partial_failed")
        status = skillops.get_n8n_corpus_status()
        self.assertEqual(status["capability_count"], 0)
        self.assertEqual(status["census"]["by_status"], {"invalid": 1})
        self.assertEqual(status["census"]["pending_count"], 0)

    def test_plan_only_input_is_not_a_production_corpus(self):
        plan = """\
# n8n Capability Corpus Implementation Plan
Create a 40-80 record governed corpus, then evaluate and synchronize it.
"""
        preflight = skillops.preflight_n8n_corpus_text(plan)
        self.assertEqual(preflight["status"], "blocked")
        self.assertFalse(preflight["ingestion_ready"])
        self.assertEqual(preflight["records_valid"], 0)

    def test_model_callable_learning_and_sync_require_approval(self):
        for tool_name in ("learn_from_vector_store", "sync_vector_store"):
            with self.subTest(tool_name=tool_name):
                result = skills.dispatch(tool_name, {"dry_run": True})
                self.assertIn("requires explicit approval", result["error"])

    def test_capability_snapshot_reports_blocked_n8n_state(self):
        snapshot = skills.get_pj_capability_snapshot()
        self.assertEqual(snapshot["n8n_capabilities"]["count"], 0)
        self.assertEqual(snapshot["n8n_capabilities"]["status"], "blocked")
        self.assertFalse(
            snapshot["n8n_capabilities"]["production_ready"]
        )

    def test_generated_skill_cannot_override_capability_snapshot(self):
        errors = skillops._validate_code(
            "get_pj_capability_snapshot",
            "def run(**kwargs):\n    return {'forged': True}\n",
        )
        self.assertIn(
            "'get_pj_capability_snapshot' is a reserved built-in skill name",
            errors,
        )

    def test_ingestion_preflight_runs_before_credential_access(self):
        source = Path(self.temp_dir.name) / "n8n-plan.md"
        source.write_text("# n8n plan\nNo production records.\n")
        receipt = Path(self.temp_dir.name) / "evaluation.json"
        receipt.write_text("{}")
        argv = [
            "vector_store_ingest.py",
            str(source),
            "--corpus-type",
            "n8n",
            "--evaluation-receipt",
            str(receipt),
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(vector_store_ingest, "_api_key") as api_key,
        ):
            with self.assertRaisesRegex(ValueError, "before credential access"):
                vector_store_ingest.main()
        api_key.assert_not_called()

    def test_ingestion_uploads_preflighted_bytes_after_source_changes(self):
        source = Path(self.temp_dir.name) / "n8n-corpus.md"
        corpus = self._corpus()
        original_bytes = corpus.encode("utf-8")
        source.write_bytes(original_bytes)
        receipt_value = self._receipt(corpus)
        receipt = Path(self.temp_dir.name) / "evaluation.json"
        receipt.write_text(json.dumps(receipt_value))
        argv = [
            "vector_store_ingest.py",
            str(source),
            "--corpus-type",
            "n8n",
            "--evaluation-receipt",
            str(receipt),
        ]
        uploaded = {}

        def mutate_source_after_preflight(_service):
            source.write_text("mutated after preflight")
            return "test-api-key"

        def post(url, **kwargs):
            response = mock.Mock()
            response.raise_for_status.return_value = None
            if url == "https://api.openai.com/v1/files":
                uploaded["bytes"] = kwargs["files"]["file"][1].read()
                uploaded["authorization"] = kwargs["headers"]["Authorization"]
                response.json.return_value = {"id": "file-uploaded"}
            else:
                response.json.return_value = {"status": "in_progress"}
            return response

        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                skillops,
                "_load_n8n_release_approval",
                return_value=self._approval(corpus, receipt_value),
            ),
            mock.patch.object(
                vector_store_ingest,
                "_api_key",
                side_effect=mutate_source_after_preflight,
            ),
            mock.patch.object(
                skillops,
                "_require_vector_store_id",
                return_value="vs-n8n",
            ),
            mock.patch.object(
                vector_store_ingest,
                "_cached_file_id",
                return_value="",
            ),
            mock.patch.object(
                vector_store_ingest,
                "_membership",
                return_value=(404, {}),
            ),
            mock.patch.object(
                vector_store_ingest,
                "_wait_until_indexed",
                return_value={"status": "completed"},
            ),
            mock.patch.object(
                vector_store_ingest.requests,
                "post",
                side_effect=post,
            ),
            mock.patch.object(
                skillops,
                "cache_vector_source",
                return_value={"status": "cached"},
            ),
            mock.patch.object(
                skillops,
                "cache_n8n_evaluation_receipt",
                return_value={"status": "cached"},
            ),
            mock.patch("builtins.print"),
        ):
            result = vector_store_ingest.main()

        self.assertEqual(result, 0)
        self.assertEqual(source.read_text(), "mutated after preflight")
        self.assertEqual(uploaded["bytes"], original_bytes)
        self.assertEqual(uploaded["authorization"], "Bearer test-api-key")


class TestCodingCapabilityCorpus(unittest.TestCase):
    FULL_CORPUS_PATH = Path(os.getenv(
        "PJ_CODING_CORPUS_PATH",
        (
            "/Users/matthewbernal/.copilot/workspaces/"
            "78c81e10-227a-4773-868d-0e0ad54e5888/attachments/"
            "9ad42745-f1dd-4977-8f7d-2e9783f33195-"
            "AI_Coding_Tools_Vector_Training_Corpus_v1.0.md"
        ),
    ))
    CAPABILITY_FIXTURES = (
        ("DOC-415", "Codex Cloud - OpenAI Coding Agent Documentation",
         "OpenAI Codex", "Cloud / web coding agent"),
        ("DOC-43", "Codex IDE extension",
         "OpenAI Codex", "IDE extension"),
        ("DOC-407", "Codex SDK",
         "OpenAI Codex", "SDK / programmatic and CI integration"),
        ("DOC-405", "GPT5.1 Codex",
         "OpenAI Codex", "CLI and shared configuration"),
        ("DOC-400", "GPT-5.1-Codex-Max System Card",
         "OpenAI Codex", "GPT-5.1-Codex-Max safety and model profile"),
        ("DOC-393", "Guide - Using GPT-5.1 - AI Engineering",
         "OpenAI GPT-5.1", "API engineering guide"),
        ("DOC-397", "Front End Coding with GPT5",
         "OpenAI GPT-5", "Front-end coding workflow"),
        ("DOC-399", "5.1 For Developers- Blog",
         "OpenAI GPT-5.1", "Developer release overview"),
    )

    def setUp(self):
        self.original_db_path = skillops._DB_PATH
        self.original_docops_db_path = docops._DB_PATH
        self.original_codeops_db_path = codeops._DB_PATH
        self.temp_db_path = Path(tempfile.mkstemp(suffix=".sqlite3")[1])
        skillops._DB_PATH = self.temp_db_path
        docops._DB_PATH = self.temp_db_path
        codeops._DB_PATH = self.temp_db_path
        self.lock_path = Path(tempfile.mkdtemp()) / "coding-sync.lock"

    def tearDown(self):
        skillops._DB_PATH = self.original_db_path
        docops._DB_PATH = self.original_docops_db_path
        codeops._DB_PATH = self.original_codeops_db_path
        self.temp_db_path.unlink(missing_ok=True)

    def _eight_record_corpus(self):
        items = []
        for item_id, title, family, surface in self.CAPABILITY_FIXTURES:
            items.append(f"""\
---ITEM_START: {item_id}---
```yaml
item_id: "{item_id}"
source_record_id: "{item_id}"
canonical_title: "{title}"
tool_family: "{family}"
surface: "{surface}"
version_scope: "verify_current"
corpus_status: "training_ready_current_docs_override"
requires_current_docs_check: true
```
### {title}
**What this item teaches:** Guidance for {surface}.
#### Appropriate tasks
- select this capability for matching coding work
#### Recommended operating workflow
1. Verify current documentation.
2. Execute a bounded task.
#### Safety and governance controls
- Use least privilege.
#### Current authoritative sources
- https://developers.openai.com/codex
---ITEM_END: {item_id}---
""")
        return (
            "# AI CODING TOOLS - VECTOR-STORE TRAINING CORPUS\n"
            "corpus_version: 1.0.0\n"
            "record_count: 8\n\n"
            "## TRAINING ITEMS\n\n"
            + "\n".join(items)
        )

    def test_yaml_metadata_precedes_embedded_json_examples(self):
        corpus = """\
# AI CODING TOOLS — VECTOR-STORE TRAINING CORPUS
corpus_version: 1.0.0
record_count: 1
---ITEM_START: CAP-1---
```yaml
item_id: "CAP-1"
source_record_id: "CAP-1"
canonical_title: "Structured Coding Agent"
tool_family: "Example Tools"
surface: "SDK"
version_scope: "verify_current"
corpus_status: "training_ready_current_docs_override"
requires_current_docs_check: true
```
### Structured Coding Agent
**What this item teaches:** Structured automation.
#### Appropriate tasks
- automate bounded repository work
#### Recommended operating workflow
1. Define a bounded task.
2. Validate the output.
#### Safety and governance controls
- Use least privilege.
#### Current authoritative sources
- https://example.com/current
#### Source-derived reference content
```json
{"type": "object", "properties": {"wrong": {"type": "string"}}}
```
---ITEM_END: CAP-1---
"""
        result = skillops.import_coding_capability_corpus_text(
            corpus,
            dry_run=False,
            source_file_id="representative.md",
        )
        self.assertEqual(result["items_total"], 1)
        self.assertEqual(result["capabilities_created"], 1)
        self.assertEqual(result["items_skipped_invalid"], 0)
        capability = skillops.list_coding_capabilities()["capabilities"][0]
        self.assertEqual(capability["item_id"], "CAP-1")
        self.assertEqual(capability["canonical_title"], "Structured Coding Agent")
        self.assertTrue(capability["requires_current_docs_check"])
        self.assertEqual(
            capability["workflow"],
            ["Define a bounded task.", "Validate the output."],
        )

    def test_docops_content_with_capability_phrases_is_not_misrouted(self):
        docops_text = """\
---ITEM_START: DOCOPS-TOOL---
item_id: DOCOPS-TOOL
canonical_title: Tool Family Brief
template_name: tool_family_brief
description: Includes phrases also used by coding guidance.
required_sections:
- Summary
tool_family: Example
**What this item teaches:** This remains a DocOps template.
---ITEM_END: DOCOPS-TOOL---
"""
        self.assertFalse(skillops._is_coding_capability_corpus(docops_text))
        result = skillops._import_vector_content(
            docops_text,
            overwrite_existing=True,
            include_provisional=True,
            dry_run=True,
        )
        self.assertEqual(result["items_total"], 1)
        self.assertEqual(result["templates_created"], 1)

    def test_corpus_header_without_capability_metadata_is_not_misrouted(self):
        docops_text = """\
# AI CODING TOOLS - VECTOR-STORE TRAINING CORPUS
corpus_version: 1.0.0
record_count: 1
## TRAINING ITEMS
---ITEM_START: DOCOPS-1---
item_id: DOCOPS-1
canonical_title: Ordinary DocOps Template
template_name: ordinary_docops_template
required_sections:
- Summary
---ITEM_END: DOCOPS-1---
"""
        self.assertFalse(skillops._is_coding_capability_corpus(docops_text))
        self.assertFalse(
            skillops._looks_like_coding_capability_corpus(docops_text)
        )

    def test_malformed_coding_corpus_fails_closed_in_all_sync_paths(self):
        text = self._eight_record_corpus().replace(
            "record_count: 8",
            "record_count: 9",
            1,
        )
        damaged_header = text.replace(
            "# AI CODING TOOLS - VECTOR-STORE TRAINING CORPUS",
            "# DAMAGED CORPUS HEADER",
            1,
        )
        damaged_heading = text.replace(
            "## TRAINING ITEMS",
            "## DAMAGED TRAINING HEADING",
            1,
        )
        self.assertTrue(skillops._looks_like_coding_capability_corpus(text))
        self.assertTrue(
            skillops._looks_like_coding_capability_corpus(damaged_header)
        )
        self.assertTrue(
            skillops._looks_like_coding_capability_corpus(damaged_heading)
        )
        self.assertFalse(skillops._is_coding_capability_corpus(text))
        routed = skillops._import_vector_content(
            text,
            overwrite_existing=True,
            include_provisional=True,
            dry_run=True,
            audit=False,
        )
        self.assertEqual(routed["corpus_type"], "ai_coding_capabilities")
        self.assertGreater(routed["items_skipped_invalid"], 0)
        self.assertEqual(routed["capabilities_created"], 0)

        entry = {"id": "vs-file-invalid", "file_id": "file-invalid"}
        metadata = {
            "id": "file-invalid",
            "filename": "invalid-coding-corpus.md",
            "bytes": len(text.encode("utf-8")),
        }
        patches = (
            mock.patch.object(skillops, "_SYNC_LOCK_PATH", self.lock_path),
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
                return_value={"file-invalid": metadata},
            ),
            mock.patch.object(
                skillops, "_get_openai_file_metadata", return_value=metadata
            ),
            mock.patch.object(
                skillops,
                "_read_openai_file_content",
                return_value=(text, False),
            ),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patches[5], patches[6]:
            automatic = skillops.sync_vector_store(dry_run=True)
        self.assertEqual(automatic["status"], "partial_failed")
        self.assertEqual(automatic["files_failed"], 1)
        self.assertEqual(automatic["capabilities_created"], 0)
        self.assertEqual(automatic["templates_created"], 0)
        self.assertGreater(automatic["items_skipped_invalid"], 0)
        sync_status = skillops.get_vector_sync_status(limit=1)["runs"][0]
        self.assertEqual(
            sync_status["items_skipped_invalid"],
            automatic["items_skipped_invalid"],
        )

        with patches[1], patches[2], patches[3], patches[4], patches[6]:
            on_demand = skillops.learn_from_vector_store(
                dry_run=True,
                overwrite_existing=True,
            )
        self.assertEqual(on_demand["status"], "failed")
        self.assertEqual(skillops.list_coding_capabilities()["count"], 0)

    def test_required_corpus_and_freshness_metadata_fail_closed(self):
        corpus = self._eight_record_corpus()
        invalid_variants = (
            corpus.replace("record_count: 8\n", "", 1),
            corpus.replace("corpus_version: 1.0.0\n", "", 1),
            corpus.replace("requires_current_docs_check: true\n", ""),
        )
        for invalid in invalid_variants:
            with self.subTest():
                self.assertTrue(
                    skillops._looks_like_coding_capability_corpus(invalid)
                )
                result = skillops.import_coding_capability_corpus_text(
                    invalid,
                    dry_run=True,
                    audit=False,
                )
                self.assertGreater(result["items_skipped_invalid"], 0)
                self.assertEqual(result["capabilities_created"], 0)
                self.assertTrue(result["errors"])

    def test_required_metadata_is_not_sourced_from_reference_body(self):
        corpus = self._eight_record_corpus()
        moved = corpus.replace("corpus_version: 1.0.0\n", "", 1)
        moved = moved.replace("record_count: 8\n", "", 1)
        moved = moved.replace(
            "requires_current_docs_check: true\n",
            "",
            1,
        )
        moved = moved.replace(
            "#### Appropriate tasks",
            "#### Source-derived reference content\n"
            "corpus_version: 1.0.0\n"
            "record_count: 8\n"
            "requires_current_docs_check: true\n"
            "#### Appropriate tasks",
            1,
        )
        result = skillops.import_coding_capability_corpus_text(
            moved,
            dry_run=True,
            audit=False,
        )
        self.assertGreater(result["items_skipped_invalid"], 0)
        self.assertEqual(result["capabilities_created"], 0)
        self.assertTrue(any(
            "record_count" in error for error in result["errors"]
        ))
        self.assertTrue(any(
            "corpus_version" in error for error in result["errors"]
        ))
        self.assertTrue(any(
            "requires_current_docs_check" in error
            for error in result["errors"]
        ))

    def test_malformed_fallback_corpora_do_not_commit_partial_records(self):
        malformed_docops = """\
---ITEM_START: DOC-COMPLETE---
item_id: DOC-COMPLETE
canonical_title: Complete But Uncommitted
template_name: complete_but_uncommitted
required_sections:
- Summary
---ITEM_END: DOC-COMPLETE---
---ITEM_START: DOC-TRUNCATED---
item_id: DOC-TRUNCATED
"""
        doc_result = skillops._import_vector_content(
            malformed_docops,
            overwrite_existing=True,
            include_provisional=True,
            dry_run=False,
        )
        self.assertEqual(doc_result["status"], "invalid")
        self.assertNotIn(
            "complete_but_uncommitted",
            {
                template["name"]
                for template in docops.list_doc_templates()["templates"]
            },
        )

        malformed_codeops = """\
---ITEM_START: CODE-COMPLETE---
```yaml
item_id: "CODE-COMPLETE"
source_page_url: "https://example.test/code"
source_record_id: "CODE-COMPLETE"
canonical_title: "Complete But Uncommitted CodeOps"
tool_family: "CodeOps"
surface: "CLI"
version_scope: "historical"
corpus_status: "training_ready_current_docs_override"
requires_current_docs_check: true
content_sha256: "code-hash"
```
#### Prompt contract
State the objective.
#### Output contract
Return evidence.
---ITEM_END: CODE-COMPLETE---
---ITEM_START: CODE-TRUNCATED---
"""
        code_result = skillops._import_vector_content(
            malformed_codeops,
            overwrite_existing=True,
            include_provisional=True,
            dry_run=False,
        )
        self.assertEqual(code_result["status"], "invalid")
        self.assertNotIn(
            "CODE-COMPLETE",
            {
                guide["item_id"]
                for guide in codeops.list_codeops_guides(limit=100)["guides"]
            },
        )

        malformed_metadata_codeops = """\
---ITEM_START: CODE-MISSING---
```yaml
item_id: "CODE-MISSING"
source_record_id: "CODE-MISSING"
canonical_title: "Malformed CodeOps"
tool_family: "CodeOps"
surface: "CLI"
version_scope: "historical"
corpus_status: "training_ready_current_docs_override"
requires_current_docs_check: true
content_sha256: "missing-source-url"
```
#### Prompt contract
State the objective.
#### Output contract
Return evidence.
## Embedded source heading
This must not turn the item into a DocOps template.
---ITEM_END: CODE-MISSING---
"""
        malformed_result = skillops._import_vector_content(
            malformed_metadata_codeops,
            overwrite_existing=True,
            include_provisional=True,
            dry_run=False,
        )
        self.assertEqual(malformed_result["status"], "invalid")
        self.assertEqual(malformed_result["corpus_type"], "codeops_guidance")
        self.assertEqual(malformed_result["templates_created"], 0)

    def test_on_demand_semantic_preflight_prevents_partial_docops_write(self):
        first_text = """\
---ITEM_START: DOC-FIRST---
item_id: DOC-FIRST
canonical_title: First Valid But Uncommitted
template_name: first_valid_but_uncommitted
required_sections:
- Summary
---ITEM_END: DOC-FIRST---
"""
        invalid_text = """\
---ITEM_START: DOC-VALID---
item_id: DOC-VALID
canonical_title: Valid But Uncommitted
template_name: valid_but_uncommitted
required_sections:
- Summary
---ITEM_END: DOC-VALID---
---ITEM_START: DOC-INVALID---
item_id: DOC-INVALID
canonical_title: Missing Required Sections
template_name: missing_required_sections
---ITEM_END: DOC-INVALID---
"""
        entries = [
            {"id": "vs-file-first", "file_id": "file-first"},
            {"id": "vs-file-semantic", "file_id": "file-semantic"},
        ]
        metadata = {
            "file-first": {
                "id": "file-first",
                "filename": "first-valid-docops.md",
                "bytes": len(first_text.encode("utf-8")),
            },
            "file-semantic": {
                "id": "file-semantic",
                "filename": "semantic-invalid-docops.md",
                "bytes": len(invalid_text.encode("utf-8")),
            },
        }
        with (
            mock.patch.object(
                skillops, "_require_vector_store_id", return_value="vs-test"
            ),
            mock.patch.object(
                skillops, "_require_openai_api_key", return_value="test-key"
            ),
            mock.patch.object(
                skillops, "_list_vector_store_files", return_value=entries
            ),
            mock.patch.object(
                skillops,
                "_list_openai_file_metadata",
                return_value=metadata,
            ),
            mock.patch.object(
                skillops,
                "_read_openai_file_content",
                side_effect=[
                    (first_text, False),
                    (invalid_text, False),
                ],
            ),
        ):
            result = skillops.learn_from_vector_store(
                overwrite_existing=True,
            )
        self.assertEqual(result["status"], "failed")
        self.assertNotIn(
            "valid_but_uncommitted",
            {
                template["name"]
                for template in docops.list_doc_templates()["templates"]
            },
        )
        self.assertNotIn(
            "first_valid_but_uncommitted",
            {
                template["name"]
                for template in docops.list_doc_templates()["templates"]
            },
        )

    def test_capability_import_honors_provisional_policy(self):
        corpus = self._eight_record_corpus().replace(
            'corpus_status: "training_ready_current_docs_override"',
            'corpus_status: "provisional_instructional_spec"',
            1,
        )
        excluded = skillops.import_coding_capability_corpus_text(
            corpus,
            include_provisional=False,
            dry_run=True,
        )
        self.assertEqual(excluded["capabilities_created"], 7)
        self.assertEqual(excluded["items_skipped_provisional"], 1)
        self.assertEqual(
            excluded["imports"][0]["action"],
            "skipped_provisional",
        )
        audit = skillops.list_coding_capabilities()["import_audit"][0]
        self.assertFalse(audit["include_provisional"])
        self.assertEqual(audit["records_skipped_provisional"], 1)

        entry = {"id": "vs-file-provisional", "file_id": "file-provisional"}
        metadata = {
            "id": "file-provisional",
            "filename": "provisional-coding-corpus.md",
            "bytes": len(corpus.encode("utf-8")),
        }
        with (
            mock.patch.object(skillops, "_SYNC_LOCK_PATH", self.lock_path),
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
                return_value={"file-provisional": metadata},
            ),
            mock.patch.object(
                skillops, "_get_openai_file_metadata", return_value=metadata
            ),
            mock.patch.object(
                skillops,
                "_read_openai_file_content",
                return_value=(corpus, False),
            ),
        ):
            synchronized = skillops.sync_vector_store(
                dry_run=True,
                include_provisional=False,
            )
        self.assertEqual(synchronized["status"], "dry_run_complete")
        self.assertEqual(synchronized["items_skipped_provisional"], 1)
        self.assertEqual(synchronized["capabilities_created"], 7)

        included = skillops.import_coding_capability_corpus_text(
            corpus,
            include_provisional=True,
            dry_run=True,
        )
        self.assertEqual(included["capabilities_created"], 8)
        self.assertEqual(included["items_skipped_provisional"], 0)

    def test_quoted_false_freshness_flag_stays_false(self):
        corpus = self._eight_record_corpus().replace(
            "requires_current_docs_check: true",
            'requires_current_docs_check: "false"',
            1,
        )
        result = skillops.import_coding_capability_corpus_text(
            corpus,
            dry_run=False,
            source_file_id="quoted-false.md",
        )
        self.assertEqual(result["items_skipped_invalid"], 0)
        first = skillops.list_coding_capabilities(
            query="DOC-415",
        )["capabilities"][0]
        self.assertFalse(first["requires_current_docs_check"])

    def test_eight_record_import_is_idempotent_and_audited(self):
        text = self._eight_record_corpus()
        dry_run = skillops.import_coding_capability_corpus_text(
            text,
            dry_run=True,
            source_file_id="coding-corpus.md",
        )
        self.assertEqual(dry_run["items_total"], 8)
        self.assertEqual(dry_run["capabilities_created"], 8)
        self.assertEqual(dry_run["items_skipped_invalid"], 0)

        imported = skillops.import_coding_capability_corpus_text(
            text,
            dry_run=False,
            source_file_id="coding-corpus.md",
        )
        self.assertEqual(imported["capabilities_created"], 8)
        repeated = skillops.import_coding_capability_corpus_text(
            text,
            dry_run=False,
            source_file_id="coding-corpus.md",
        )
        self.assertEqual(repeated["capabilities_created"], 0)
        self.assertEqual(repeated["capabilities_updated"], 0)
        self.assertEqual(repeated["capabilities_unchanged"], 8)

        listed = skillops.list_coding_capabilities()
        self.assertEqual(listed["count"], 8)
        self.assertGreaterEqual(len(listed["import_audit"]), 3)
        self.assertEqual(
            {row["item_id"] for row in listed["capabilities"]},
            {
                "DOC-415", "DOC-43", "DOC-407", "DOC-405",
                "DOC-400", "DOC-393", "DOC-397", "DOC-399",
            },
        )
        self.assertTrue(all(
            row["requires_current_docs_check"]
            for row in listed["capabilities"]
        ))

    def test_full_coding_corpus_attachment_when_available(self):
        if not self.FULL_CORPUS_PATH.exists():
            self.skipTest("AI coding tools corpus attachment is not available")
        result = skillops.import_coding_capability_corpus_text(
            self.FULL_CORPUS_PATH.read_text(),
            dry_run=True,
            source_file_id=self.FULL_CORPUS_PATH.name,
        )
        self.assertEqual(result["items_total"], 8)
        self.assertEqual(result["capabilities_created"], 8)
        self.assertEqual(result["items_skipped_invalid"], 0)

    def test_automatic_sync_routes_coding_corpus_to_capability_registry(self):
        text = self._eight_record_corpus()
        entry = {
            "id": "vs-file-coding",
            "file_id": "file-coding",
            "created_at": 300,
        }
        metadata = {
            "id": "file-coding",
            "filename": "coding-corpus.md",
            "bytes": len(text.encode("utf-8")),
            "created_at": 300,
        }
        with (
            mock.patch.object(skillops, "_SYNC_LOCK_PATH", self.lock_path),
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
                return_value={"file-coding": metadata},
            ),
            mock.patch.object(
                skillops, "_get_openai_file_metadata", return_value=metadata
            ),
            mock.patch.object(
                skillops,
                "_read_openai_file_content",
                return_value=(text, False),
            ),
        ):
            result = skillops.sync_vector_store()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["capabilities_created"], 8)
        self.assertEqual(result["templates_created"], 0)
        self.assertEqual(
            result["file_reports"][0]["corpus_type"],
            "ai_coding_capabilities",
        )

    def test_on_demand_learning_routes_coding_corpus(self):
        text = self._eight_record_corpus()
        entry = {"id": "vs-file-learn", "file_id": "file-learn"}
        metadata = {
            "id": "file-learn",
            "filename": "coding-corpus.md",
            "bytes": len(text.encode("utf-8")),
        }
        with (
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
                return_value={"file-learn": metadata},
            ),
            mock.patch.object(
                skillops,
                "_read_openai_file_content",
                return_value=(text, False),
            ),
        ):
            result = skillops.learn_from_vector_store(
                overwrite_existing=True,
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["capabilities_created"], 8)
        self.assertEqual(result["templates_created"], 0)


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
