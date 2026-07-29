"""Bounded, deduplicated automation notifications."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from ops.jobs.models import QuietHours


@dataclass(frozen=True)
class NotificationDecision:
    send: bool
    reason: str


class NotificationPolicy:
    def __init__(self, *, daily_cap: int, quiet_hours: QuietHours | None = None) -> None:
        self.daily_cap = daily_cap
        self.quiet_hours = quiet_hours
        self._sent: dict[str, set[str]] = defaultdict(set)
        self._dismissed: set[str] = set()

    def decide(self, notification_id: str, *, category: str, now: datetime) -> NotificationDecision:
        day = now.date().isoformat()
        if notification_id in self._dismissed:
            return NotificationDecision(False, "dismissed")
        if notification_id in self._sent[day]:
            return NotificationDecision(False, "duplicate")
        if category not in {"owner_decision", "failure"}:
            return NotificationDecision(False, "no_escalation")
        if self.quiet_hours and self.quiet_hours.contains(now):
            return NotificationDecision(False, "quiet_hours")
        if len(self._sent[day]) >= self.daily_cap:
            return NotificationDecision(False, "daily_cap")
        self._sent[day].add(notification_id)
        return NotificationDecision(True, "send")

    def dismiss(self, notification_id: str) -> None:
        self._dismissed.add(notification_id)
