"""Deterministic, metadata-safe quality gates for governed documents.

The validators deliberately operate locally: they do not call providers, fetch
URLs, or log document content. Reports contain rule identifiers and locations,
never matched text, so callers can safely retain them as governance evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path


QUALITY_SCHEMA_VERSION = "1.0"
VALIDATOR_VERSION = "1.0.0"


class Severity(StrEnum):
    BLOCKER = "blocker"
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    ADVISORY = "advisory"


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    message: str
    line: int | None = None
    waiver_eligible: bool = False

    def public(self) -> dict:
        value = asdict(self)
        value["severity"] = self.severity.value
        return value


_DRAFT_RESIDUE = (
    re.compile(r"\[(?:TBD|VERIFY CURRENT)(?:[^\]]*)\]", re.I),
    re.compile(r"\b(?:TODO|FIXME|XXX)\s*:", re.I),
    re.compile(r"\{\{[^{}]+\}\}"),
    re.compile(r"\blorem ipsum\b", re.I),
)
_SENSITIVE = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:api[_-]?key|password|secret)\s*[:=]\s*[^*\s][^\s]{7,}", re.I),
)
_EMPTY_LINK = re.compile(r"\[[^\]]*\]\(\s*\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _finding(pattern: re.Pattern, content: str, **kwargs) -> Finding | None:
    match = pattern.search(content)
    if not match:
        return None
    return Finding(line=_line_number(content, match.start()), **kwargs)


def validate_content(content: str, *, profile: str = "governed") -> dict:
    """Validate Markdown content and return a deterministic public report."""
    findings: list[Finding] = []
    if not content.strip():
        findings.append(Finding("DOC-STRUCT-001", Severity.BLOCKER, "Document is empty."))

    for pattern in _DRAFT_RESIDUE:
        finding = _finding(
            pattern,
            content,
            rule_id="DOC-COMPLETE-001",
            severity=Severity.BLOCKER,
            message="Unresolved drafting residue is present.",
        )
        if finding:
            findings.append(finding)

    for pattern in _SENSITIVE:
        finding = _finding(
            pattern,
            content,
            rule_id="DOC-SEC-001",
            severity=Severity.CRITICAL,
            message="Potential credential or private-key material is present.",
        )
        if finding:
            findings.append(finding)

    empty_link = _finding(
        _EMPTY_LINK,
        content,
        rule_id="DOC-LINK-001",
        severity=Severity.MAJOR,
        message="A Markdown link has no destination.",
        waiver_eligible=True,
    )
    if empty_link:
        findings.append(empty_link)

    headings: list[tuple[int, str, int]] = []
    for number, line in enumerate(content.splitlines(), 1):
        match = _HEADING.match(line)
        if match:
            headings.append((len(match.group(1)), match.group(2).casefold(), number))
    if not headings or headings[0][0] != 1:
        findings.append(
            Finding(
                "DOC-STRUCT-002", Severity.MAJOR, "Document must begin with one level-one title."
            )
        )
    if sum(level == 1 for level, _, _ in headings) > 1:
        findings.append(
            Finding("DOC-STRUCT-003", Severity.MAJOR, "Document has multiple level-one titles.")
        )
    seen: set[tuple[int, str]] = set()
    previous = 0
    for level, title, line in headings:
        if previous and level > previous + 1:
            findings.append(
                Finding("DOC-A11Y-001", Severity.MAJOR, "Heading levels are skipped.", line, True)
            )
        key = (level, title)
        if key in seen:
            findings.append(
                Finding("DOC-STRUCT-004", Severity.MINOR, "A heading is duplicated.", line, True)
            )
        seen.add(key)
        previous = level

    findings.sort(key=lambda item: (item.line or 0, item.rule_id, item.message))
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    counts = {severity.value: 0 for severity in Severity}
    for finding in findings:
        counts[finding.severity.value] += 1
    passing = not any(
        finding.severity in {Severity.BLOCKER, Severity.CRITICAL, Severity.MAJOR}
        for finding in findings
    )
    report = {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "profile": profile,
        "source_sha256": digest,
        "status": "pass" if passing else "fail",
        "counts": counts,
        "findings": [finding.public() for finding in findings],
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    report["report_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return report


def validate_path(path: Path, *, profile: str = "governed") -> dict:
    report = validate_content(path.read_text(encoding="utf-8"), profile=profile)
    report["validated_at"] = datetime.now(timezone.utc).isoformat()
    return report
