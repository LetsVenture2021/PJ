"""Deterministic evaluation with mocks and secret-safe frozen fixtures."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator

from .compiler import CompiledWorkflow
from .models import WorkflowError

SECRET_KEY = re.compile(r"(api[_-]?key|authorization|password|secret|token)", re.I)


@dataclass(frozen=True)
class EvaluationResult:
    passed: bool
    cases: tuple[dict[str, Any], ...]
    regressions: tuple[str, ...]


class EvaluationHarness:
    def load_fixture(self, path: str | Path) -> dict[str, Any]:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        self._reject_secrets(value)
        if not isinstance(value, dict) or not isinstance(value.get("cases"), list):
            raise WorkflowError("evaluation fixture must contain a cases array")
        return value

    def evaluate(
        self,
        compiled: CompiledWorkflow,
        fixture: Mapping[str, Any],
        runner: Callable[[CompiledWorkflow, Any, Mapping[str, Any]], Any],
        baseline: Mapping[str, bool] | None = None,
    ) -> EvaluationResult:
        self._reject_secrets(fixture)
        results: list[dict[str, Any]] = []
        regressions: list[str] = []
        baseline = baseline or {}
        for case in fixture.get("cases", []):
            case_id = str(case.get("id", "unnamed"))
            output = runner(compiled, case.get("input"), fixture.get("mocks", {}))
            errors = [
                e.message
                for e in Draft202012Validator(compiled.definition.outputs).iter_errors(output)
            ]
            expected = case.get("expected", {})
            accepted = not errors and all(output.get(k) == v for k, v in expected.items())
            results.append({"id": case_id, "passed": accepted, "schema_errors": errors})
            if baseline.get(case_id) is True and not accepted:
                regressions.append(case_id)
        return EvaluationResult(
            all(x["passed"] for x in results) and not regressions,
            tuple(results),
            tuple(regressions),
        )

    @classmethod
    def _reject_secrets(cls, value: Any, path: str = "fixture") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if SECRET_KEY.search(str(key)):
                    raise WorkflowError(
                        f"production secrets are prohibited in fixtures ({path}.{key})"
                    )
                cls._reject_secrets(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                cls._reject_secrets(child, f"{path}[{index}]")
