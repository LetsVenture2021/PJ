"""
Smoke tests for PJ's skill system (stdlib unittest, no extra deps).

Run with:
    venv/bin/python -m unittest discover tests -v

All module-level _DB_PATH globals are redirected to a temp database so
tests never touch the real pj_data.sqlite3.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Redirect every module's DB to a throwaway file BEFORE any skill runs.
_TMP_DB = Path(tempfile.mkstemp(suffix=".sqlite3")[1])

import skills, skillops, docops, chiefops, chatlog  # noqa: E402
import realtime_server  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
