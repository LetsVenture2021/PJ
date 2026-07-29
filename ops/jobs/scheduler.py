"""Deterministic UTC scheduling with bounded restart catch-up."""

from __future__ import annotations

from datetime import datetime, timezone

from ops.jobs.models import MissedRunPolicy, Schedule, TriggerKind


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
