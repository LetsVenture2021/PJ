"""Metadata-only automation receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RunReceipt:
    run_id: str
    workflow_id: str
    started_at: datetime
    status: str
    reason_code: str | None = None
    approval_status: str = "not_required"
    cost: float = 0.0
    outcome_link: str | None = None

    def __post_init__(self) -> None:
        if self.cost < 0 or self.status not in {
            "pending",
            "running",
            "succeeded",
            "failed",
            "skipped",
            "cancelled",
        }:
            raise ValueError("invalid metadata receipt")
        if self.status == "skipped" and not self.reason_code:
            raise ValueError("skipped receipts require a reason code")
