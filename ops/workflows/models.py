"""Serializable workflow automation definitions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class WorkflowKind(str, Enum):
    SUGGESTION = "suggestion"
    ACTION = "action"


class ApprovalPolicy(str, Enum):
    NONE = "none"
    OWNER = "owner"
    MANDATORY = "mandatory"


@dataclass(frozen=True)
class Step:
    name: str
    effect: str = "internal"
    estimated_cost: float = 0.0
    approval: ApprovalPolicy = ApprovalPolicy.NONE

    @property
    def requires_approval(self) -> bool:
        sensitive = {"destructive", "public", "credentialed", "paid", "irreversible"}
        return self.approval != ApprovalPolicy.NONE or self.effect in sensitive


@dataclass(frozen=True)
class Workflow:
    workflow_id: str
    version: int
    owner: str
    kind: WorkflowKind
    steps: tuple[Step, ...]
    budget: float = 0.0
    enabled: bool = True
    project_id: str = "default"
    conditions: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.workflow_id or not self.owner or self.version < 1 or self.budget < 0:
            raise ValueError(
                "workflow id, owner, positive version, and nonnegative budget required"
            )
        if self.kind == WorkflowKind.SUGGESTION and any(s.effect != "internal" for s in self.steps):
            raise ValueError("suggestion workflows cannot contain external effects")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
