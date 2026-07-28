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

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Redirect every module's DB to a throwaway file BEFORE any skill runs.
_TMP_DB = Path(tempfile.mkstemp(suffix=".sqlite3")[1])

import skills, skillops, docops, chiefops, chatlog  # noqa: E402

for _mod in (skills, skillops, docops, chiefops, chatlog):
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
        self.lock_path = Path(tempfile.mkdtemp()) / "vector-sync.lock"
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
            "purpose": "assistants",
        }

    def _run_sync(self, **kwargs):
        with (
            mock.patch.object(skillops, "_SYNC_LOCK_PATH", self.lock_path),
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
                "_get_openai_file_metadata",
                return_value=dict(self.metadata),
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
                skillops, "_require_vector_store_id", return_value="vs-test"
            ),
            mock.patch.object(
                skillops, "_require_openai_api_key", return_value="test-key"
            ),
            mock.patch.object(
                skillops, "_list_vector_store_files", return_value=[entry]
            ),
            mock.patch.object(
                skillops, "_get_openai_file_metadata", return_value=metadata
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
        self.temp_db_path = Path(tempfile.mkstemp(suffix=".sqlite3")[1])
        skillops._DB_PATH = self.temp_db_path
        self.lock_path = Path(tempfile.mkdtemp()) / "coding-sync.lock"

    def tearDown(self):
        skillops._DB_PATH = self.original_db_path
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
