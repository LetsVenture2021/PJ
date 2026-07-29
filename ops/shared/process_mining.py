"""Privacy-preserving process mining for PJ structured metadata logs."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

_EVENT_NAMES = {
    "http.request.completed",
    "http.request.started",
    "realtime.outbound_payload.invalid",
    "responses.continuation_rejected",
    "responses.turn.failed",
    "responses.turn.invalid_outbound_payload",
    "tool.execution.completed",
    "tool.execution.failed",
    "tool.execution.replayed",
    "tool.execution.started",
    "upload.accepted",
    "upload.failed",
    "upload.rejected",
}
_CASE_FIELDS = ("request_id", "upload_id", "tool_call_id", "session_id")
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_SAFE_HTTP_PATH = re.compile(r"^/[A-Za-z0-9_./-]{0,240}$")
_TERMINAL_EVENTS = (
    ".accepted",
    ".completed",
    ".failed",
    ".rejected",
    ".replayed",
    ".invalid",
    ".invalid_outbound_payload",
)


@dataclass(frozen=True)
class ProcessEvent:
    """A deliberately small, metadata-only event used for process discovery."""

    case_id: str
    activity: str
    timestamp: datetime
    duration_ms: float | None = None
    failed: bool = False
    feature: str | None = None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_activity(value: object) -> str | None:
    activity = str(value or "").strip()
    if activity in _EVENT_NAMES:
        return activity
    return None


def _feature(record: Mapping[str, Any], activity: str) -> str | None:
    tool_name = record.get("tool_name")
    if activity.startswith("tool.execution.") and isinstance(tool_name, str):
        return f"tool:{tool_name}" if _TOOL_NAME.fullmatch(tool_name) else None
    http_path = record.get("http_path")
    if activity.startswith("http.request.") and isinstance(http_path, str):
        if not _SAFE_HTTP_PATH.fullmatch(http_path):
            return None
        parts = http_path.split("/")
        if len(parts) > 3 and parts[1:3] in (["responses", "sessions"], ["responses", "artifacts"]):
            parts[3] = ":id"
        return f"route:{'/'.join(parts)}"
    return None


def event_from_record(record: Mapping[str, Any]) -> ProcessEvent | None:
    """Convert one structured log record without retaining payload-like fields."""
    activity = _safe_activity(record.get("message"))
    timestamp = _timestamp(record.get("timestamp"))
    if activity is None or timestamp is None:
        return None
    case_id = next(
        (
            f"{field}:{record[field]}"
            for field in _CASE_FIELDS
            if isinstance(record.get(field), str) and record[field]
        ),
        None,
    )
    if case_id is None:
        return None
    duration = record.get("duration_ms")
    duration_ms = float(duration) if isinstance(duration, (int, float)) and duration >= 0 else None
    status = record.get("http_status")
    failed = activity.endswith((".failed", ".rejected", ".invalid")) or (
        isinstance(status, int) and status >= 400
    )
    return ProcessEvent(
        case_id, activity, timestamp, duration_ms, failed, _feature(record, activity)
    )


def read_jsonl(path: str | Path) -> tuple[list[ProcessEvent], dict[str, int]]:
    """Read events from JSONL, counting malformed and safely ignored records."""
    events: list[ProcessEvent] = []
    counts = {"records": 0, "malformed": 0, "ignored": 0}
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            counts["records"] += 1
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                counts["malformed"] += 1
                continue
            if not isinstance(record, dict):
                counts["ignored"] += 1
                continue
            event = event_from_record(record)
            if event is None:
                counts["ignored"] += 1
            else:
                events.append(event)
    return events, counts


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def discover_process(events: Iterable[ProcessEvent]) -> dict[str, Any]:
    """Discover variants, transitions, performance, and actionable bottlenecks."""
    traces: dict[str, list[ProcessEvent]] = defaultdict(list)
    replay_count = 0
    for event in events:
        traces[event.case_id].append(event)
        replay_count += int(event.activity == "tool.execution.replayed")

    variants: Counter[tuple[str, ...]] = Counter()
    transitions: Counter[tuple[str, str]] = Counter()
    outcome_counts: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    durations: dict[str, list[float]] = defaultdict(list)
    incomplete = 0
    for trace in traces.values():
        trace.sort(key=lambda item: item.timestamp)
        activities = tuple(item.activity for item in trace)
        variants[activities] += 1
        transitions.update(zip(activities, activities[1:]))
        if activities and not activities[-1].endswith(_TERMINAL_EVENTS):
            incomplete += 1
        for event in trace:
            if event.activity.endswith(_TERMINAL_EVENTS):
                stage = event.feature or event.activity.rsplit(".", 1)[0]
                outcome_counts[stage] += 1
                failure_counts[stage] += int(event.failed)
                if event.duration_ms is not None:
                    durations[stage].append(event.duration_ms)

    activity_rows = []
    recommendations = []
    for activity, count in sorted(outcome_counts.items()):
        failures = failure_counts[activity]
        rate = failures / count
        p95 = _percentile(durations[activity], 0.95)
        activity_rows.append(
            {
                "activity": activity,
                "count": count,
                "failure_rate": round(rate, 4),
                "mean_duration_ms": (
                    round(sum(durations[activity]) / len(durations[activity]), 3)
                    if durations[activity]
                    else None
                ),
                "p95_duration_ms": p95,
            }
        )
        if count >= 3 and rate >= 0.05:
            recommendations.append(
                {
                    "priority": "high",
                    "activity": activity,
                    "signal": "failure_rate",
                    "value": round(rate, 4),
                    "action": "stabilize this step before reducing its latency",
                }
            )
        if count >= 3 and p95 is not None and p95 >= 2000:
            recommendations.append(
                {
                    "priority": "medium",
                    "activity": activity,
                    "signal": "p95_duration_ms",
                    "value": p95,
                    "action": "profile this step and remove avoidable waits or duplicate work",
                }
            )
    if traces and incomplete / len(traces) >= 0.05:
        recommendations.append(
            {
                "priority": "high",
                "activity": "process",
                "signal": "incomplete_case_rate",
                "value": round(incomplete / len(traces), 4),
                "action": "inspect abandoned cases and guarantee a terminal event on every path",
            }
        )
    if replay_count:
        recommendations.append(
            {
                "priority": "low",
                "activity": "tool.execution.replayed",
                "signal": "replay_count",
                "value": replay_count,
                "action": "retain exact-once replay and inspect upstream retry causes",
            }
        )

    priority = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda item: (priority[item["priority"]], -float(item["value"])))
    return {
        "case_count": len(traces),
        "event_count": sum(len(trace) for trace in traces.values()),
        "incomplete_case_count": incomplete,
        "variants": [
            {"activities": list(variant), "count": count}
            for variant, count in variants.most_common(20)
        ],
        "transitions": [
            {"from": source, "to": target, "count": count}
            for (source, target), count in transitions.most_common(50)
        ],
        "activities": activity_rows,
        "recommendations": recommendations,
    }


def analyze_jsonl(path: str | Path) -> dict[str, Any]:
    """Build a serializable process-mining report for one structured log."""
    events, input_counts = read_jsonl(path)
    return {"input": input_counts, **discover_process(events)}
