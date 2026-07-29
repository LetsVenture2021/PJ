"""Metadata-only cost accounting; prompts and provider results are never accepted."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class CostRecord:
    session_id: str
    project_id: str
    job_id: str
    provider_operation: str
    estimate: float
    reservation: float
    actual: float | None
    currency: str
    source: Mapping[str, str]


class CostLedger:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cost_ledger (
                session_id TEXT NOT NULL, project_id TEXT NOT NULL, job_id TEXT NOT NULL,
                provider_operation TEXT NOT NULL, estimate REAL NOT NULL,
                reservation REAL NOT NULL, actual REAL, currency TEXT NOT NULL,
                source_json TEXT NOT NULL, PRIMARY KEY(session_id,project_id,job_id,provider_operation))""")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def reserve(self, record: CostRecord, *, limit: float | None = None) -> None:
        import json

        if record.estimate < 0 or record.reservation < 0:
            raise ValueError("costs cannot be negative")
        if (
            limit is not None
            and self.total_reserved(record.project_id) + record.reservation > limit
        ):
            raise ValueError("cost_exhausted")
        allowed_source = {str(key): str(value) for key, value in record.source.items()}
        with self._connect() as db:
            db.execute(
                "INSERT INTO cost_ledger VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    record.session_id,
                    record.project_id,
                    record.job_id,
                    record.provider_operation,
                    record.estimate,
                    record.reservation,
                    record.actual,
                    record.currency.upper(),
                    json.dumps(allowed_source, sort_keys=True),
                ),
            )

    def settle(
        self, session_id: str, project_id: str, job_id: str, provider_operation: str, actual: float
    ) -> None:
        if actual < 0:
            raise ValueError("actual cost cannot be negative")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE cost_ledger SET actual=? WHERE session_id=? AND project_id=? AND job_id=? AND provider_operation=?",
                (actual, session_id, project_id, job_id, provider_operation),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)

    def total_reserved(self, project_id: str) -> float:
        with self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(reservation),0) FROM cost_ledger WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return float(row[0])

    @staticmethod
    def estimate_card(operation: str, amount: float, currency: str = "USD") -> dict[str, Any]:
        return {
            "kind": "cost_estimate",
            "operation": operation,
            "amount": amount,
            "currency": currency,
            "approval_required": True,
        }

    @staticmethod
    def outcome_card(operation: str, actual: float, currency: str = "USD") -> dict[str, Any]:
        return {
            "kind": "outcome",
            "operation": operation,
            "actual_cost": actual,
            "currency": currency,
        }
