"""Job automation definitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ops.workflows.models import ApprovalPolicy


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
