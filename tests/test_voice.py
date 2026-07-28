import json
import unittest
from unittest.mock import patch

import voice


class TestVoiceToolApproval(unittest.TestCase):
    def test_approval_gated_tool_requires_owner_confirmation(self):
        with (
            patch.object(
                voice.skills,
                "tool_policy_mode",
                return_value="approval",
            ),
            patch.object(
                voice,
                "dispatch_realtime_function",
                return_value={"status": "approved"},
            ) as dispatch,
        ):
            result = json.loads(voice._run_tool_call(
                "sync_vector_store",
                '{"dry_run": true}',
                approval_handler=lambda event: True,
            ))

        self.assertEqual(result, {"status": "approved"})
        dispatch.assert_called_once_with(
            "sync_vector_store",
            {"dry_run": True},
            approval_granted=True,
        )

    def test_rejected_tool_is_not_dispatched(self):
        with (
            patch.object(
                voice.skills,
                "tool_policy_mode",
                return_value="approval",
            ),
            patch.object(voice, "dispatch_realtime_function") as dispatch,
        ):
            result = json.loads(voice._run_tool_call(
                "run_codeops_validation",
                '{"task_id": "task-123"}',
                approval_handler=lambda event: False,
            ))

        self.assertIn("rejected by the owner", result["error"])
        dispatch.assert_not_called()

    def test_allowed_tool_dispatches_without_approval_flag(self):
        with (
            patch.object(
                voice.skills,
                "tool_policy_mode",
                return_value="allow",
            ),
            patch.object(
                voice,
                "dispatch_realtime_function",
                return_value={"ok": True},
            ) as dispatch,
        ):
            result = json.loads(voice._run_tool_call(
                "search_notes",
                '{"query": "release"}',
            ))

        self.assertEqual(result, {"ok": True})
        dispatch.assert_called_once_with(
            "search_notes",
            {"query": "release"},
        )


if __name__ == "__main__":
    unittest.main()
