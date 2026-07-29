"""Typed local schedules. Event schedules intentionally do not exist yet."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo


class MissedRunPolicy(StrEnum):
    SKIP = "skip"
    RUN_ONCE = "run_once"


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
    missed_run_policy: MissedRunPolicy = MissedRunPolicy.RUN_ONCE

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
