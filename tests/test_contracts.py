import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator, ValidationError

import promptops
import skills
from runtime_config import load_tool_policy


ROOT = Path(__file__).resolve().parents[1]

LEGACY_PROMPT_RESULT_V1_SCHEMA = {
    "type": "object",
    "properties": {
        "refined_prompt": {"type": "string", "minLength": 1},
        "changed": {"type": "boolean"},
        "version": {"const": "2.0"},
        "surface": {"type": "string", "minLength": 1},
        "intent_summary": {"type": "string"},
        "constraints_preserved": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "refined_prompt",
        "changed",
        "version",
        "surface",
        "intent_summary",
        "constraints_preserved",
    ],
}


class SchemaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema_document = json.loads((ROOT / "schemas" / "task_triage.json").read_text())
        cls.task_triage_schema = schema_document["schema"]
        cls.tool_schemas = {tool["name"]: tool["parameters"] for tool in skills.TOOL_SCHEMAS}

    def test_representative_tool_request_payloads_match_exported_schemas(self):
        payloads = {
            "add_task": {
                "title": "Publish contract tests",
                "notes": "Cover schema and policy boundaries.",
                "priority": "P1",
            },
            "run_shortcut": {
                "name": "Daily Review",
                "input_text": "Open contract tasks",
            },
        }

        for name, schema in self.tool_schemas.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)
        for name, payload in payloads.items():
            with self.subTest(payload=name):
                Draft202012Validator(self.tool_schemas[name]).validate(payload)

        with self.assertRaises(ValidationError):
            Draft202012Validator(self.tool_schemas["add_task"]).validate(
                {"notes": "A title is required."}
            )

    def test_representative_structured_response_matches_checked_in_schema(self):
        Draft202012Validator.check_schema(self.task_triage_schema)
        validator = Draft202012Validator(self.task_triage_schema)

        validator.validate(
            {
                "summary": "Two tasks need follow-up.",
                "priority": "P1",
                "next_actions": ["Assign an owner", "Set a due date"],
            }
        )
        with self.assertRaises(ValidationError):
            validator.validate(
                {
                    "summary": "Unsupported priority.",
                    "priority": "urgent",
                    "next_actions": [],
                }
            )

    def test_versioned_prompt_result_remains_compatible_with_v1_shape(self):
        result = promptops.perfect_prompt(
            None,
            {"model": "test-model"},
            "Keep this request unchanged.",
            surface="cli",
            required=False,
        )

        self.assertEqual(result["version"], promptops.PROMPT_PERFECTING_VERSION)
        Draft202012Validator(LEGACY_PROMPT_RESULT_V1_SCHEMA).validate(
            promptops.public_result(result)
        )


class ToolPolicyContractTests(unittest.TestCase):
    def _dispatch_with_policy(self, policy, *, approval_granted=False):
        probe = Mock(return_value={"ok": True})
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "tool_policy.json"
            policy_path.write_text(json.dumps(policy))
            with (
                patch.dict(
                    skills.DISPATCH_TABLE,
                    {"contract_probe": probe},
                    clear=False,
                ),
                patch.object(skills, "_TOOL_POLICY_PATH", policy_path),
                patch.object(skills._skillops, "record_invocation"),
                patch.dict(os.environ, {}, clear=True),
            ):
                result = skills.dispatch(
                    "contract_probe",
                    {"value": "representative"},
                    approval_granted=approval_granted,
                )
        return result, probe

    def test_checked_in_policy_references_exported_tools(self):
        policy = load_tool_policy(ROOT / "tool_policy.json", environ={})

        self.assertEqual(policy["default"], "allow")
        self.assertLessEqual(
            set(policy["tools"]),
            set(skills.DISPATCH_TABLE),
        )
        self.assertEqual(policy["tools"]["run_shortcut"], "approval")

    def test_explicit_allow_overrides_default_deny(self):
        result, probe = self._dispatch_with_policy(
            {
                "default": "deny",
                "tools": {"contract_probe": "allow"},
            }
        )

        self.assertEqual(result, {"ok": True})
        probe.assert_called_once_with(value="representative")

    def test_explicit_deny_blocks_tool_execution(self):
        result, probe = self._dispatch_with_policy(
            {
                "default": "allow",
                "tools": {"contract_probe": "deny"},
            }
        )

        self.assertIn("blocked by policy", result["error"])
        probe.assert_not_called()

    def test_approval_policy_requires_trusted_gate(self):
        policy = {
            "default": "allow",
            "tools": {"contract_probe": "approval"},
        }

        blocked, blocked_probe = self._dispatch_with_policy(policy)
        allowed, allowed_probe = self._dispatch_with_policy(policy, approval_granted=True)

        self.assertIn("requires explicit approval", blocked["error"])
        blocked_probe.assert_not_called()
        self.assertEqual(allowed, {"ok": True})
        allowed_probe.assert_called_once_with(value="representative")


if __name__ == "__main__":
    unittest.main()
