"""Deterministic scheduling with bounded restart catch-up."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ops.jobs.models import MissedRunPolicy, Schedule, TriggerKind


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
class OneTimeSchedule:
    at: datetime
    timezone: str
    missed_run_policy: MissedRunPolicy = MissedRunPolicy.CATCH_UP

    def next_after(self, value: datetime) -> datetime | None:
        target = (
            self.at.replace(tzinfo=ZoneInfo(self.timezone))
            if self.at.tzinfo is None
            else self.at.astimezone(ZoneInfo(self.timezone))
        )
        return target if target > value.astimezone(target.tzinfo) else None


@dataclass(frozen=True)
class CronSchedule:
    """A deliberately small, deterministic five-field cron schedule."""

    expression: str
    timezone: str
    missed_run_policy: MissedRunPolicy = MissedRunPolicy.SKIP
    quiet_hours: QuietHours | None = None

    def next_after(self, value: datetime) -> datetime:
        fields = self.expression.split()
        if len(fields) != 5:
            raise ValueError("cron requires five fields")
        candidate = value.astimezone(ZoneInfo(self.timezone)).replace(
            second=0, microsecond=0
        ) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60):
            values = (
                candidate.minute,
                candidate.hour,
                candidate.day,
                candidate.month,
                candidate.weekday(),
            )
            if all(
                field == "*" or int(field) == actual for field, actual in zip(fields, values)
            ) and not (self.quiet_hours and self.quiet_hours.contains(candidate)):
                return candidate
            candidate += timedelta(minutes=1)
        raise ValueError("schedule has no occurrence within one year")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("schedule timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def next_run(schedule: Schedule, *, after: datetime) -> datetime | None:
    anchor = _utc(schedule.at) if schedule.at else None
    cursor = _utc(after)
    if schedule.kind == TriggerKind.ONCE:
        return anchor if anchor and anchor > cursor else None
    assert anchor is not None and schedule.interval_seconds is not None
    elapsed = (cursor - anchor).total_seconds()
    increments = max(0, int(elapsed // schedule.interval_seconds) + 1)
    return datetime.fromtimestamp(
        anchor.timestamp() + increments * schedule.interval_seconds, timezone.utc
    )


def candidate_runs(
    schedule: Schedule, *, last_check: datetime, now: datetime
) -> tuple[datetime, ...]:
    """Return due instants; UTC arithmetic makes DST overlap/gap behavior deterministic."""
    cursor, end = _utc(last_check), _utc(now)
    due: list[datetime] = []
    while (candidate := next_run(schedule, after=cursor)) is not None and candidate <= end:
        due.append(candidate)
        cursor = candidate
    if schedule.missed_run_policy == MissedRunPolicy.SKIP:
        return tuple(due[-1:])
    return tuple(due[-schedule.max_catch_up :]) if schedule.max_catch_up else ()
