"""Fail-closed accounting for frame capture and paid visual analysis."""

from dataclasses import dataclass


class BudgetExceeded(RuntimeError):
    """The configured capture budget would be exceeded."""


@dataclass(slots=True)
class CaptureBudget:
    max_bytes: int = 8_000_000
    max_duration_ms: int = 120_000
    max_spend_micros: int = 500_000
    used_bytes: int = 0
    used_duration_ms: int = 0
    used_spend_micros: int = 0

    def charge(self, *, byte_size: int = 0, duration_ms: int = 0, spend_micros: int = 0) -> None:
        requested = (
            self.used_bytes + byte_size,
            self.used_duration_ms + duration_ms,
            self.used_spend_micros + spend_micros,
        )
        if any(value < 0 for value in (byte_size, duration_ms, spend_micros)):
            raise ValueError("budget charges cannot be negative")
        if requested[0] > self.max_bytes:
            raise BudgetExceeded("capture_byte_budget_exceeded")
        if requested[1] > self.max_duration_ms:
            raise BudgetExceeded("capture_duration_budget_exceeded")
        if requested[2] > self.max_spend_micros:
            raise BudgetExceeded("vision_spend_budget_exceeded")
        self.used_bytes, self.used_duration_ms, self.used_spend_micros = requested
