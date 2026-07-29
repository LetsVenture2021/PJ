"""Provider-neutral durable job types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence


class JobState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"


TERMINAL_STATES = {
    JobState.CANCELLED,
    JobState.SUCCEEDED,
    JobState.PARTIALLY_SUCCEEDED,
    JobState.FAILED,
}


@dataclass(frozen=True)
class Estimate:
    cost_units: int
    risk: str


@dataclass(frozen=True)
class StepDefinition:
    key: str
    input: Mapping[str, Any] = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    approval_sensitive: bool = False
    max_attempts: int = 3


@dataclass(frozen=True)
class StepResult:
    public: Mapping[str, Any] = field(default_factory=dict)
    checkpoint: bytes | None = None
    external_operation_key: str | None = None


class CancellationRequested(RuntimeError):
    """Raised cooperatively between bounded handler operations."""


class JobHandler(Protocol):
    def validate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def estimate(self, payload: Mapping[str, Any]) -> Estimate: ...
    def plan(self, payload: Mapping[str, Any]) -> Sequence[StepDefinition]: ...
    def execute_step(self, step: StepDefinition, context: "ExecutionContext") -> StepResult: ...
    def compensate(
        self, step: StepDefinition, checkpoint: bytes, context: "ExecutionContext"
    ) -> None: ...
    def summarize(self, step_results: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]: ...


class ExecutionContext(Protocol):
    job_id: str
    attempt_id: str

    def checkpoint(self, value: bytes) -> None: ...
    def check_cancelled(self) -> None: ...
    def external_effect(self, operation_key: str, request: Mapping[str, Any], call: Any) -> Any: ...
