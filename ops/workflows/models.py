"""Typed models for the bounded WorkflowOps manifest format.

The format is deliberately data-only.  It has no expression evaluator and no
field capable of carrying Python or JavaScript source.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

FORMAT_VERSION = "1.0"
STEP_TYPES = frozenset(
    {
        "prompt",
        "local_tool",
        "connector_read",
        "connector_action",
        "approval",
        "branch",
        "parallel_read",
        "validation",
        "artifact_output",
    }
)
EFFECT_TYPES = frozenset({"none", "local", "external_read", "external_write"})


class WorkflowError(ValueError):
    """A safe, operator-actionable workflow validation error."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def manifest_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class Budget:
    max_steps: int = 50
    max_cost_usd: float = 5.0
    max_duration_seconds: int = 900
    max_parallel: int = 4

    @classmethod
    def parse(cls, value: Any) -> "Budget":
        if not isinstance(value, Mapping):
            raise WorkflowError("budget must be an object")
        try:
            result = cls(
                max_steps=int(value.get("max_steps", 50)),
                max_cost_usd=float(value.get("max_cost_usd", 5)),
                max_duration_seconds=int(value.get("max_duration_seconds", 900)),
                max_parallel=int(value.get("max_parallel", 4)),
            )
        except (TypeError, ValueError) as exc:
            raise WorkflowError("budget values must be numeric") from exc
        if min(result.max_steps, result.max_duration_seconds, result.max_parallel) < 1:
            raise WorkflowError("budget limits must be positive")
        if result.max_cost_usd < 0:
            raise WorkflowError("budget.max_cost_usd cannot be negative")
        return result


@dataclass(frozen=True)
class Step:
    id: str
    type: str
    next: tuple[str, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)
    effect: str = "none"
    rollback: str | None = None

    @classmethod
    def parse(cls, value: Any) -> "Step":
        if not isinstance(value, Mapping):
            raise WorkflowError("each step must be an object")
        step_id, step_type = value.get("id"), value.get("type")
        if not isinstance(step_id, str) or not step_id.strip():
            raise WorkflowError("each step requires a non-empty id")
        if step_type not in STEP_TYPES:
            raise WorkflowError(f"step {step_id!r} has unsupported type {step_type!r}")
        successors = value.get("next", [])
        if not isinstance(successors, list) or not all(
            isinstance(item, str) for item in successors
        ):
            raise WorkflowError(f"step {step_id!r}.next must be a string array")
        config = value.get("config", {})
        if not isinstance(config, Mapping):
            raise WorkflowError(f"step {step_id!r}.config must be an object")
        effect = value.get("effect", "none")
        if effect not in EFFECT_TYPES:
            raise WorkflowError(f"step {step_id!r} has unsupported effect {effect!r}")
        rollback = value.get("rollback")
        if rollback is not None and rollback not in {"provider_supported"}:
            raise WorkflowError(f"step {step_id!r} makes an unsupported rollback claim")
        return cls(step_id, step_type, tuple(successors), dict(config), effect, rollback)


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    version: str
    author: str
    profile: Mapping[str, Any]
    inputs: Mapping[str, Any]
    outputs: Mapping[str, Any]
    steps: tuple[Step, ...]
    tools: Mapping[str, Mapping[str, Any]]
    knowledge: tuple[Mapping[str, Any], ...]
    policy: Mapping[str, Any]
    budget: Budget
    compatibility_version: str = FORMAT_VERSION
    realtime_compatible: bool = False
    source: str = "local"

    @classmethod
    def from_dict(cls, data: Any) -> "WorkflowDefinition":
        if not isinstance(data, Mapping):
            raise WorkflowError("workflow must be a JSON object")
        cls._reject_executable_fields(data)
        required = ("name", "version", "author", "input_schema", "output_schema", "steps")
        for key in required:
            if key not in data:
                raise WorkflowError(f"missing required field: {key}")
        for key in ("name", "version", "author"):
            if not isinstance(data[key], str) or not data[key].strip():
                raise WorkflowError(f"{key} must be a non-empty string")
        steps_raw = data["steps"]
        if not isinstance(steps_raw, list) or not steps_raw:
            raise WorkflowError("steps must be a non-empty array")
        tools = data.get("tools", {})
        if not isinstance(tools, Mapping):
            raise WorkflowError("tools must be an object")
        knowledge = data.get("knowledge", [])
        if not isinstance(knowledge, list) or not all(isinstance(x, Mapping) for x in knowledge):
            raise WorkflowError("knowledge must be an array of objects")
        schemas = (data["input_schema"], data["output_schema"])
        if not all(isinstance(schema, Mapping) for schema in schemas):
            raise WorkflowError("input_schema and output_schema must be objects")
        return cls(
            name=data["name"],
            version=data["version"],
            author=data["author"],
            profile=dict(data.get("assistant_profile", {})),
            inputs=dict(schemas[0]),
            outputs=dict(schemas[1]),
            steps=tuple(Step.parse(x) for x in steps_raw),
            tools={str(k): dict(v) for k, v in tools.items() if isinstance(v, Mapping)},
            knowledge=tuple(dict(x) for x in knowledge),
            policy=dict(data.get("policy", {})),
            budget=Budget.parse(data.get("budget", {})),
            compatibility_version=str(data.get("compatibility_version", FORMAT_VERSION)),
            realtime_compatible=bool(data.get("realtime_compatible", False)),
            source=str(data.get("source", "local")),
        )

    @classmethod
    def _reject_executable_fields(cls, value: Any) -> None:
        forbidden = {"python", "javascript", "script", "source_code", "eval", "exec"}
        if isinstance(value, Mapping):
            found = forbidden.intersection(str(key).lower() for key in value)
            if found:
                raise WorkflowError(f"executable fields are prohibited: {', '.join(sorted(found))}")
            for child in value.values():
                cls._reject_executable_fields(child)
        elif isinstance(value, list):
            for child in value:
                cls._reject_executable_fields(child)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "assistant_profile": dict(self.profile),
            "input_schema": dict(self.inputs),
            "output_schema": dict(self.outputs),
            "steps": [
                {
                    "id": s.id,
                    "type": s.type,
                    "next": list(s.next),
                    "config": dict(s.config),
                    "effect": s.effect,
                    **({"rollback": s.rollback} if s.rollback else {}),
                }
                for s in self.steps
            ],
            "tools": {k: dict(v) for k, v in self.tools.items()},
            "knowledge": [dict(x) for x in self.knowledge],
            "policy": dict(self.policy),
            "budget": self.budget.__dict__,
            "compatibility_version": self.compatibility_version,
            "realtime_compatible": self.realtime_compatible,
            "source": self.source,
        }
