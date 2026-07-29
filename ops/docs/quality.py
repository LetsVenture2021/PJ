"""Metadata-only document quality measurement and governed scorecards.

This module intentionally accepts aggregates rather than validator findings.  It
is a privacy boundary: free-form validator output and document content have no
column (or catch-all JSON field) in the ledger.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Iterator, Mapping, Sequence

from . import service

SEVERITIES = ("blocker", "critical", "high", "medium", "low", "info")
FRESHNESS_STATES = ("current", "stale", "unknown", "not_applicable")
APPROVAL_STATES = ("pending", "approved", "rejected", "not_required")
WAIVER_EXPIRY_STATES = ("none", "active", "expiring", "expired")
STATUSES = ("passed", "failed", "error")
ZERO_TOLERANCE_INDICATORS = (
    "unintended_sensitive_data_exposure",
    "integrity_failures",
    "unresolved_blockers",
)
BASELINE_DAYS = 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | str | None) -> str:
    if value is None:
        value = _utc_now()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


@contextmanager
def _db(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path or service._DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS docops_quality_ledger (
            doc_id TEXT NOT NULL, version INTEGER NOT NULL, profile TEXT NOT NULL,
            source_hash TEXT NOT NULL, report_hash TEXT NOT NULL,
            validator_version TEXT NOT NULL, status TEXT NOT NULL,
            started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
            controls_executed INTEGER NOT NULL, controls_passed INTEGER NOT NULL,
            finding_counts_json TEXT NOT NULL, validation_duration_ms INTEGER NOT NULL,
            artifact_format TEXT NOT NULL, artifact_byte_size INTEGER NOT NULL,
            review_cycle_duration_ms INTEGER, revision_count INTEGER NOT NULL,
            source_freshness_state TEXT NOT NULL, approval_state TEXT NOT NULL,
            waiver_count INTEGER NOT NULL, waiver_expiry_state TEXT NOT NULL,
            suppression_count INTEGER NOT NULL, citation_controls_passed INTEGER NOT NULL,
            citation_controls_executed INTEGER NOT NULL,
            accessibility_controls_passed INTEGER NOT NULL,
            accessibility_controls_executed INTEGER NOT NULL,
            fidelity_controls_passed INTEGER NOT NULL,
            fidelity_controls_executed INTEGER NOT NULL,
            PRIMARY KEY (doc_id, version, profile, source_hash, report_hash,
                         validator_version, status, started_at, completed_at)
        );
        CREATE INDEX IF NOT EXISTS idx_docops_quality_completed
            ON docops_quality_ledger(completed_at);
        CREATE TABLE IF NOT EXISTS docops_quality_incidents (
            incident_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, version INTEGER NOT NULL,
            incident_type TEXT NOT NULL, occurred_at TEXT NOT NULL, resolved_at TEXT,
            count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS docops_quality_calibrations (
            calibration_id TEXT PRIMARY KEY, performed_at TEXT NOT NULL,
            seeded_defects INTEGER NOT NULL, seeded_defects_detected INTEGER NOT NULL,
            auto_passes_sampled INTEGER NOT NULL, human_review_defects INTEGER NOT NULL,
            validator_version TEXT NOT NULL
        );
        """
    )


