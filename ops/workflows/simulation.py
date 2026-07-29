"""Workflow simulation that never invokes step executors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ops.workflows.models import Workflow


@dataclass(frozen=True)
class Simulation:
    candidate_event: Mapping[str, Any]
    conditions: tuple[tuple[str, bool], ...]
    expected_steps: tuple[str, ...]
    approvals: tuple[str, ...]
    spend_ceiling: float
    external_effects: tuple[str, ...]


def simulate(workflow: Workflow, event: Mapping[str, Any]) -> Simulation:
    """Describe a prospective run. Conditions use explicit boolean event fields."""
    conditions = tuple((name, bool(event.get(name, False))) for name in workflow.conditions)
    return Simulation(
        candidate_event=dict(event),
        conditions=conditions,
        expected_steps=tuple(step.name for step in workflow.steps),
        approvals=tuple(step.name for step in workflow.steps if step.requires_approval),
        spend_ceiling=workflow.budget,
        external_effects=tuple(step.effect for step in workflow.steps if step.effect != "internal"),
    )
