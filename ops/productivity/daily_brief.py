"""Deterministic scheduled daily-brief ranking and metadata-only feedback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from .models import ActionItem
from .service import within_quiet_hours


@dataclass
class DailyBriefJob:
    max_items: int = 10
    quiet_start: time = time(22)
    quiet_end: time = time(7)

    def run(self, proposals: list[ActionItem], *, now: datetime) -> list[ActionItem]:
        if within_quiet_hours(now, self.quiet_start, self.quiet_end):
            return []
        confirmed = [item for item in proposals if item.state == "confirmed"]
        return sorted(
            confirmed, key=lambda item: item.due_at or datetime.max.replace(tzinfo=now.tzinfo)
        )[: self.max_items]

    @staticmethod
    def dismissal_feedback(item_id: str, reason_code: str) -> dict[str, str]:
        return {"item_id": item_id, "reason_code": reason_code}