def _bounded_count(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _validate_hash(name: str, value: str) -> str:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def record_quality_run(
    *,
    doc_id: str,
    version: int,
    profile: str,
    source_hash: str,
    report_hash: str,
    validator_version: str,
    status: str,
    started_at: datetime | str,
    completed_at: datetime | str,
    controls_executed: int,
    controls_passed: int,
    finding_counts: Mapping[str, Mapping[str, int]],
    validation_duration_ms: int,
    artifact_format: str,
    artifact_byte_size: int,
    review_cycle_duration_ms: int | None = None,
    revision_count: int = 0,
    source_freshness_state: str = "unknown",
    approval_state: str = "pending",
    waiver_count: int = 0,
    waiver_expiry_state: str = "none",
    suppression_count: int = 0,
    citation_controls: tuple[int, int] = (0, 0),
    accessibility_controls: tuple[int, int] = (0, 0),
    fidelity_controls: tuple[int, int] = (0, 0),
    db_path: Path | str | None = None,
) -> bool:
    """Record one idempotent aggregate run, rejecting non-aggregate findings."""
    if not doc_id or not profile or not validator_version:
        raise ValueError("document ID, profile, and validator version are required")
    _bounded_count("version", version)
    if version == 0:
        raise ValueError("version must be positive")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if source_freshness_state not in FRESHNESS_STATES:
        raise ValueError("invalid source freshness state")
    if approval_state not in APPROVAL_STATES:
        raise ValueError("invalid approval state")
    if waiver_expiry_state not in WAIVER_EXPIRY_STATES:
        raise ValueError("invalid waiver expiry state")
    executed = _bounded_count("controls_executed", controls_executed)
    passed = _bounded_count("controls_passed", controls_passed)
    waived = _bounded_count("waiver_count", waiver_count)
    suppressed = _bounded_count("suppression_count", suppression_count)
    if passed > executed or passed + waived + suppressed > executed:
        raise ValueError("passes, waivers, and suppressions must be disjoint executed controls")
    safe_findings: dict[str, dict[str, int]] = {}
    for severity, by_rule in finding_counts.items():
        if severity not in SEVERITIES or not isinstance(by_rule, Mapping):
            raise ValueError("findings must be counts grouped by severity and rule ID")
        safe_findings[severity] = {}
        for rule_id, count in by_rule.items():
            if not isinstance(rule_id, str) or not rule_id or len(rule_id) > 128:
                raise ValueError("rule IDs must be non-empty strings of at most 128 characters")
            if any(char.isspace() for char in rule_id):
                raise ValueError("rule IDs may not contain prose or whitespace")
            safe_findings[severity][rule_id] = _bounded_count("finding count", count)
    dimensions: list[int] = []
    for name, pair in (
        ("citation_controls", citation_controls),
        ("accessibility_controls", accessibility_controls),
        ("fidelity_controls", fidelity_controls),
    ):
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ValueError(f"{name} must be a (passed, executed) tuple")
        dimension_passed = _bounded_count(f"{name} passed", pair[0])
        dimension_executed = _bounded_count(f"{name} executed", pair[1])
        if dimension_passed > dimension_executed:
            raise ValueError(f"{name} passes cannot exceed executions")
        dimensions.extend((dimension_passed, dimension_executed))
    values = (
        doc_id,
        version,
        profile,
        _validate_hash("source_hash", source_hash),
        _validate_hash("report_hash", report_hash),
        validator_version,
        status,
        _iso(started_at),
        _iso(completed_at),
        executed,
        passed,
        json.dumps(safe_findings, sort_keys=True, separators=(",", ":")),
        _bounded_count("validation_duration_ms", validation_duration_ms),
        artifact_format,
        _bounded_count("artifact_byte_size", artifact_byte_size),
        None
        if review_cycle_duration_ms is None
        else _bounded_count("review_cycle_duration_ms", review_cycle_duration_ms),
        _bounded_count("revision_count", revision_count),
        source_freshness_state,
        approval_state,
        waived,
        waiver_expiry_state,
        suppressed,
        *dimensions,
    )
    with _db(db_path) as conn:
        before = conn.total_changes
        conn.execute(
            f"INSERT OR IGNORE INTO docops_quality_ledger VALUES ({','.join('?' for _ in values)})",
            values,
        )
        return conn.total_changes > before


def record_quality_incident(
    *,
    incident_id: str,
    doc_id: str,
    version: int,
    incident_type: str,
    count: int = 1,
    occurred_at: datetime | str | None = None,
    resolved_at: datetime | str | None = None,
    db_path: Path | str | None = None,
) -> bool:
    allowed = {
        "post_publication_correction",
        "defect_supersession",
        "broken_link",
        "sensitive_data_exposure",
        "audience_complaint",
        "rollback_withdrawal",
        "integrity_failure",
    }
    if incident_type not in allowed:
        raise ValueError("invalid aggregate incident type")
    with _db(db_path) as conn:
        before = conn.total_changes
        conn.execute(
            "INSERT OR IGNORE INTO docops_quality_incidents VALUES (?,?,?,?,?,?,?)",
            (
                incident_id,
                doc_id,
                version,
                incident_type,
                _iso(occurred_at),
                None if resolved_at is None else _iso(resolved_at),
                _bounded_count("count", count),
            ),
        )
        return conn.total_changes > before


def record_control_calibration(
    *,
    calibration_id: str,
    seeded_defects: int,
    seeded_defects_detected: int,
    auto_passes_sampled: int,
    human_review_defects: int,
    validator_version: str,
    performed_at: datetime | str | None = None,
    db_path: Path | str | None = None,
) -> bool:
    total = _bounded_count("seeded_defects", seeded_defects)
    detected = _bounded_count("seeded_defects_detected", seeded_defects_detected)
    sampled = _bounded_count("auto_passes_sampled", auto_passes_sampled)
    found = _bounded_count("human_review_defects", human_review_defects)
    if detected > total or found > sampled:
        raise ValueError("calibration subsets cannot exceed their samples")
    with _db(db_path) as conn:
        before = conn.total_changes
        conn.execute(
            "INSERT OR IGNORE INTO docops_quality_calibrations VALUES (?,?,?,?,?,?,?)",
            (
                calibration_id,
                _iso(performed_at),
                total,
                detected,
                sampled,
                found,
                validator_version,
            ),
        )
        return conn.total_changes > before


def _ratio(numerator: int, denominator: int) -> dict[str, float | int | None]:
    return {
        "value": None if denominator == 0 else numerator / denominator,
        "numerator": numerator,
        "denominator": denominator,
    }


def _window_metrics(rows: Sequence[sqlite3.Row], incident_rows: Sequence[sqlite3.Row]) -> dict:
    documents = {(row["doc_id"], row["version"]) for row in rows}
    pristine = 0
    blockers = stale = approved = 0
    review_times: list[int] = []
    citations = [0, 0]
    accessibility = [0, 0]
    fidelity = [0, 0]
    waivers = Counter()
    complete_runs = 0
    for row in rows:
        findings = json.loads(row["finding_counts_json"])
        blockers += sum(sum(findings.get(level, {}).values()) for level in ("blocker", "critical"))
        clean = not any(sum(counts.values()) for counts in findings.values())
        complete = row["controls_executed"] > 0
        complete_runs += int(complete)
        pristine += int(
            clean and complete and row["waiver_count"] == 0 and row["suppression_count"] == 0
        )
        stale += int(row["source_freshness_state"] == "stale")
        approved += int(row["approval_state"] == "approved")
        if row["review_cycle_duration_ms"] is not None:
            review_times.append(row["review_cycle_duration_ms"])
        for target, prefix in (
            (citations, "citation"),
            (accessibility, "accessibility"),
            (fidelity, "fidelity"),
        ):
            target[0] += row[f"{prefix}_controls_passed"]
            target[1] += row[f"{prefix}_controls_executed"]
        waivers[row["waiver_expiry_state"]] += row["waiver_count"]
    lagging = Counter()
    for row in incident_rows:
        lagging[row["incident_type"]] += row["count"]
    run_count = len(rows)
    return {
        "leading": {
            "first_pass_pristine_rate": _ratio(pristine, run_count),
            "blocker_density_per_document": _ratio(blockers, len(documents)),
            "citation_provenance_coverage": _ratio(*citations),
            "accessibility_pass_rate": _ratio(*accessibility),
            "stale_document_rate": _ratio(stale, run_count),
            "median_review_time_ms": {
                "value": median(review_times) if review_times else None,
                "numerator": len(review_times),
                "denominator": run_count,
            },
            "waiver_aging": dict(waivers),
            "cross_format_fidelity_rate": _ratio(*fidelity),
        },
        "lagging": {
            "post_publication_corrections": lagging["post_publication_correction"],
            "supersessions_caused_by_defects": lagging["defect_supersession"],
            "broken_link_incidents": lagging["broken_link"],
            "unintended_sensitive_data_exposure": lagging["sensitive_data_exposure"],
            "audience_complaints": lagging["audience_complaint"],
            "rollback_or_withdrawal_rate": _ratio(lagging["rollback_withdrawal"], len(documents)),
        },
        "coverage": {
            "documents": len(documents),
            "validation_runs": run_count,
            "runs_with_controls_executed": complete_runs,
            "validator_execution_rate": _ratio(complete_runs, run_count),
            "approved_runs": approved,
        },
    }


def calculate_scorecard(
    *, period_end: datetime | str | None = None, db_path: Path | str | None = None
) -> dict:
    """Calculate monthly indicators plus a prior rolling 30-day baseline."""
    end = datetime.fromisoformat(_iso(period_end))
    start = end - timedelta(days=BASELINE_DAYS)
    baseline_start = start - timedelta(days=BASELINE_DAYS)
    with _db(db_path) as conn:

        def fetch(table: str, column: str, low: datetime, high: datetime) -> list[sqlite3.Row]:
            return conn.execute(
                f"SELECT * FROM {table} WHERE {column}>=? AND {column}<?", (_iso(low), _iso(high))
            ).fetchall()

        current = _window_metrics(
            fetch("docops_quality_ledger", "completed_at", start, end),
            fetch("docops_quality_incidents", "occurred_at", start, end),
        )
        baseline = _window_metrics(
            fetch("docops_quality_ledger", "completed_at", baseline_start, start),
            fetch("docops_quality_incidents", "occurred_at", baseline_start, start),
        )
        first = conn.execute("SELECT MIN(completed_at) FROM docops_quality_ledger").fetchone()[0]
        calibrations = [
            dict(row)
            for row in conn.execute(
                "SELECT performed_at, seeded_defects, seeded_defects_detected, "
                "auto_passes_sampled, human_review_defects, validator_version "
                "FROM docops_quality_calibrations WHERE performed_at>=? AND performed_at<? "
                "ORDER BY performed_at",
                (_iso(start - timedelta(days=60)), _iso(end)),
            ).fetchall()
        ]
    baseline_complete = bool(
        first and end - datetime.fromisoformat(first) >= timedelta(days=BASELINE_DAYS)
    )
    return {
        "period": {"start": _iso(start), "end": _iso(end)},
        "indicators": current,
        "rolling_baseline": baseline,
        "quarterly_control_calibration": {
            "runs": calibrations,
            "coverage_note": (
                "Each quarter requires seeded-defect fixtures and a sampled human review "
                "of documents that passed automatically. Empty runs indicate missing coverage."
            ),
        },
        "baseline_complete": baseline_complete,
        "target_policy": {
            "aggressive_targets_deferred": not baseline_complete,
            "baseline_days": BASELINE_DAYS,
            "zero_tolerance_from_day_one": list(ZERO_TOLERANCE_INDICATORS),
        },
        "confidence_notes": [
            "Percentages include explicit numerators and denominators.",
            "A null value means the denominator was zero; it does not mean 0%.",
            "Finding trends are interpretable only with validator execution coverage.",
            "Waived and suppressed controls are excluded from clean passes.",
        ],
    }


def regression_alerts(scorecard: Mapping, absolute_thresholds: Mapping[str, float]) -> list[dict]:
    """Alert only when a metric breaches its threshold or worsens vs baseline.

    Finding-based improvements are suppressed when execution coverage declined.
    Metric paths use ``leading.<name>`` or ``lagging.<name>``.
    """
    alerts = []
    current = scorecard["indicators"]
    baseline = scorecard["rolling_baseline"]
    current_coverage = current["coverage"]["validator_execution_rate"]["value"] or 0
    baseline_coverage = baseline["coverage"]["validator_execution_rate"]["value"] or 0
    if current_coverage < baseline_coverage:
        alerts.append(
            {"metric": "coverage.validator_execution_rate", "reason": "rolling_regression"}
        )
    for path, threshold in absolute_thresholds.items():
        group, name = path.split(".", 1)
        raw = current[group][name]
        old_raw = baseline[group][name]
        value = raw.get("value") if isinstance(raw, Mapping) else raw
        old = old_raw.get("value") if isinstance(old_raw, Mapping) else old_raw
        if value is None:
            continue
        reasons = []
        higher_is_bad = name not in {
            "first_pass_pristine_rate",
            "citation_provenance_coverage",
            "accessibility_pass_rate",
            "cross_format_fidelity_rate",
        }
        if (value > threshold) if higher_is_bad else (value < threshold):
            reasons.append("absolute_threshold")
        if old is not None and ((value > old) if higher_is_bad else (value < old)):
            reasons.append("rolling_regression")
        if reasons:
            alerts.append({"metric": path, "value": value, "reasons": reasons})
    return alerts


def generate_monthly_scorecard(
    *,
    period_end: datetime | str | None = None,
    db_path: Path | str | None = None,
    finalize: bool = True,
) -> dict:
    """Create the scorecard as a versioned, hash-sealed DocOps audit document."""
    card = calculate_scorecard(period_end=period_end, db_path=db_path)
    service.create_doc_template(
        "quality_scorecard",
        json.dumps(
            [
                "Period and Governance",
                "Leading Indicators",
                "Lagging Indicators",
                "Coverage and Confidence",
                "Control Calibration",
            ]
        ),
        description="Governed monthly aggregate document-quality scorecard",
    )

    def rendered(value: object) -> str:
        return "```json\n" + json.dumps(value, indent=2, sort_keys=True) + "\n```"

    sections = {
        "Period and Governance": rendered(
            {"period": card["period"], "target_policy": card["target_policy"]}
        ),
        "Leading Indicators": rendered(card["indicators"]["leading"]),
        "Lagging Indicators": rendered(card["indicators"]["lagging"]),
        "Coverage and Confidence": rendered(
            {"coverage": card["indicators"]["coverage"], "notes": card["confidence_notes"]}
        ),
        "Control Calibration": rendered(card["quarterly_control_calibration"]),
    }
    result = service.draft_document(
        "quality_scorecard",
        "Monthly Document Quality Scorecard",
        json.dumps(sections),
        tags="audit,quality,scorecard",
        finalize=finalize,
    )
    result["scorecard"] = card
    return result
