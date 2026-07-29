import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ops.workflows import (
    EvaluationHarness,
    WorkflowCompiler,
    WorkflowDefinition,
    WorkflowError,
    WorkflowStore,
    export_package,
    import_package,
)


def manifest(**changes):
    value = {
        "name": "send-report",
        "version": "1",
        "author": "owner",
        "input_schema": {
            "type": "object",
            "required": ["topic"],
            "properties": {"topic": {"type": "string"}},
        },
        "output_schema": {
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        },
        "tools": {"mail": {"permissions": ["mail.send"]}},
        "knowledge": [{"source": "approved-handbook"}],
        "policy": {"data_classification": "internal"},
        "budget": {"max_steps": 10, "max_cost_usd": 2, "max_duration_seconds": 60},
        "steps": [
            {
                "id": "draft",
                "type": "prompt",
                "next": ["approve"],
                "config": {"estimated_cost_usd": 0.1},
            },
            {"id": "approve", "type": "approval", "next": ["send"]},
            {
                "id": "send",
                "type": "connector_action",
                "effect": "external_write",
                "config": {"tool": "mail"},
            },
        ],
    }
    value.update(changes)
    return value


class WorkflowCompilerTests(unittest.TestCase):
    def setUp(self):
        self.compiler = WorkflowCompiler({"mail"}, max_cost_usd=10)

    def test_compile_and_dry_run_never_execute(self):
        compiled = self.compiler.compile(WorkflowDefinition.from_dict(manifest()))
        result = self.compiler.dry_run(compiled, {"topic": "Quarterly review"})
        self.assertFalse(result["executed"])
        self.assertEqual(result["predicted_tools"], ["mail"])
        self.assertEqual(result["approvals"], ["approve"])
        self.assertEqual(result["potential_external_effects"], ["send"])

    def test_malformed_graph_cycle_and_missing_node(self):
        for steps in (
            [{"id": "a", "type": "prompt", "next": ["a"]}],
            [{"id": "a", "type": "prompt", "next": ["missing"]}],
        ):
            with self.subTest(steps=steps), self.assertRaises(WorkflowError):
                self.compiler.compile(WorkflowDefinition.from_dict(manifest(steps=steps)))

    def test_policy_bypass_and_unsupported_steps_are_rejected(self):
        bypass = manifest(
            steps=[
                {
                    "id": "send",
                    "type": "connector_action",
                    "effect": "external_write",
                    "config": {"tool": "mail"},
                }
            ]
        )
        with self.assertRaisesRegex(WorkflowError, "approval"):
            self.compiler.compile(WorkflowDefinition.from_dict(bypass))
        with self.assertRaisesRegex(WorkflowError, "unsupported"):
            WorkflowDefinition.from_dict(manifest(steps=[{"id": "x", "type": "python"}]))
        with self.assertRaisesRegex(WorkflowError, "executable"):
            WorkflowDefinition.from_dict(manifest(python="print('unsafe')"))
        nested = manifest()
        nested["steps"][0]["config"]["javascript"] = "fetch('unsafe')"
        with self.assertRaisesRegex(WorkflowError, "executable"):
            WorkflowDefinition.from_dict(nested)

    def test_tool_budget_realtime_rollback_and_input_validation(self):
        bad_values = [
            manifest(steps=[{"id": "x", "type": "local_tool", "config": {"tool": "missing"}}]),
            manifest(budget={"max_cost_usd": 999}),
            manifest(realtime_compatible=True),
            manifest(
                steps=[
                    {
                        "id": "x",
                        "type": "local_tool",
                        "rollback": "provider_supported",
                        "config": {"tool": "mail"},
                    }
                ]
            ),
        ]
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(WorkflowError):
                self.compiler.compile(WorkflowDefinition.from_dict(value))
        compiled = self.compiler.compile(WorkflowDefinition.from_dict(manifest()))
        with self.assertRaisesRegex(WorkflowError, "schema mismatch"):
            self.compiler.dry_run(compiled, {})


class WorkflowPersistenceTests(unittest.TestCase):
    def test_immutable_versions_activation_rollback_and_jobs(self):
        compiled = WorkflowCompiler({"mail"}).compile(WorkflowDefinition.from_dict(manifest()))
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(Path(directory) / "workflow.sqlite3")
            store.publish(compiled, {"passed": True}, activate=True)
            with self.assertRaisesRegex(WorkflowError, "immutable"):
                store.publish(compiled, {}, activate=False)
            job = store.create_job(compiled, {"topic": "x"})
            self.assertEqual(store.get_job(job)["status"], "queued")
            v2 = dict(manifest(version="2"))
            v2["tools"] = {"mail": {"permissions": ["mail.send", "contacts.read"]}}
            compiled2 = WorkflowCompiler({"mail"}).compile(WorkflowDefinition.from_dict(v2))
            store.publish(compiled2, {"passed": True}, activate=True)
            store.activate("send-report", "1")
            with self.assertRaisesRegex(WorkflowError, "active"):
                store.create_job(compiled2, {"topic": "x"})

    def test_signed_package_and_import_traversal(self):
        compiled = WorkflowCompiler({"mail"}).compile(WorkflowDefinition.from_dict(manifest()))
        package = export_package(compiled, b"test-key")
        restored = import_package(package, {"mail"}, b"test-key")
        self.assertEqual(restored.manifest_hash, compiled.manifest_hash)
        target = io.BytesIO()
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("../manifest.json", "{}")
            archive.writestr("checksums.json", "{}")
            archive.writestr("signature.txt", "")
        with self.assertRaises(WorkflowError):
            import_package(target.getvalue(), {"mail"})


class WorkflowEvaluationTests(unittest.TestCase):
    def test_mock_evaluation_regression_and_secret_rejection(self):
        compiled = WorkflowCompiler({"mail"}).compile(WorkflowDefinition.from_dict(manifest()))
        harness = EvaluationHarness()
        fixture = {
            "mocks": {"mail": {"result": "mocked"}},
            "cases": [{"id": "success", "input": {"topic": "x"}, "expected": {"ok": True}}],
        }
        result = harness.evaluate(compiled, fixture, lambda *_: {"ok": False}, {"success": True})
        self.assertFalse(result.passed)
        self.assertEqual(result.regressions, ("success",))
        with self.assertRaisesRegex(WorkflowError, "secrets"):
            harness.evaluate(compiled, {"api_key": "production"}, lambda *_: {})

    def test_generated_skills_remain_outside_workflow_step_types(self):
        schema = json.loads(Path("schemas/workflow-v1.schema.json").read_text())
        allowed = schema["properties"]["steps"]["items"]["properties"]["type"]["enum"]
        self.assertNotIn("generated_skill", allowed)
        self.assertNotIn("script", allowed)


if __name__ == "__main__":
    unittest.main()
