import json
import unittest
from types import SimpleNamespace

import promptops


class FakeResponses:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(self.output))


class FakeClient:
    def __init__(self, output):
        self.responses = FakeResponses(output)


class TestPromptOps(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "model": "gpt-test",
            "prompt_perfecting": {
                "enabled": True,
                "model": "gpt-perfect",
                "timeout_seconds": 15,
                "reasoning_effort": "low",
                "surfaces": ["cli", "full_power", "full_power_voice"],
            },
        }

    def test_structured_refinement_preserves_original_and_hashes(self):
        client = FakeClient({
            "refined_prompt": "Create a concise board memo in DOCX.",
            "intent_summary": "Create a board memo.",
            "constraints_preserved": ["DOCX", "concise"],
        })

        result = promptops.perfect_prompt(
            client,
            self.cfg,
            "make a concise board memo in DOCX",
            surface="cli",
        )

        self.assertEqual(
            result["original_prompt"],
            "make a concise board memo in DOCX",
        )
        self.assertEqual(
            result["refined_prompt"],
            "Create a concise board memo in DOCX.",
        )
        self.assertTrue(result["changed"])
        self.assertNotEqual(
            result["original_sha256"], result["refined_sha256"]
        )
        call = client.responses.calls[0]
        self.assertEqual(call["model"], "gpt-perfect")
        self.assertTrue(call["text"]["format"]["strict"])

    def test_control_response_must_remain_unchanged(self):
        client = FakeClient({
            "refined_prompt": "Approve and continue.",
            "intent_summary": "Approval.",
            "constraints_preserved": ["approve"],
        })

        with self.assertRaisesRegex(
            promptops.PromptPerfectingError,
            "control response",
        ):
            promptops.perfect_prompt(
                client, self.cfg, "approve", surface="cli"
            )

    def test_required_disabled_surface_fails_closed(self):
        cfg = {
            **self.cfg,
            "prompt_perfecting": {
                **self.cfg["prompt_perfecting"],
                "surfaces": ["cli"],
            },
        }
        with self.assertRaisesRegex(
            promptops.PromptPerfectingError,
            "not enabled",
        ):
            promptops.perfect_prompt(
                FakeClient({}), cfg, "Research this", surface="full_power"
            )

    def test_invalid_json_is_explicit_error(self):
        client = FakeClient({})
        client.responses.create = lambda **kwargs: SimpleNamespace(
            output_text="not-json"
        )

        with self.assertRaisesRegex(
            promptops.PromptPerfectingError,
            "structured JSON",
        ):
            promptops.perfect_prompt(
                client, self.cfg, "Research this", surface="cli"
            )

    def test_explicit_optional_mode_retains_original_after_provider_failure(self):
        client = FakeClient({})
        client.responses.create = lambda **kwargs: SimpleNamespace(
            output_text="not-json"
        )
        result = promptops.perfect_prompt(
            client,
            self.cfg,
            "Research this",
            surface="cli",
            required=False,
        )
        self.assertEqual(result["refined_prompt"], "Research this")
        self.assertFalse(result["changed"])
        self.assertEqual(
            result["original_sha256"],
            result["refined_sha256"],
        )

    def test_exact_literals_must_survive_refinement(self):
        client = FakeClient({
            "refined_prompt": (
                "Deploy DOC-42 on 2026-08-02 using 4 workers and review "
                "https://example.test/runbook."
            ),
            "intent_summary": "Deploy a specific release.",
            "constraints_preserved": ["DOC-42", "date", "workers", "URL"],
        })
        with self.assertRaisesRegex(
            promptops.PromptPerfectingError,
            "exact",
        ):
            promptops.perfect_prompt(
                client,
                self.cfg,
                "Deploy DOC-42 on 2026-08-01 using 4 workers and review "
                "https://example.test/runbook.",
                surface="cli",
            )

    def test_unexpected_programming_errors_are_not_masked_as_provider_errors(self):
        client = FakeClient({})
        client.responses.create = lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("programming defect")
        )
        with self.assertRaisesRegex(RuntimeError, "programming defect"):
            promptops.perfect_prompt(
                client, self.cfg, "Research this", surface="cli"
            )

    def test_invalid_config_types_return_typed_error(self):
        cfg = {
            **self.cfg,
            "prompt_perfecting": {
                **self.cfg["prompt_perfecting"],
                "enabled": "true",
            },
        }
        with self.assertRaises(promptops.PromptPerfectingError) as raised:
            promptops.settings_from_config(cfg)
        self.assertEqual(
            raised.exception.code,
            "invalid_prompt_perfecting_config",
        )


if __name__ == "__main__":
    unittest.main()
