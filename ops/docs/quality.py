"""Deterministic, content-safe validation for governed PJ documents."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GOVERNANCE_DIR = ROOT / "governance" / "document-quality"
STANDARD_PATH = GOVERNANCE_DIR / "standard.json"
MANIFEST_PATH = GOVERNANCE_DIR / "manifest.json"
REPORTS_DIR = ROOT / "reports" / "document-quality"
BLOCKING_SEVERITIES = {"blocker", "critical"}
_PLACEHOLDERS = ("[TBD", "[VERIFY CURRENT]", "{{", "TODO:")
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|authorization|password|secret)\s*[:=]\s*['\"]?(?!\*{4,}|placeholder|example)[A-Za-z0-9_./+\-=]{12,}"
)
_LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finding(rule_id: str, severity: str, location: str) -> dict[str, str]:
    # Deliberately record no excerpts, matched values, or document content.
    return {"rule_id": rule_id, "severity": severity, "location": location}


def validate_document(
    path: str | Path,
    *,
    profile: str,
    metadata_complete: bool = True,
    today: date | None = None,
) -> dict[str, Any]:
    """Return a stable, metadata-only report; never mutate the source."""
    source = Path(path).resolve()
    standard = _load(STANDARD_PATH)
    if profile not in standard["profiles"]:
        raise ValueError(f"unknown document profile: {profile}")
    raw = source.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    findings: list[dict[str, str]] = []
    if not metadata_complete:
        findings.append(_finding("DOC-META-001", "blocker", "manifest"))
    if source.suffix.lower() == ".md":
        headings = sum(1 for line in text.splitlines() if re.match(r"^#{1,6}\s+\S", line))
        if headings < standard["profiles"][profile]["required_headings"]:
            findings.append(_finding("DOC-STRUCT-001", "critical", "document"))
    for marker in _PLACEHOLDERS:
        if marker in text:
            findings.append(_finding("DOC-PLACEHOLDER-001", "blocker", "document"))
            break
    if _SECRET.search(text):
        findings.append(_finding("DOC-SEC-001", "blocker", "document"))
    base = source.parent
    for target in sorted(set(_LINK.findall(text))):
        clean = target.split("#", 1)[0].strip()
        if (
            clean
            and not re.match(r"(?:https?://|mailto:|#)", clean)
            and not (base / clean).exists()
        ):
            findings.append(_finding("DOC-LINK-001", "critical", "link"))
    dates = []
    for match in _DATE.finditer(text):
        try:
            dates.append(datetime.strptime(match.group(0), "%Y-%m-%d").date())
        except ValueError:
            continue
    if dates and profile in {"product", "security", "evidence"}:
        provenance_terms = ("source", "reference", "evidence", "http://", "https://")
        if not any(term in text.lower() for term in provenance_terms):
            findings.append(_finding("DOC-PROV-001", "warning", "document"))
    if dates:
        age = ((today or date.today()) - max(dates)).days
        if age > standard["profiles"][profile]["max_age_days"]:
            findings.append(_finding("DOC-FRESH-001", "warning", "document-date"))
    findings.sort(key=lambda item: (item["rule_id"], item["location"]))
    counts = {severity: 0 for severity in standard["severity_order"]}
    for finding in findings:
        counts[finding["severity"]] += 1
    try:
        source_name = str(source.relative_to(ROOT))
    except ValueError:
        source_name = source.name
    return {
        "schema_version": "1.0.0",
        "standard_version": standard["standard_version"],
        "source": source_name,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "profile": profile,
        "status": "fail" if any(f["severity"] in BLOCKING_SEVERITIES for f in findings) else "pass",
        "findings": findings,
        "counts": counts,
    }


def persist_report(report: dict[str, Any], directory: str | Path = REPORTS_DIR) -> Path:
    """Atomically persist canonical JSON named by the immutable source hash."""
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"{Path(report['source']).name}-{report['source_sha256']}.json"
    target = target_dir / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    temporary.replace(target)
    return target


def audit_manifest(*, persist: bool = False, today: date | None = None) -> dict[str, Any]:
    manifest = _load(MANIFEST_PATH)
    required = {"path", "owner", "class", "profile", "lifecycle", "disposition", "sha256"}
    reports = []
    for entry in sorted(manifest["documents"], key=lambda item: item["path"]):
        complete = required.issubset(entry) and all(entry[key] for key in required)
        report = validate_document(
            ROOT / entry["path"], profile=entry["profile"], metadata_complete=complete, today=today
        )
        if entry.get("sha256") != report["source_sha256"]:
            report["findings"].append(_finding("DOC-INTEGRITY-001", "blocker", "manifest-hash"))
            report["findings"].sort(key=lambda item: (item["rule_id"], item["location"]))
            report["counts"]["blocker"] += 1
            report["status"] = "fail"
        if persist:
            persist_report(report)
        reports.append(report)
    return {
        "documents": len(reports),
        "passing": sum(report["status"] == "pass" for report in reports),
        "failing": sum(report["status"] == "fail" for report in reports),
        "findings": sum(len(report["findings"]) for report in reports),
        "reports": reports,
    }


def validate_for_finalization(path: str | Path) -> dict[str, Any]:
    """Validate a DocOps source under the runtime profile and persist evidence."""
    report = validate_document(path, profile="runtime")
    persist_report(report)
    return report
