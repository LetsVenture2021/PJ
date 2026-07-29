"""Provider-neutral durable job types and job automation definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum, StrEnum
from typing import Any, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ops.workflows.models import ApprovalPolicy


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


class TriggerKind(str, Enum):
    ONCE = "once"
    RECURRING = "recurring"
    EVENT = "event"


class MissedRunPolicy(str, Enum):
    SKIP = "skip"
    CATCH_UP = "catch_up"


@dataclass(frozen=True)
class QuietHours:
    start: time
    end: time

    def contains(self, value: datetime) -> bool:
        local = value.timetz().replace(tzinfo=None)
        return (
            self.start <= local < self.end
            if self.start < self.end
            else local >= self.start or local < self.end
        )


@dataclass(frozen=True)
class RateLimit:
    runs: int
    per_seconds: int


@dataclass(frozen=True)
class Schedule:
    kind: TriggerKind
    timezone: str
    at: datetime | None = None
    interval_seconds: int | None = None
    missed_run_policy: MissedRunPolicy = MissedRunPolicy.SKIP
    max_catch_up: int = 0

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA zone") from exc
        if self.kind == TriggerKind.ONCE and self.at is None:
            raise ValueError("one-time schedules require at")
        if self.kind == TriggerKind.RECURRING and (self.interval_seconds or 0) < 1:
            raise ValueError("recurring schedules require a positive interval")
        if self.kind == TriggerKind.EVENT:
            raise ValueError("event triggers use connector-specific verified ingress")
        if self.max_catch_up < 0:
            raise ValueError("max_catch_up cannot be negative")


@dataclass(frozen=True)
class Job:
    job_id: str
    workflow_id: str
    workflow_version: int
    owner: str
    schedule: Schedule
    conditions: tuple[str, ...] = ()
    budget: float = 0.0
    quiet_hours: QuietHours | None = None
    rate_limit: RateLimit | None = None
    approval_policy: ApprovalPolicy = ApprovalPolicy.OWNER
    last_run: datetime | None = None
    next_run: datetime | None = None
    enabled: bool = True
