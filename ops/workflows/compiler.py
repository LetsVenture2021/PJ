"""Static compiler and effect-free simulation for workflow manifests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Collection, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .models import FORMAT_VERSION, WorkflowDefinition, WorkflowError, manifest_hash

REALTIME_FORBIDDEN = {"connector_action", "approval", "parallel_read", "artifact_output"}


@dataclass(frozen=True)
class CompiledWorkflow:
    definition: WorkflowDefinition
    manifest_hash: str
    required_permissions: tuple[str, ...]
    ordered_steps: tuple[str, ...]
    estimated_cost_usd: float


class WorkflowCompiler:
    def __init__(self, available_tools: Collection[str], *, max_cost_usd: float = 100.0):
        self.available_tools = frozenset(available_tools)
        self.max_cost_usd = max_cost_usd

    def compile(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        if definition.compatibility_version != FORMAT_VERSION:
            raise WorkflowError("unsupported compatibility version")
        if definition.budget.max_cost_usd > self.max_cost_usd:
            raise WorkflowError("workflow budget exceeds the operator limit")
        try:
            Draft202012Validator.check_schema(definition.inputs)
            Draft202012Validator.check_schema(definition.outputs)
        except SchemaError as exc:
            raise WorkflowError(f"invalid JSON schema: {exc.message}") from exc
        by_id = {step.id: step for step in definition.steps}
        if len(by_id) != len(definition.steps):
            raise WorkflowError("step ids must be unique")
        for step in definition.steps:
            missing = set(step.next) - by_id.keys()
            if missing:
                raise WorkflowError(f"step {step.id!r} references missing steps: {sorted(missing)}")
            if step.type in {"local_tool", "connector_read", "connector_action"}:
                tool = step.config.get("tool")
                if not isinstance(tool, str) or tool not in self.available_tools:
                    raise WorkflowError(f"step {step.id!r} references unavailable tool {tool!r}")
                if tool not in definition.tools:
                    raise WorkflowError(f"step {step.id!r} has no declared tool binding")
            if step.rollback and not bool(step.config.get("rollback_supported")):
                raise WorkflowError(f"step {step.id!r} rollback is not supported by its provider")
            if definition.realtime_compatible and step.type in REALTIME_FORBIDDEN:
                raise WorkflowError(f"step {step.id!r} is not Realtime-compatible")
        order = self._topological(definition)
        reachable = self._reachable(definition.steps[0].id, by_id)
        if reachable != set(by_id):
            raise WorkflowError(f"unreachable steps: {sorted(set(by_id) - reachable)}")
        self._validate_approvals(definition, by_id)
        permissions = sorted(
            {
                str(p)
                for binding in definition.tools.values()
                for p in binding.get("permissions", [])
            }
        )
        estimate = sum(float(step.config.get("estimated_cost_usd", 0)) for step in definition.steps)
        if estimate > definition.budget.max_cost_usd:
            raise WorkflowError("estimated step cost exceeds workflow budget")
        return CompiledWorkflow(
            definition,
            manifest_hash(definition.as_dict()),
            tuple(permissions),
            tuple(order),
            estimate,
        )

    @staticmethod
    def _topological(definition: WorkflowDefinition) -> list[str]:
        state: dict[str, int] = {}
        result: list[str] = []
        by_id = {s.id: s for s in definition.steps}

        def visit(node: str) -> None:
            if state.get(node) == 1:
                raise WorkflowError("workflow graph contains a cycle")
            if state.get(node) == 2:
                return
            state[node] = 1
            for child in by_id[node].next:
                visit(child)
            state[node] = 2
            result.append(node)

        for step in definition.steps:
            visit(step.id)
        return list(reversed(result))

    @staticmethod
    def _reachable(root: str, by_id: Mapping[str, Any]) -> set[str]:
        found, pending = set(), [root]
        while pending:
            node = pending.pop()
            if node not in found:
                found.add(node)
                pending.extend(by_id[node].next)
        return found

    @staticmethod
    def _validate_approvals(definition: WorkflowDefinition, by_id: Mapping[str, Any]) -> None:
        parents: dict[str, set[str]] = {key: set() for key in by_id}
        for step in definition.steps:
            for child in step.next:
                parents[child].add(step.id)
        for step in definition.steps:
            if step.effect != "external_write":
                continue
            seen, pending, approved = set(), list(parents[step.id]), False
            while pending:
                node = pending.pop()
                if node in seen:
                    continue
                seen.add(node)
                approved = approved or by_id[node].type == "approval"
                pending.extend(parents[node])
            if not approved:
                raise WorkflowError(f"external-write step {step.id!r} requires a prior approval")

    def dry_run(self, compiled: CompiledWorkflow, inputs: Any) -> dict[str, Any]:
        errors = sorted(
            error.message
            for error in Draft202012Validator(compiled.definition.inputs).iter_errors(inputs)
        )
        if errors:
            raise WorkflowError("input schema mismatch: " + "; ".join(errors))
        steps = compiled.definition.steps
        return {
            "manifest_hash": compiled.manifest_hash,
            "resolved_inputs": inputs,
            "predicted_tools": [s.config["tool"] for s in steps if "tool" in s.config],
            "approvals": [s.id for s in steps if s.type == "approval"],
            "connector_scopes": sorted(
                {scope for s in steps for scope in s.config.get("scopes", [])}
            ),
            "estimated_cost_usd": compiled.estimated_cost_usd,
            "potential_external_effects": [s.id for s in steps if s.effect != "none"],
            "executed": False,
        }
