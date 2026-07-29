"""Offline quality orchestration, manifest validation, and lifecycle contracts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .model import Finding, QualityConfig, QualityReport
from .validators import validate_text


class QualityGateError(ValueError):
    """Raised when a stale, mismatched, or failing report is used."""


def _sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _config_sha(config: QualityConfig) -> str:
    canonical = json.dumps(config.normalized(), sort_keys=True, separators=(",", ":"))
    return _sha(canonical)


def _active_waivers(
    findings: list[Finding], waivers: list[dict[str, Any]], today: date
) -> list[Finding]:
    active: set[str] = set()
    for waiver in waivers:
        try:
            expires = date.fromisoformat(str(waiver["expires"]))
            rule_id = str(waiver["rule_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if expires >= today:
            active.add(rule_id)
    return [replace(item, waived=item.rule_id in active) for item in findings]


def validate_document(
    source: str | Path,
    *,
    config: QualityConfig | None = None,
    waivers: list[dict[str, Any]] | None = None,
    today: date | None = None,
) -> QualityReport:
    """Validate UTF-8 Markdown deterministically; malformed bytes become a blocker."""
    config = config or QualityConfig()
    path = Path(source)
    raw = path.read_bytes()
    findings: list[Finding]
    try:
        text = raw.decode("utf-8", errors="strict")
        findings = list(validate_text(text, config))
    except UnicodeDecodeError:
        findings = [
            Finding("DOC-INPUT-001", "blocker", "Source is not valid UTF-8; content omitted.", 0, 0)
        ]
    findings = _active_waivers(findings, waivers or [], today or date.today())
    return QualityReport(str(path), _sha(raw), _config_sha(config), findings)


def assert_report_current(
    report: QualityReport | dict[str, Any],
    source: str | Path,
    *,
    config: QualityConfig | None = None,
) -> None:
    """Refuse finalization when a report is stale, mismatched, or failing."""
    payload = report.as_dict() if isinstance(report, QualityReport) else report
    raw = Path(source).read_bytes()
    config = config or QualityConfig()
    if payload.get("source_sha256") != _sha(raw):
        raise QualityGateError("quality report is stale or belongs to different content")
    if payload.get("config_sha256") != _config_sha(config):
        raise QualityGateError("quality report configuration does not match")
    if payload.get("status") != "pass":
        raise QualityGateError("quality report contains unwaived major-or-higher findings")


def validate_manifest(
    manifest_path: str | Path,
    *,
    changed: set[str] | None = None,
    scheduled: bool = False,
    today: date | None = None,
) -> list[QualityReport]:
    """Validate a manifest, optionally selecting changed documents and dependents."""
    manifest_path = Path(manifest_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise QualityGateError("manifest must contain a 'documents' array")
    root = (
        manifest_path.parent.parent
        if manifest_path.parent.name == "documents"
        else manifest_path.parent
    )
    selected = set(changed or ())
    if selected:
        progressed = True
        while progressed:
            progressed = False
            for entry in data["documents"]:
                source = str(entry.get("source", ""))
                dependencies = {str(item) for item in entry.get("depends_on", [])}
                if source in selected or dependencies & selected:
                    if source not in selected:
                        selected.add(source)
                        progressed = True
    current = today or date.today()
    reports: list[QualityReport] = []
    for entry in sorted(data["documents"], key=lambda item: str(item.get("source", ""))):
        source = str(entry.get("source", ""))
        if selected and source not in selected:
            continue
        report = validate_document(root / source, waivers=entry.get("waivers", []), today=current)
        if scheduled:
            extra = list(report.findings)
            for field, rule in (("fresh_until", "DOC-FRESH-001"), ("review_by", "DOC-REVIEW-001")):
                try:
                    deadline = date.fromisoformat(str(entry[field]))
                except (KeyError, ValueError):
                    continue
                if deadline < current:
                    extra.append(Finding(rule, "major", f"{field} deadline has passed.", 0, 0))
            report.findings = extra
        reports.append(report)
    return reports


def _ensure_approval_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS document_quality_approvals (
        source TEXT PRIMARY KEY, report_digest TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        approved_at TEXT NOT NULL)"""
    )


def approve_report(connection: sqlite3.Connection, report: QualityReport) -> None:
    """Persist approval bound to both report and exact source content."""
    if report.failed:
        raise QualityGateError("a failing report cannot be approved")
    _ensure_approval_table(connection)
    connection.execute(
        "INSERT OR REPLACE INTO document_quality_approvals VALUES (?,?,?,?)",
        (
            report.source,
            report.digest,
            report.source_sha256,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()


def report_is_approved(connection: sqlite3.Connection, report: QualityReport) -> bool:
    _ensure_approval_table(connection)
    row = connection.execute(
        "SELECT report_digest, source_sha256 FROM document_quality_approvals WHERE source=?",
        (report.source,),
    ).fetchone()
    return bool(row and row == (report.digest, report.source_sha256) and not report.failed)
