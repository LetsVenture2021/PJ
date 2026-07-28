import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import skillops
import skills


@contextmanager
def _rows_from(rows):
    connection = Mock()
    connection.execute.return_value = SimpleNamespace(fetchall=lambda: rows)
    yield connection


class SkillDispatchTests(unittest.TestCase):
    def _dispatch(self, handler, arguments=None, *, policy="allow", approved=False):
        arguments = {"value": "input"} if arguments is None else arguments
        with (
            patch.dict(skills.DISPATCH_TABLE, {"dispatch_probe": handler}),
            patch.object(skills, "_tool_policy_mode", return_value=policy),
            patch.object(skills._skillops, "record_invocation") as telemetry,
        ):
            result = skills.dispatch(
                "dispatch_probe",
                arguments,
                approval_granted=approved,
            )
        return result, telemetry

    def test_routes_registered_skill_with_keyword_arguments(self):
        handler = Mock(return_value={"selected": "primary"})

        result, telemetry = self._dispatch(handler)

        self.assertEqual(result, {"selected": "primary"})
        handler.assert_called_once_with(value="input")
        telemetry.assert_called_once()
        self.assertEqual(telemetry.call_args.args[0:2], ("dispatch_probe", True))

    def test_routes_approved_skill_when_trusted_approval_is_granted(self):
        handler = Mock(return_value={"selected": "approved"})

        result, _ = self._dispatch(
            handler,
            policy="approval",
            approved=True,
        )

        self.assertEqual(result, {"selected": "approved"})
        handler.assert_called_once_with(value="input")

    def test_policy_block_prevents_handler_selection(self):
        handler = Mock(return_value={"must_not": "run"})

        result, telemetry = self._dispatch(handler, policy="deny")

        self.assertEqual(
            result,
            {"error": "Tool 'dispatch_probe' is blocked by policy (deny)."},
        )
        handler.assert_not_called()
        telemetry.assert_called_once_with(
            "dispatch_probe",
            False,
            0,
            "blocked_by_policy_deny",
        )

    def test_unknown_skill_error_is_deterministic(self):
        self.assertEqual(
            skills.dispatch("missing_dispatch_probe", {}),
            {"error": "Unknown skill: missing_dispatch_probe"},
        )

    def test_non_object_arguments_return_deterministic_errors(self):
        handler = Mock()
        for arguments in (None, [], "value", 7):
            with self.subTest(arguments=arguments):
                with patch.dict(
                    skills.DISPATCH_TABLE,
                    {"dispatch_probe": handler},
                ):
                    result = skills.dispatch("dispatch_probe", arguments)
                self.assertEqual(
                    result,
                    {
                        "error": (
                            "Invalid arguments for skill 'dispatch_probe': "
                            "expected object"
                        )
                    },
                )
        handler.assert_not_called()

    def test_handler_failures_use_typed_fallback_errors(self):
        scenarios = (
            (TypeError("wrong shape"), "tool_argument_error: wrong shape"),
            (ValueError("bad value"), "tool_value_error: bad value"),
            (RuntimeError("dependency down"), "tool_runtime_error: dependency down"),
            (LookupError("missing item"), "tool_unhandled_error: missing item"),
        )
        for exception, expected_error in scenarios:
            with self.subTest(exception=type(exception).__name__):
                handler = Mock(side_effect=exception)
                result, telemetry = self._dispatch(handler)
                self.assertEqual(result, {"error": expected_error})
                self.assertEqual(
                    telemetry.call_args.args[0:2],
                    ("dispatch_probe", False),
                )
                self.assertEqual(telemetry.call_args.args[3], expected_error)


class GeneratedSkillDispatchTests(unittest.TestCase):
    def test_loads_valid_active_skill_into_dispatch_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "generated_probe.py"
            path.write_text(
                "SCHEMA = {'type': 'function', 'name': 'generated_probe', "
                "'parameters': {'type': 'object', 'properties': {}}}\n"
                "def run(**kwargs):\n"
                "    return {'routed': kwargs}\n"
            )
            rows = [("generated_probe", str(path))]
            with patch.object(skillops, "_db", lambda: _rows_from(rows)):
                schemas, dispatch = skillops.load_generated_skills()

        self.assertEqual([schema["name"] for schema in schemas], ["generated_probe"])
        self.assertEqual(
            dispatch["generated_probe"](value="input"),
            {"routed": {"value": "input"}},
        )

    def test_database_failure_falls_back_to_empty_registry(self):
        @contextmanager
        def unavailable_database():
            raise OSError("registry unavailable")
            yield

        with patch.object(skillops, "_db", unavailable_database):
            self.assertEqual(skillops.load_generated_skills(), ([], {}))

    def test_missing_generated_module_is_skipped(self):
        rows = [("missing_generated_probe", "/does/not/exist.py")]

        with patch.object(skillops, "_db", lambda: _rows_from(rows)):
            self.assertEqual(skillops.load_generated_skills(), ([], {}))

    def test_reserved_skill_name_cannot_override_primary_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "get_current_time.py"
            path.write_text(
                "SCHEMA = {'name': 'get_current_time'}\n"
                "def run(**kwargs):\n"
                "    return {'overridden': True}\n"
            )
            rows = [("get_current_time", str(path))]
            with patch.object(skillops, "_db", lambda: _rows_from(rows)):
                self.assertEqual(skillops.load_generated_skills(), ([], {}))

    def test_broken_generated_module_is_skipped_and_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken_probe.py"
            path.write_text("raise RuntimeError('broken import')\n")
            rows = [("broken_probe", str(path))]
            with (
                patch.object(skillops, "_db", lambda: _rows_from(rows)),
                patch.object(skillops, "record_invocation") as telemetry,
            ):
                self.assertEqual(skillops.load_generated_skills(), ([], {}))

        telemetry.assert_called_once_with(
            "broken_probe",
            False,
            0,
            "load failure: broken import",
        )


if __name__ == "__main__":
    unittest.main()
