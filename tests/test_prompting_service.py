import json
from types import SimpleNamespace
import unittest

from ops.prompting import service as prompt_service


class FakePromptProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create_response(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(self.payload))


class TestPromptPerfectingLiteralRepair(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "model": "test-model",
            "prompt_perfecting": {
                "enabled": True,
                "model": "test-model",
                "surfaces": ["full_power", "full_power_voice", "cli"],
                "timeout_seconds": 30,
                "max_input_chars": 20000,
                "max_output_chars": 30000,
                "reasoning_effort": "low",
            },
        }

    def test_prompt_perfecting_repairs_missing_protected_literals(self):
        original = "\n".join(
            [
                "Create Canva CLI setup docs from https://www.canva.dev/docs/apps/setting-up-starter-kit/.",
                "Use `canva_cli_app_scaffold_planner` and `npm create @canva/app@latest`.",
                'Preserve "false" as JSON and `False` as Python.',
                "Include --template and apps/webapp/server/logs/vector_sync_launchd.err.log.",
            ]
        )
        payload = {
            "refined_prompt": "Goal: Create concise Canva CLI setup docs.",
            "intent_summary": "Create Canva CLI documentation.",
            "constraints_preserved": [],
        }

        result = prompt_service.perfect_prompt(
            object(),
            self.cfg,
            original,
            surface="full_power",
            provider=FakePromptProvider(payload),
        )

        refined = result["refined_prompt"]
        self.assertIn("Immutable literals to preserve exactly:", refined)
        for literal in (
            "https://www.canva.dev/docs/apps/setting-up-starter-kit/",
            "`canva_cli_app_scaffold_planner`",
            "`npm create @canva/app@latest`",
            '"false"',
            "`False`",
            "--template",
            "apps/webapp/server/logs/vector_sync_launchd.err.log",
        ):
            self.assertIn(literal, refined)
        self.assertFalse(prompt_service._missing_preserved_literals(original, refined))

    def test_prompt_perfecting_does_not_add_repair_section_when_literals_survive(self):
        original = "Run `npm create @canva/app@latest` against https://www.canva.dev/docs/apps/setting-up-starter-kit/."
        payload = {
            "refined_prompt": (
                "Goal: Run `npm create @canva/app@latest` using "
                "https://www.canva.dev/docs/apps/setting-up-starter-kit/."
            ),
            "intent_summary": "Run the Canva setup command.",
            "constraints_preserved": ["Canva setup command and URL"],
        }

        result = prompt_service.perfect_prompt(
            object(),
            self.cfg,
            original,
            surface="full_power",
            provider=FakePromptProvider(payload),
        )

        self.assertNotIn("Immutable literals to preserve exactly:", result["refined_prompt"])
        self.assertFalse(
            prompt_service._missing_preserved_literals(original, result["refined_prompt"])
        )


if __name__ == "__main__":
    unittest.main()
