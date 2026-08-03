import unittest
from unittest.mock import patch

import pj


class TestCliWorkbench(unittest.TestCase):
    def setUp(self):
        self.manifest = {
            "experience": {
                "default_mode": "full_power_text",
                "workflows": [
                    {
                        "id": "assistant_chat",
                        "label": "Chat",
                        "status": "active",
                        "launch_prompt": "Use tools when useful.",
                        "tools": ["responses", "web_search"],
                    },
                    {
                        "id": "codex_workspace",
                        "label": "Code",
                        "status": "degraded",
                        "launch_prompt": "Inspect the repository and validate changes.",
                        "tools": ["codex_analyze", "run_codex_task"],
                    },
                ],
                "approval_boundaries": ["file_writes", "publishing"],
            }
        }

    def test_render_workbench_reports_workflows_and_approval_boundaries(self):
        output = pj._render_workbench(self.manifest)

        self.assertIn("1/2 workflows active", output)
        self.assertIn("assistant_chat", output)
        self.assertIn("codex_workspace", output)
        self.assertIn("file writes, publishing", output)

    def test_resolve_workbench_launch_builds_model_prompt(self):
        with patch.object(pj, "capability_manifest", return_value=self.manifest):
            launch = pj._resolve_workbench_launch(
                "/workbench code fix the failing test",
                {},
            )

        self.assertEqual(launch["workflow"]["id"], "codex_workspace")
        self.assertEqual(launch["user_prompt"], "fix the failing test")
        self.assertIn("Inspect the repository", launch["model_prompt"])
        self.assertIn("fix the failing test", launch["model_prompt"])

    def test_resolve_workbench_launch_requires_a_task(self):
        with patch.object(pj, "capability_manifest", return_value=self.manifest):
            launch = pj._resolve_workbench_launch("/workbench code", {})

        self.assertIn("Usage: /workbench codex_workspace <task>", launch["error"])


if __name__ == "__main__":
    unittest.main()
